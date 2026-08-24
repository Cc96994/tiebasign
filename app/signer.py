# -*- coding: utf-8 -*-
"""M2.3 manual one-key sign engine.

Flow per PRD v1.0 (single attempt, NO retry):
    1. get_self_info          -> account validity gate
    2. client.sign_forums()   -> Baidu web one-key sign (msign)
    3. get_self_follow_forums -> enumerate followed forums, page by page
    4. sign_forum(fname)      -> sign every forum still not signed
Failure categories: 已签到 / 未关注 / 账号失效 / 网络异常 / 风控其他.
Manual runs never raise system notifications and never set the red dot.

All state lives in a module-level dict guarded by _lock; the HTTP layer polls
GET /api/sign/state. The aiotieba coroutine runs on tieba_api's event loop
via run_coroutine_threadsafe from a worker thread.
"""

import asyncio
import threading
import time

import tieba_api

_lock = threading.RLock()
_run_counter = 0
_state = None

NETWORK_TYPE_HINTS = (
    "ClientConnector", "ClientOSError", "ClientConnection", "ConnectionError",
    "TimeoutError", "Timeout", "SSLCertVerificationError", "SSLError",
    "CertificationError", "SocketError", "Socket", "OSError",
)

MAX_FORUM_PAGES = 25  # 25 * 200 = 5000 forums, far beyond realistic usage


def _now_ms():
    return int(time.time() * 1000)


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
        "msign": None,          # {"status": "success|error", "category","detail"}
        "forum_total": 0,       # forums that still needed signing
        "forum_checked": 0,
        "forum_success": 0,
        "forum_failed": 0,
        "failures": [],         # [{forum, category, code, detail}]
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
                accounts.append(acct_snap)
            snap["accounts"] = accounts
        else:
            snap["failures"] = list(_state["failures"])
        return snap


def start(account, bduss):
    """Launch a worker for the given account. Returns (run_id, err|None)."""
    global _run_counter, _state
    with _lock:
        if _state is not None and _state["status"] in ("starting", "running"):
            return None, "已有签到任务正在进行，请等待完成"
        _run_counter += 1
        run_id = "r{:d}".format(_run_counter)
        _state = _new_state(run_id, account)

    def _worker():
        loop = tieba_api._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(
            _sign_async(_state, bduss), loop)
        try:
            future.result(timeout=900)  # 15 min ceiling
        except asyncio.TimeoutError:
            _update(status="error", step="签到超时",
                    error="签到超过 15 分钟仍未完成，已停止等待")
        except Exception as exc:  # noqa: BLE001
            _update(status="error", step="签到异常",
                    error="worker error: {}: {}".format(
                        type(exc).__name__, exc))

    threading.Thread(target=_worker, name="tieba-sign-worker",
                     daemon=True).start()
    return run_id, None


def _classify_server_error(code, msg):
    code_int = code
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        code_int = None
    msg_l = (msg or "").lower()

    if code_int == 160002 or "已经签过" in msg_l or "已签到" in msg_l:
        return "已签到"
    # server told us the credential is bad / session expired
    if any(k in msg_l for k in ("bduss", "登录已失效", "登录失效",
                                "用户不存在", "未登录", "账号失效",
                                "帐号失效", "失效", "过期")):
        return "账号失效"
    # forum relationship problems
    if any(k in msg_l for k in ("未关注", "关注该吧", "先关注",
                                "取消关注", "非本吧会员", "会员才能",
                                "吧不存在")):
        return "未关注"
    return "风控其他"


def _err_detail(err):
    code = getattr(err, "code", None)
    msg = getattr(err, "msg", None)
    if msg is None:
        msg = str(err)
    msg = str(msg)
    if code is None:
        return None, msg
    if int(code) == 340006:
        return code, "{}（百度错误码 340006，按 PRD 不自动重试）".format(msg)
    return code, "{}（错误码 {}）".format(msg, code)


def _classify_exception(exc):
    name = type(exc).__name__
    text = "{} {}".format(name, exc)
    mro_text = " ".join(c.__name__ for c in type(exc).__mro__)
    if any(k in mro_text or k in text for k in NETWORK_TYPE_HINTS):
        return "网络异常"
    code = getattr(exc, "code", None)
    msg = getattr(exc, "msg", None)
    if code is not None:
        return _classify_server_error(code, str(msg or exc))
    return "风控其他"


def _record_forum_failure(state, fname, err):
    code, detail = _err_detail(err)
    category = _classify_server_error(code, str(getattr(err, "msg", None)
                                                or err))
    _append_failure_in(state, {
        "forum": fname,
        "category": category,
        "code": code,
        "detail": detail,
    })


async def _get_self_info(client):
    return await client.get_self_info()


def _update_in(state, **kwargs):
    state.update(kwargs)


def _append_failure_in(state, item):
    state["failures"].append(item)
    state["forum_failed"] += 1


async def _sign_async(state, bduss):
    import aiotieba

    try:
        _update_in(state, status="running", step="连接百度并验证账号")
        async with aiotieba.Client(bduss) as client:
            # ---- 1. account validity gate --------------------------------
            user = await client.get_self_info()
            uerr = getattr(user, "err", None)
            if uerr is not None:
                code = getattr(uerr, "code", None)
                msg = getattr(uerr, "msg", None) or str(uerr)
                category = _classify_server_error(code, str(msg))
                detail = str(msg)
                if code is not None:
                    detail = "{}（错误码 {}）".format(detail, code)
                _update_in(state, status="error", step="账号验证失败",
                           error={"category": category, "detail": detail})
                return

            # ---- 2. Baidu web one-key sign (msign) -----------------------
            _update_in(state, step="网页一键签到")
            msign = await client.sign_forums()
            merr = getattr(msign, "err", None)
            if merr is None:
                _update_in(state, msign={"status": "success",
                                         "category": None,
                                         "detail": "网页一键签到成功"})
            else:
                code, detail = _err_detail(merr)
                category = _classify_server_error(
                    code, str(getattr(merr, "msg", None) or merr))
                _update_in(state, msign={"status": "error",
                                         "category": category,
                                         "detail": detail})

            # ---- 3. enumerate followed forums ----------------------------
            _update_in(state, step="获取关注吧列表")
            forums = []
            pn = 1
            while pn <= MAX_FORUM_PAGES:
                res = await client.get_self_follow_forums(pn=pn, rn=200)
                rerr = getattr(res, "err", None)
                if rerr is not None:
                    code = getattr(rerr, "code", None)
                    msg = getattr(rerr, "msg", None) or str(rerr)
                    category = _classify_exception(rerr)
                    detail = str(msg)
                    if code is not None:
                        detail = "{}（错误码 {}）".format(detail, code)
                    _update_in(state, status="error",
                               step="获取关注吧列表失败",
                               error={"category": category, "detail": detail})
                    return
                for forum in res:
                    forums.append(forum)
                if not getattr(res, "has_more", False):
                    break
                pn += 1

            to_sign = [f for f in forums if not bool(getattr(f, "is_signed",
                                                             False))]
            _update_in(state, forum_total=len(to_sign), step="逐吧补签")

            # ---- 4. sign each still-unsigned forum (single attempt) ------
            for idx, forum in enumerate(to_sign, 1):
                fname = str(getattr(forum, "fname", ""))
                if not fname:
                    _record_forum_failure(state, "<无名吧>",
                                          RuntimeError("forum name missing"))
                    _update_in(state, forum_checked=idx)
                    continue
                resp = await client.sign_forum(fname)
                rerr = getattr(resp, "err", None)
                if rerr is None:
                    state["forum_success"] += 1
                else:
                    _record_forum_failure(state, fname, rerr)
                _update_in(state, forum_checked=idx)
                # keep a readable step for long lists
                if idx % 10 == 0 or idx == len(to_sign):
                    _update_in(state, step="逐吧补签 {}/{}".format(
                        idx, len(to_sign)))

            _update_in(state, status="done", step="签到完成",
                       finished_at=_now_ms())
    except Exception as exc:  # noqa: BLE001
        category = _classify_exception(exc)
        _update_in(state, status="error", step="签到异常",
                   finished_at=_now_ms(),
                   error={"category": category,
                          "detail": "{}: {}".format(type(exc).__name__, exc)})


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
        _state = _new_batch(run_id, pairs)

    def _worker():
        loop = tieba_api._ensure_loop()
        for idx, (account, bduss) in enumerate(pairs):
            with _lock:
                sub = _state["accounts"][idx] if _state is not None else None
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
                           error={"category": "网络异常",
                                  "detail": "签到超过 15 分钟仍未完成，"
                                            "已停止等待"})
            except Exception as exc:  # noqa: BLE001
                sub.update(status="error", step="签到异常",
                           finished_at=_now_ms(),
                           error={"category": _classify_exception(exc),
                                  "detail": "{}: {}".format(
                                      type(exc).__name__, exc)})
            with _lock:
                if _state is not None:
                    if sub.get("status") not in ("done", "error"):
                        sub.update(status="error", step="签到中断",
                                   error={"category": "风控其他",
                                          "detail": "签到线程异常结束"})
                    _state["account_done"] += 1
                    _state["step"] = "账号进度 {}/{}".format(
                        _state["account_done"], _state["account_total"])
        _update(status="done", step="全部账号签到完成",
                finished_at=_now_ms())

    threading.Thread(target=_worker, name="tieba-sign-multi-worker",
                     daemon=True).start()
    return run_id, None
