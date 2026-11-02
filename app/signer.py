# -*- coding: utf-8 -*-
"""M2.3 manual one-key sign engine.

Flow per PRD v1.0 (single attempt, NO retry):
    1. get_self_info          -> account validity gate
    1.5 client.sign_growth()  -> 成长等级签到（旁路，失败只记 warning）
    2. client.sign_forums()   -> Baidu web one-key sign (msign)
    3. get_self_follow_forums -> enumerate followed forums, page by page
    4. sign_forum(fname)      -> sign every forum still not signed
Failure categories: 已签到 / 未关注 / 吧不存在 / 账号失效 / 需要验证码 /
网络异常 / 风控限制 / 未知错误.
Manual runs never raise system notifications and never set the red dot.

v0.5.0 changes:
    * 逐吧补签改成**有上限的并发**（SIGN_CONCURRENCY），单次请求之间加几十
      毫秒抖动。串行时几百个吧要几分钟，这是签到慢的主因；并发仍然是单次
      尝试、不重试，不改变 PRD 里「失败就记失败」的语义。
    * 错误码结构化：msign / 每个吧失败都带 ``code``、``reason``（见
      ``tieba_codes``）和完整 ``detail``，并写入 ``sign.log``。日志只落
      ``ANDROID_PRIVATE``，绝不落完整 BDUSS（``redact`` 双重脱敏）。
    * ``warnings`` 与 ``failures`` 分开：msign 失败不再算整体失败，后面
      逐吧补签成功就是成功（v0.4.0 曾因此误报「自动签到失败」）。

All state lives in a module-level dict guarded by _lock; the HTTP layer polls
GET /api/sign/state. The aiotieba coroutine runs on tieba_api's event loop
via run_coroutine_threadsafe from a worker thread.

Data files (both under ANDROID_PRIVATE, neither contains a BDUSS):
    sign.log                 追加式详细日志（滚动保留 1 份）
    tiebasign_sign_last.json 最近一次签到报告（供 /sign/report 导出）
"""

import asyncio
import json
import os
import random
import threading
import time

import redact
import tieba_api
import tieba_codes

_lock = threading.RLock()
_run_counter = 0
_state = None

NETWORK_TYPE_HINTS = (
    "ClientConnector", "ClientOSError", "ClientConnection", "ConnectionError",
    "TimeoutError", "Timeout", "SSLCertVerificationError", "SSLError",
    "CertificationError", "SocketError", "Socket", "OSError",
)

MAX_FORUM_PAGES = 25  # 25 * 200 = 5000 forums, far beyond realistic usage

#: 同时最多几个 sign_forum 请求。取 4 是折中：串行几百个吧要几分钟；再高
#: 会让短时间内的请求峰值更像脚本。每条请求前随机停 30~150ms 打散节奏。
SIGN_CONCURRENCY = 4
SIGN_JITTER = (0.03, 0.15)

_LOG_FILENAME = "sign.log"
_REPORT_FILENAME = "tiebasign_sign_last.json"
_LOG_MAX_BYTES = 512 * 1024
_REPORT_FAILURES_MAX = 200
_REPORT_WARNINGS_MAX = 50


def _now_ms():
    return int(time.time() * 1000)


def _data_path(filename):
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, filename)


def _log(msg, secret=None):
    """追加一行 sign.log；任何异常都不能影响签到本身。"""
    try:
        path = _data_path(_LOG_FILENAME)
        try:
            if os.path.getsize(path) > _LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        line = "[{}] {}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"),
            redact.scrub(str(msg), secret))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001 - 日志失败绝不能打断签到
        pass


def _new_state(run_id, account):
    return {
        "run_id": run_id,
        "status": "starting",   # starting|running|done|error
        "step": "准备中",
        "account_id": account["id"],
        "account_name": account["name"] or account["mask"],
        "account_mask": account["mask"],
        "started_at": _now_ms(),
        "finished_at": None,
        "msign": None,          # {"status","category","code","reason","detail"}
        "growth": None,         # 成长等级签到（4.7.1 的 sign_growth）结果
        "forum_total": 0,       # forums that still needed signing
        "forum_checked": 0,
        "forum_success": 0,
        "forum_failed": 0,
        "failures": [],         # [{forum,category,code,reason,detail}]
        "warnings": [],         # non-fatal: msign / 后续步骤的旁路问题
        "error": None,          # run-level fatal error
    }


def _update(**kwargs):
    with _lock:
        if _state is None:
            return
        _state.update(kwargs)


def _append_failure(item):
    with _lock:
        if _state is not None:
            _state["failures"].append(item)
            _state["forum_failed"] += 1


def get_state():
    with _lock:
        if _state is None:
            return {"status": "idle"}
        # shallow copy so the HTTP thread never sees a half-written list
        snap = dict(_state)
        if snap.get("mode") == "multi":
            accounts = []
            for sub in _state["accounts"]:
                acct_snap = dict(sub)
                acct_snap["failures"] = list(sub["failures"])
                acct_snap["warnings"] = list(sub.get("warnings") or [])
                accounts.append(acct_snap)
            snap["accounts"] = accounts
        else:
            snap["failures"] = list(_state["failures"])
            snap["warnings"] = list(_state["warnings"])
        return snap


def start(account, bduss):
    """Launch a worker for the given account. Returns (run_id, err|None)."""
    global _run_counter, _state
    with _lock:
        if _state is not None and _state["status"] in ("starting", "running"):
            return None, "已有签到任务正在进行，请等待完成"
        _run_counter += 1
        run_id = "r{:d}".format(_run_counter)
        state = _new_state(run_id, account)
        _state = state

    def _worker():
        loop = tieba_api._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(
            _sign_async(state, bduss), loop)
        try:
            future.result(timeout=900)  # 15 min ceiling
        except asyncio.TimeoutError:
            _update(status="error", step="签到超时",
                    error={"category": "网络异常", "code": None,
                           "reason": None,
                           "detail": "签到超过 15 分钟仍未完成，已停止等待"})
        except Exception as exc:  # noqa: BLE001
            _update(status="error", step="签到异常",
                    error={"category": _classify_exception(exc), "code": None,
                           "reason": None,
                           "detail": "worker error: {}: {}".format(
                               type(exc).__name__, exc)})
        finally:
            _persist_report(state)

    threading.Thread(target=_worker, name="tieba-sign-worker",
                     daemon=True).start()
    return run_id, None


def _classify_server_error(code, msg):
    """优先按错误码表分类；没有码时退回中文关键词；都没有就是「未知错误」。"""
    category = tieba_codes.category_for_code(code)
    if category:
        return category
    msg_l = (msg or "").lower()

    if any(k in msg_l for k in ("已经签过", "已签到", "重复签到", "签到过快")):
        return tieba_codes.CAT_DONE
    # server told us the credential is bad / session expired
    if any(k in msg_l for k in ("bduss", "登录已失效", "登录失效",
                                "用户不存在", "未登录", "账号失效",
                                "帐号失效", "失效", "过期", "封禁",
                                "黑名单", "实名")):
        return tieba_codes.CAT_ACCOUNT
    if any(k in msg_l for k in ("吧不存在", "贴吧不存在", "该吧不存在")):
        return tieba_codes.CAT_NO_FORUM
    # forum relationship problems
    if any(k in msg_l for k in ("未关注", "关注该吧", "先关注",
                                "取消关注", "非本吧会员", "会员才能")):
        return tieba_codes.CAT_UNFOLLOWED
    if "验证码" in msg_l:
        return tieba_codes.CAT_CAPTCHA
    if any(k in msg_l for k in ("风控", "太快", "目录出问题", "服务忙",
                                "tbs")):
        return tieba_codes.CAT_RISK
    # 空 errmsg 或完全没见过的文案：明确写「未知」，不伪装成「风控」。
    return tieba_codes.CAT_UNKNOWN


def _err_detail(err):
    """返回 (code, detail, reason)。detail 一定带错误码解释（若 code 存在）。"""
    code = tieba_codes.parse_code(getattr(err, "code", None))
    msg = getattr(err, "msg", None)
    if msg is None:
        msg = str(err)
    msg = str(msg).strip()
    msg = redact.scrub(msg)

    reason = tieba_codes.reason(code)
    if code is None:
        return None, (msg or "未知错误（异常没有错误码）"), None
    if reason:
        if msg:
            detail = "{}（{}）".format(msg, tieba_codes.explain(code))
        else:
            detail = "百度未返回错误原因（{}）".format(tieba_codes.explain(code))
    elif msg:
        detail = "{}（错误码 {}，未收录）".format(msg, code)
    else:
        detail = "百度未返回错误原因（错误码 {}，未收录）".format(code)
    return code, detail, reason


def _classify_exception(exc):
    name = type(exc).__name__
    text = "{} {}".format(name, exc)
    mro_text = " ".join(c.__name__ for c in type(exc).__mro__)
    if any(k in mro_text or k in text for k in NETWORK_TYPE_HINTS):
        return tieba_codes.CAT_NETWORK
    code = getattr(exc, "code", None)
    msg = getattr(exc, "msg", None)
    if code is not None:
        return _classify_server_error(code, str(msg or exc))
    return tieba_codes.CAT_UNKNOWN


def _failure_item(fname, err, secret=None):
    code, detail, reason = _err_detail(err)
    category = _classify_server_error(code, str(getattr(err, "msg", None)
                                               or err))
    return {
        "forum": redact.scrub(str(fname), secret),
        "category": category,
        "code": code,
        "reason": reason,
        "detail": redact.scrub(detail, secret),
    }


def _record_forum_failure(state, fname, err, secret=None):
    item = _failure_item(fname, err, secret)
    _append_failure_in(state, item)
    _log("run={} forum={} code={} category={} reason={} detail={}".format(
        state.get("run_id"), item["forum"], item["code"],
        item["category"], item["reason"], item["detail"]), secret)


def _warn(state, kind, err, secret=None):
    item = _failure_item(kind, err, secret)
    state["warnings"].append(item)


async def _get_self_info(client):
    return await client.get_self_info()


async def _sign_growth_step(client, state, secret=None):
    """成长等级签到（aiotieba 4.7.1 的 sign_growth）。

    它不是吧群签到：4.7.1 / 4.8.4 都没有吧群签到接口。这里把它作为签到流程的
    一个**旁路步骤**接入：成功加分，失败只记 warning，绝不拖累吧签到结果。
    """
    _update_in(state, step="成长等级签到")
    try:
        resp = await client.sign_growth()
    except Exception as exc:  # noqa: BLE001 - 旁路步骤失败不影响主流程
        item = _failure_item("成长等级签到", exc, secret)
        state["growth"] = {"status": "error", "category": item["category"],
                           "code": item["code"], "reason": item["reason"],
                           "detail": item["detail"]}
        state["warnings"].append(item)
        _log("growth failed code={} detail={}".format(
            item["code"], item["detail"]), secret)
        return

    err = getattr(resp, "err", None)
    if err is None:
        state["growth"] = {"status": "success", "category": None,
                           "code": None, "reason": None,
                           "detail": "成长等级签到成功"}
        _log("growth success", secret)
        return

    item = _failure_item("成长等级签到", err, secret)
    # 这几个码表示「今天已经签过」，对用户来说是完成，不是错误。
    if item["code"] in (3, 160002, 340010, 1101, 1102):
        state["growth"] = {"status": "done", "category": item["category"],
                           "code": item["code"], "reason": item["reason"],
                           "detail": "今天已经签到过了"}
    else:
        state["growth"] = {"status": "error", "category": item["category"],
                           "code": item["code"], "reason": item["reason"],
                           "detail": item["detail"]}
        state["warnings"].append(item)
    _log("growth code={} status={} detail={}".format(
        item["code"], state["growth"]["status"], item["detail"]), secret)


def _update_in(state, **kwargs):
    state.update(kwargs)


def _append_failure_in(state, item):
    state["failures"].append(item)
    state["forum_failed"] += 1


async def _sign_forums(client, state, forums, secret=None):
    """有上限并发地逐吧补签；每个吧仍然只尝试一次。"""
    total = len(forums)
    concurrency = max(1, int(SIGN_CONCURRENCY))
    sem = asyncio.Semaphore(concurrency)
    jitter_lo, jitter_hi = SIGN_JITTER

    async def _one(forum):
        fname = str(getattr(forum, "fname", "") or "")
        if not fname:
            _record_forum_failure(state, "<无名吧>",
                                  RuntimeError("forum name missing"), secret)
        else:
            try:
                async with sem:
                    delay = random.uniform(jitter_lo, jitter_hi)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    resp = await client.sign_forum(fname)
                rerr = getattr(resp, "err", None)
                if rerr is None:
                    state["forum_success"] += 1
                else:
                    _record_forum_failure(state, fname, rerr, secret)
            except Exception as exc:  # noqa: BLE001 - 单个吧失败不拖垮整轮
                _record_forum_failure(state, fname, exc, secret)
        state["forum_checked"] += 1
        checked = state["forum_checked"]
        if checked % 10 == 0 or checked == total:
            _update_in(state, step="逐吧补签 {}/{}".format(checked, total))

    await asyncio.gather(*(_one(forum) for forum in forums))


async def _sign_async(state, bduss):
    import aiotieba

    secret = bduss if isinstance(bduss, str) else None
    try:
        _update_in(state, status="running", step="连接百度并验证账号")
        _log("run={} account_id={} mask={} start".format(
            state.get("run_id"), state.get("account_id"),
            state.get("account_mask")), secret)
        async with aiotieba.Client(bduss) as client:
            # ---- 1. account validity gate --------------------------------
            user = await client.get_self_info()
            uerr = getattr(user, "err", None)
            if uerr is not None:
                item = _failure_item("账号验证", uerr, secret)
                detail = item["detail"]
                _log("account gate failed code={} category={} detail={}".format(
                    item["code"], item["category"], detail), secret)
                _update_in(state, status="error", step="账号验证失败",
                           finished_at=_now_ms(),
                           error={"category": item["category"],
                                  "code": item["code"],
                                  "reason": item["reason"],
                                  "detail": detail})
                return

            # ---- 1.5 成长等级签到（旁路，失败不算失败） -----------------
            await _sign_growth_step(client, state, secret)

            # ---- 2. Baidu web one-key sign (msign) -----------------------
            _update_in(state, step="网页一键签到")
            _log("run={} msign start".format(state.get("run_id")), secret)
            msign = await client.sign_forums()
            merr = getattr(msign, "err", None)
            if merr is None:
                _update_in(state, msign={"status": "success",
                                         "category": None,
                                         "code": None,
                                         "reason": None,
                                         "detail": "网页一键签到成功"})
            else:
                code, detail, reason = _err_detail(merr)
                category = _classify_server_error(
                    code, str(getattr(merr, "msg", None) or merr))
                _update_in(state, msign={"status": "error",
                                         "category": category,
                                         "code": code,
                                         "reason": reason,
                                         "detail": redact.scrub(detail,
                                                                secret)})
                # msign 失败不算整体失败：后面逐吧补签才是权威结果。
                _warn(state, "网页一键签到(msign)", merr, secret)
                _log("msign failed code={} category={} detail={}".format(
                    code, category, detail), secret)

            # ---- 3. enumerate followed forums ----------------------------
            _update_in(state, step="获取关注吧列表")
            forums = []
            pn = 1
            while pn <= MAX_FORUM_PAGES:
                res = await client.get_self_follow_forums(pn=pn, rn=200)
                rerr = getattr(res, "err", None)
                if rerr is not None:
                    item = _failure_item("获取关注吧列表", rerr, secret)
                    _log("follow list failed pn={} code={} category={} "
                         "detail={}".format(pn, item["code"],
                                            item["category"],
                                            item["detail"]), secret)
                    _update_in(state, status="error",
                               step="获取关注吧列表失败",
                               finished_at=_now_ms(),
                               error={"category": item["category"],
                                      "code": item["code"],
                                      "reason": item["reason"],
                                      "detail": item["detail"]})
                    return
                for forum in res:
                    forums.append(forum)
                if not getattr(res, "has_more", False):
                    break
                pn += 1

            to_sign = [f for f in forums if not bool(getattr(f, "is_signed",
                                                             False))]
            _update_in(state, forum_total=len(to_sign), step="逐吧补签")
            _log("run={} followed={} to_sign={} concurrency={}".format(
                state.get("run_id"), len(forums), len(to_sign),
                max(1, int(SIGN_CONCURRENCY))), secret)

            # ---- 4. sign each still-unsigned forum (single attempt) ------
            await _sign_forums(client, state, to_sign, secret)

            _update_in(state, status="done", step="签到完成",
                       finished_at=_now_ms())
            _log("run={} done success={} failed={} warnings={} elapsed_ms={}".format(
                state.get("run_id"), state.get("forum_success"),
                state.get("forum_failed"), len(state.get("warnings") or []),
                (state.get("finished_at") or _now_ms())
                - (state.get("started_at") or _now_ms())), secret)
    except Exception as exc:  # noqa: BLE001
        category = _classify_exception(exc)
        detail = redact.scrub("{}: {}".format(type(exc).__name__, exc), secret)
        _update_in(state, status="error", step="签到异常",
                   finished_at=_now_ms(),
                   error={"category": category,
                          "code": tieba_codes.parse_code(
                              getattr(exc, "code", None)),
                          "reason": tieba_codes.reason(
                              getattr(exc, "code", None)),
                          "detail": detail})
        _log("run={} exception category={} detail={}".format(
            state.get("run_id"), category, detail), secret)


# --------------------------------------------------------------------------
# report (v0.5.0: 让用户能把失败详情一次复制出来)
# --------------------------------------------------------------------------

def _snapshot_for_report(state):
    """只保留可公开字段；再整体脱敏一次，防御性兜底。"""
    snap = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": state.get("mode") or "single",
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "step": state.get("step"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "elapsed_ms": ((state.get("finished_at") or _now_ms())
                       - (state.get("started_at") or _now_ms())),
    }
    if snap["mode"] == "multi":
        accounts = []
        for sub in state.get("accounts") or []:
            accounts.append({
                "account_id": sub.get("account_id"),
                "account_name": sub.get("account_name"),
                "account_mask": sub.get("account_mask"),
                "status": sub.get("status"),
                "step": sub.get("step"),
                "msign": sub.get("msign"),
                "growth": sub.get("growth"),
                "forum_total": sub.get("forum_total"),
                "forum_checked": sub.get("forum_checked"),
                "forum_success": sub.get("forum_success"),
                "forum_failed": sub.get("forum_failed"),
                "failures": list(sub.get("failures") or [])[:_REPORT_FAILURES_MAX],
                "warnings": list(sub.get("warnings") or [])[:_REPORT_WARNINGS_MAX],
                "error": sub.get("error"),
            })
        snap["account_total"] = state.get("account_total")
        snap["account_done"] = state.get("account_done")
        snap["accounts"] = accounts
    else:
        snap.update({
            "account_id": state.get("account_id"),
            "account_name": state.get("account_name"),
            "account_mask": state.get("account_mask"),
            "msign": state.get("msign"),
            "growth": state.get("growth"),
            "forum_total": state.get("forum_total"),
            "forum_checked": state.get("forum_checked"),
            "forum_success": state.get("forum_success"),
            "forum_failed": state.get("forum_failed"),
            "failures": list(state.get("failures") or [])[:_REPORT_FAILURES_MAX],
            "warnings": list(state.get("warnings") or [])[:_REPORT_WARNINGS_MAX],
            "error": state.get("error"),
        })
    return redact.scrub_obj(snap)


def _persist_report(state):
    try:
        snap = _snapshot_for_report(state)
        path = _data_path(_REPORT_FILENAME)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - 报告落盘失败不影响签到
        pass


def load_last_report():
    try:
        with open(_data_path(_REPORT_FILENAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _fmt_msign(msign):
    if not msign:
        return None
    if msign.get("status") == "success":
        return "网页一键签到：成功"
    code = msign.get("code")
    detail = msign.get("detail") or ""
    return "网页一键签到：失败（{}）".format(detail or
                                             tieba_codes.explain(code))


def _fmt_failure(item):
    reason = item.get("reason")
    code = item.get("code")
    bits = [str(item.get("forum") or "?")]
    if item.get("category"):
        bits.append(str(item["category"]))
    if code is not None:
        bits.append("错误码 {}".format(code))
    if reason:
        bits.append(str(reason))
    elif code is not None:
        bits.append("未收录的错误码")
    detail = item.get("detail")
    if detail and str(detail) not in ("", "None"):
        bits.append("百度原文：{}".format(detail))
    return " | ".join(bits)


def _fmt_growth(growth):
    if not growth:
        return None
    status = growth.get("status")
    if status == "success":
        return "成长等级签到：成功"
    if status == "done":
        return "成长等级签到：今天已经签到过了"
    detail = growth.get("detail") or tieba_codes.explain(growth.get("code"))
    return "成长等级签到：失败（{}）".format(detail)


def _report_lines_for_account(label, acct):
    lines = []
    name = acct.get("account_name") or "贴吧账号"
    mask = acct.get("account_mask") or ""
    lines.append("{}：{}（BDUSS {}）".format(label, name, mask))
    lines.append("  状态：{}".format(acct.get("status") or "?"))
    growth = _fmt_growth(acct.get("growth"))
    if growth:
        lines.append("  " + growth)
    msign = _fmt_msign(acct.get("msign"))
    if msign:
        lines.append("  " + msign)
    if acct.get("forum_total") is not None:
        lines.append("  补签：检查 {} / 成功 {} / 失败 {}".format(
            acct.get("forum_total") or 0,
            acct.get("forum_success") or 0,
            acct.get("forum_failed") or 0))
    error = acct.get("error")
    if error:
        lines.append("  整体错误：{}".format(_fmt_failure({
            "forum": "签到流程",
            "category": error.get("category"),
            "code": error.get("code"),
            "reason": error.get("reason"),
            "detail": error.get("detail"),
        })))
    for item in acct.get("warnings") or []:
        lines.append("  警告：{}".format(_fmt_failure(item)))
    for item in acct.get("failures") or []:
        lines.append("  失败：{}".format(_fmt_failure(item)))
    return lines


def report_text(state=None):
    """返回可直接复制/粘贴的纯文本报告；没有记录时返回提示。"""
    if state is None:
        state = load_last_report()
    if not state:
        return "还没有签到记录。先执行一次「一键签到」，再回来查看。"
    lines = ["贴吧签到助手 签到报告",
             "生成时间：{}".format(state.get("generated_at") or "?"),
             "运行编号：{}".format(state.get("run_id") or "?"),
             "结果：{}".format(state.get("status") or "?")]
    elapsed = state.get("elapsed_ms")
    if isinstance(elapsed, int) and elapsed >= 0:
        lines.append("耗时：{:.1f} 秒".format(elapsed / 1000.0))
    lines.append("")
    if state.get("mode") == "multi":
        for idx, acct in enumerate(state.get("accounts") or [], 1):
            lines.extend(_report_lines_for_account(
                "账号 {}/{}".format(idx, state.get("account_total") or "?"),
                acct))
            lines.append("")
    else:
        lines.extend(_report_lines_for_account("账号", state))
        lines.append("")
    lines.append("说明：本报告不含完整 BDUSS（只保留掩码）；"
                 "错误码解释来自代码内置的已核实码表，未收录的会明确写「未收录」。")
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------
# multi-account batch (manual one-key sign may select several accounts)
# --------------------------------------------------------------------------

def _new_batch(run_id, pairs):
    accounts = []
    for idx, (account, _bduss) in enumerate(pairs):
        sub = _new_state("{}-{:d}".format(run_id, idx), account)
        sub["status"] = "pending"
        sub["idx"] = idx
        accounts.append(sub)
    return {
        "run_id": run_id,
        "status": "starting",
        "step": "准备中",
        "mode": "multi",
        "account_total": len(accounts),
        "account_done": 0,
        "accounts": accounts,
        "started_at": _now_ms(),
        "finished_at": None,
    }


def _update_account(account_id, **kwargs):
    with _lock:
        if _state is None or _state.get("mode") != "multi":
            return
        for sub in _state["accounts"]:
            if sub["account_id"] == account_id:
                sub.update(kwargs)
                return


def start_multi(pairs):
    """Start one run per (account, bduss) pair, sequentially.

    pairs: [(account_dict, bduss_str), ...] -- bduss lives only in this
    call frame / the coroutine, never in state snapshots.
    Returns (run_id, err|None).
    """
    global _run_counter, _state
    with _lock:
        if _state is not None and _state["status"] in ("starting", "running"):
            return None, "已有签到任务正在进行，请等待完成"
        if not pairs:
            return None, "未选择要签到的账号"
        _run_counter += 1
        run_id = "m{:d}".format(_run_counter)
        batch = _new_batch(run_id, pairs)
        _state = batch

    def _worker():
        loop = tieba_api._ensure_loop()
        for idx, (account, bduss) in enumerate(pairs):
            with _lock:
                sub = batch["accounts"][idx] if batch is not None else None
            if sub is None:
                break
            _update_account(account["id"], status="running",
                            step="准备中")
            future = asyncio.run_coroutine_threadsafe(
                _sign_async(sub, bduss), loop)
            try:
                future.result(timeout=900)
            except asyncio.TimeoutError:
                sub.update(status="error", step="签到超时",
                           finished_at=_now_ms(),
                           error={"category": "网络异常", "code": None,
                                  "reason": None,
                                  "detail": "签到超过 15 分钟仍未完成，"
                                            "已停止等待"})
            except Exception as exc:  # noqa: BLE001
                sub.update(status="error", step="签到异常",
                           finished_at=_now_ms(),
                           error={"category": _classify_exception(exc),
                                  "code": None, "reason": None,
                                  "detail": "{}: {}".format(
                                      type(exc).__name__, exc)})
            with _lock:
                if sub.get("status") not in ("done", "error"):
                    sub.update(status="error", step="签到中断",
                               error={"category": "未知错误", "code": None,
                                      "reason": None,
                                      "detail": "签到线程异常结束"})
                batch["account_done"] += 1
                batch["step"] = "账号进度 {}/{}".format(
                    batch["account_done"], batch["account_total"])
        batch["status"] = "done"
        batch["step"] = "全部账号签到完成"
        batch["finished_at"] = _now_ms()
        _persist_report(batch)

    threading.Thread(target=_worker, name="tieba-sign-multi-worker",
                     daemon=True).start()
    return run_id, None
