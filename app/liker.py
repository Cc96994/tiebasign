# -*- coding: utf-8 -*-
"""每日随机点赞：每个关注的吧随机挑一个帖子点主楼赞。

用户口径（2026-11-03 修正）：
  * **不是**只挑一个吧：每个关注的吧都要随机抽一个帖子点赞；
  * 每个吧每天最多一次，全部吧都完成当天就结束；
  * 点赞失败只记失败详情/提醒，不影响签到；
  * 签到页上有独立的「随机点赞」模块和按钮，与一键签到/一键水贴并列。

实现边界：
  * 只点主楼赞（``Client.agree(tid)``），不点楼中楼；
  * 跳过置顶帖/被屏蔽帖；某吧首页没有可点的帖子就跳过；
  * 每个账号串行跑自己的关注吧，同一时刻只发 1 个点赞请求，并在每次
    ``agree`` 前停 2~3.5 秒；遇到 3280004（操作太频繁）会退避重试，
    避免“大多数吧都 3280004”；
  * 数据文件 ``<ANDROID_PRIVATE>/tiebasign_like.json``，不含 BDUSS；
  * 每日记录按账号 + 吧名，成功一次当天不再点这个吧。

v0.6.0 P0（优化清单第 2 节）：
  * 分段耗时 ``phase_ms``（拉列表 / 取帖 / 点赞前等待 / 点赞请求）随
    ``last_runs`` 落盘——当天点过的吧重跑会 skip，只有隔天首跑的数据能
    证明提速，所以不能只留在内存状态里；
  * 错误分类统一走 ``app/errors.py``，失败记录保留真实错误码。
"""

import asyncio
import json
import os
import random
import threading
import time

import errors
import redact
import tieba_api
import tieba_codes

_DATA_FILENAME = "tiebasign_like.json"
_MAX_FORUM_PAGES = 25
_LIKE_CONCURRENCY = 5
_LIKE_DELAY = (0.25, 0.5)
_LIKE_RETRIES = 2
_RL_CODE = 3280004
_RN = 30

#: 分段耗时（P0-2）：拉列表 / 取帖 / 点赞前等待 / 点赞请求。
PHASE_LIST = "list"
PHASE_FETCH = "fetch"
PHASE_SLEEP = "sleep"
PHASE_AGREE = "agree"
PHASE_LIKE = "like"

_lock = threading.RLock()
_state = None
_run_counter = 0


def _data_path():
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, _DATA_FILENAME)


def _load():
    try:
        with open(_data_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("enabled", True)
            if not isinstance(data.get("accounts"), dict):
                data["accounts"] = {}
            return data
    except (OSError, ValueError):
        pass
    return {"enabled": True, "accounts": {}}


def _save(data):
    path = _data_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _today():
    return time.strftime("%Y-%m-%d")


def _now_ms():
    return int(time.time() * 1000)


def _phase_add(sub, phase, started_ms):
    """累加一段耗时到账号状态的 phase_ms（毫秒，并发任务是累计值）。"""
    spent = max(0, _now_ms() - int(started_ms))
    phases = sub.get("phase_ms")
    if not isinstance(phases, dict):
        phases = {}
        sub["phase_ms"] = phases
    phases[phase] = int(phases.get(phase) or 0) + spent


def _persist_last_run(sub, elapsed_ms):
    """把本轮分段耗时写进 tiebasign_like.json，重启后还能复盘。

    点赞是隔天首跑才准的（当天点过的吧会被跳过），所以数据必须落盘，
    不能只留在内存状态里。
    """
    try:
        data = _load()
        aid = sub.get("account_id")
        if aid is None:
            return
        aid = str(aid)
        runs = data.get("last_runs")
        if not isinstance(runs, dict):
            runs = {}
            data["last_runs"] = runs
        runs[aid] = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_ms": int(elapsed_ms),
            "total": int(sub.get("total") or 0),
            "success": int(sub.get("success") or 0),
            "failed": int(sub.get("failed") or 0),
            "skipped": int(sub.get("skipped") or 0),
            "phase_ms": dict(sub.get("phase_ms") or {}),
        }
        _save(data)
    except Exception:  # noqa: BLE001 - 统计落盘失败不能影响点赞
        pass


def _get_forum_record(account_id, fname):
    data = _load()
    acct = data["accounts"].get(str(account_id)) or {}
    if acct.get("date") != _today():
        return None
    return (acct.get("forums") or {}).get(fname)


def _record_forum(account_id, fname, ok, tid=None, title=None,
                  code=None, reason=None, advice=None, detail=None, raw=None):
    data = _load()
    aid = str(account_id)
    acct = data["accounts"].get(aid) or {}
    if acct.get("date") != _today():
        acct = {"date": _today(), "forums": {}}
    acct["forums"][fname] = {
        "ok": bool(ok),
        "tid": int(tid) if tid else None,
        "title": redact.scrub(title or "")[:80],
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
        "reason": reason,
        "advice": advice,
        "detail": redact.scrub(detail or ""),
        "raw": redact.scrub(raw or ""),
    }
    data["accounts"][aid] = acct
    _save(data)
    return acct["forums"][fname]


def get_settings():
    data = _load()
    accounts = []
    for aid, acct in sorted(data["accounts"].items(),
                            key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        accounts.append({
            "account_id": int(aid) if str(aid).isdigit() else aid,
            "date": acct.get("date"),
            "forums": acct.get("forums") or {},
        })
    return {"enabled": bool(data.get("enabled", True)), "accounts": accounts}


def _new_state(run_id, pairs):
    accounts = []
    for idx, (account, _bduss) in enumerate(pairs):
        accounts.append({
            "idx": idx,
            "account_id": account["id"],
            "account_name": account.get("name") or account.get("mask") or "",
            "account_mask": account.get("mask") or "",
            "status": "pending",
            "step": "等待中",
            "total": 0,
            "checked": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "phase_ms": {},
            "failures": [],
            "error": None,
        })
    return {
        "run_id": run_id,
        "status": "starting",
        "step": "准备中",
        "mode": "like",
        "account_total": len(accounts),
        "account_done": 0,
        "accounts": accounts,
        "started_at": int(time.time() * 1000),
        "finished_at": None,
        "error": None,
    }


def get_state():
    with _lock:
        if _state is None:
            return {"status": "idle"}
        snap = dict(_state)
        snap["accounts"] = []
        for sub in _state["accounts"]:
            item = dict(sub)
            item["failures"] = list(sub["failures"])
            item["phase_ms"] = dict(sub.get("phase_ms") or {})
            snap["accounts"].append(item)
        return snap


def _update(**kwargs):
    with _lock:
        if _state is not None:
            _state.update(kwargs)


def _update_account(account_id, **kwargs):
    with _lock:
        if _state is None:
            return
        for sub in _state["accounts"]:
            if sub["account_id"] == account_id:
                sub.update(kwargs)
                return


def start(pairs):
    """pairs: [(account_dict, bduss_str), ...]；bduss 只活在本次调用/协程里。"""
    global _state, _run_counter
    with _lock:
        if _state is not None and _state["status"] in ("starting", "running"):
            return None, "随机点赞正在进行，请等待完成"
        if not pairs:
            return None, "未选择要点赞的账号"
        _run_counter += 1
        run_id = "like{:d}".format(_run_counter)
        _state = _new_state(run_id, pairs)

    def _worker():
        loop = tieba_api._ensure_loop()
        for idx, (account, bduss) in enumerate(pairs):
            sub = _state["accounts"][idx] if _state is not None else None
            if sub is None:
                break
            _update_account(account["id"], status="running", step="准备中")
            future = asyncio.run_coroutine_threadsafe(
                _like_account_async(account, bduss), loop)
            try:
                future.result(timeout=1800)
            except asyncio.TimeoutError:
                _update_account(account["id"], status="error",
                                step="点赞超时",
                                error={"detail": "随机点赞超过 30 分钟仍未完成"})
            except Exception as exc:  # noqa: BLE001
                _update_account(account["id"], status="error",
                                step="点赞异常",
                                error={"detail": "{}: {}".format(
                                    type(exc).__name__, exc)})
            with _lock:
                if _state is not None:
                    if sub.get("status") not in ("done", "error"):
                        sub.update(status="error", step="点赞中断",
                                   error={"detail": "随机点赞线程异常结束"})
                    _state["account_done"] += 1
                    _state["step"] = "账号进度 {}/{}".format(
                        _state["account_done"], _state["account_total"])
        _update(status="done", step="随机点赞完成",
                finished_at=int(time.time() * 1000))

    threading.Thread(target=_worker, name="tieba-like-worker",
                     daemon=True).start()
    return run_id, None


def _describe(err):
    """统一分类器：返回 category/code/reason/advice/detail/raw。"""
    return errors.classify(err, subject="这个吧的点赞")


def _bump(aid, field):
    with _lock:
        if _state is None:
            return
        for sub in _state["accounts"]:
            if sub["account_id"] == aid:
                sub[field] = int(sub.get(field) or 0) + 1
                if field == "checked":
                    checked = sub["checked"]
                    if checked % 10 == 0 or checked == sub["total"]:
                        sub["step"] = "逐个吧点赞 {}/{}".format(
                            checked, sub["total"])
                return


def _add_failure(sub, fname, info):
    with _lock:
        sub.setdefault("failures", []).append({
            "forum": redact.scrub(fname),
            "category": info["category"],
            "code": info["code"],
            "reason": info["reason"],
            "advice": info["advice"],
            "detail": info["detail"],
            "raw": info["raw"],
        })


def _sub_for(aid):
    with _lock:
        if _state is None:
            return {"failures": []}
        for sub in _state["accounts"]:
            if sub["account_id"] == aid:
                return sub
    return {"failures": []}


async def _fetch_forums(client, sub):
    forums = []
    pn = 1
    while pn <= _MAX_FORUM_PAGES:
        res = await client.get_self_follow_forums(pn=pn, rn=200)
        rerr = getattr(res, "err", None)
        if rerr is not None:
            info = _describe(rerr)
            _update_account(sub["account_id"], status="error",
                            step="获取关注吧失败",
                            error={"category": info["category"],
                                   "code": info["code"],
                                   "reason": info["reason"],
                                   "advice": info["advice"],
                                   "detail": info["detail"],
                                   "raw": info["raw"]})
            return None
        for forum in res:
            forums.append(forum)
        if not getattr(res, "has_more", False):
            break
        pn += 1
    return forums


async def _agree_with_retry(client, tid):
    """点赞；3280004 表示操作太频繁，退避后重试，不把整轮都撞死。"""
    last_err = None
    for attempt in range(_LIKE_RETRIES + 1):
        try:
            resp = await client.agree(tid)
        except Exception as exc:  # noqa: BLE001
            return None, exc
        err = getattr(resp, "err", None)
        if err is None:
            return resp, None
        last_err = err
        if tieba_codes.parse_code(getattr(err, "code", None)) != _RL_CODE \
                or attempt >= _LIKE_RETRIES:
            return resp, err
        await asyncio.sleep(5 if attempt == 0 else 12)
    return None, last_err


async def _like_account_async(account, bduss):
    import aiotieba

    aid = account["id"]
    sub = _sub_for(aid)
    run_started = _now_ms()
    try:
        async with aiotieba.Client(bduss) as client:
            list_started = _now_ms()
            try:
                forums = await _fetch_forums(client, sub)
            finally:
                _phase_add(sub, PHASE_LIST, list_started)
            if forums is None:
                return
            _update_account(aid, total=len(forums), status="running",
                            step="逐个吧点赞")
            if not forums:
                _update_account(aid, status="done", step="没有关注的吧")
                return

            sem = asyncio.Semaphore(max(1, _LIKE_CONCURRENCY))
            flags = {"stop": False}
            rng = random.SystemRandom()

            async def one(forum):
                fname = str(getattr(forum, "fname", "") or "")
                if not fname:
                    _bump(aid, "checked")
                    return
                if flags["stop"]:
                    _bump(aid, "skipped")
                    return
                async with sem:
                    if flags["stop"]:
                        _bump(aid, "skipped")
                        return
                    rec = _get_forum_record(aid, fname)
                    if rec and rec.get("ok"):
                        _bump(aid, "skipped")
                        _bump(aid, "checked")
                        return
                    fetch_started = _now_ms()
                    try:
                        threads = await client.get_threads(fname, pn=1,
                                                           rn=_RN)
                    except Exception as exc:  # noqa: BLE001
                        info = _describe(exc)
                        _add_failure(sub, fname, info)
                        _bump(aid, "failed")
                        _bump(aid, "checked")
                        return
                    finally:
                        _phase_add(sub, PHASE_FETCH, fetch_started)
                    terr = getattr(threads, "err", None)
                    if terr is not None:
                        info = _describe(terr)
                        _add_failure(sub, fname, info)
                        if info["category"] == tieba_codes.CAT_ACCOUNT:
                            flags["stop"] = True
                        _bump(aid, "failed")
                        _bump(aid, "checked")
                        return
                    pool = [t for t in threads
                            if int(getattr(t, "tid", 0) or 0)
                            and not bool(getattr(t, "is_top", False))
                            and not bool(getattr(t, "is_hide", False))]
                    if not pool:
                        _bump(aid, "skipped")
                        _bump(aid, "checked")
                        return
                    thread = rng.choice(pool)
                    tid = int(thread.tid)
                    title = str(getattr(thread, "title", "") or "")
                    # 点赞接口对频率很敏感：每次请求前停一小段，
                    # 3280004 再退避重试（见 _agree_with_retry）。
                    sleep_started = _now_ms()
                    try:
                        await asyncio.sleep(rng.uniform(*_LIKE_DELAY))
                    finally:
                        _phase_add(sub, PHASE_SLEEP, sleep_started)
                    agree_started = _now_ms()
                    try:
                        resp, err = await _agree_with_retry(client, tid)
                    finally:
                        _phase_add(sub, PHASE_AGREE, agree_started)
                    if err is not None:
                        info = _describe(err)
                        _add_failure(sub, fname, info)
                        _record_forum(aid, fname, False, tid, title,
                                      info["code"], info["reason"],
                                      info["advice"], info["detail"],
                                      info["raw"])
                        if info["category"] == tieba_codes.CAT_ACCOUNT:
                            flags["stop"] = True
                        if info["code"] == _RL_CODE:
                            flags["rate_limited"] = flags.get(
                                "rate_limited", 0) + 1
                            if flags["rate_limited"] >= 3:
                                flags["stop"] = True
                        _bump(aid, "failed")
                        _bump(aid, "checked")
                        return
                    _record_forum(aid, fname, True, tid, title)
                    _bump(aid, "success")
                    _bump(aid, "checked")

            like_started = _now_ms()
            try:
                await asyncio.gather(*(one(f) for f in forums))
            finally:
                _phase_add(sub, PHASE_LIKE, like_started)
            _update_account(aid, status="done", step="随机点赞完成")
    except Exception as exc:  # noqa: BLE001
        _update_account(aid, status="error", step="随机点赞异常",
                        error={"detail": "{}: {}".format(
                            type(exc).__name__, exc)})
    finally:
        # 当天点过的吧重跑会直接 skip，只有隔天首跑的数据才能证明提速；
        # 所以分段耗时必须落进 tiebasign_like.json，不能只在内存里。
        _persist_last_run(sub, _now_ms() - run_started)
