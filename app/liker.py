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
import tiers

_DATA_FILENAME = "tiebasign_like.json"
_MAX_FORUM_PAGES = 25
#: 点赞并发 / 延迟由速度档位（tiers.py）决定，这里不再写死。
#: L3：取帖是纯读、风控权重低，单独给 12 并发；点赞仍由档位并发管。
_FETCH_CONCURRENCY = 12
_LIKE_RETRIES = 2
_RL_CODE = 3280004
_RN = 30
#: L1/L2：进程内累积，每 20 个「ok 状态变化」才整文件落盘一次，结束再落一次。
_FLUSH_EVERY = 20
_MIN_LIKE_RATE = 1.0
#: L4：撞 3280004 后的全局冷却时间（秒）；测试可调小。
_RL_BACKOFF = (5.0, 12.0)

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


class _RateLimiter:
    """L5：按全局每秒速率放行请求的令牌桶。

    限的是「每秒几个请求」而不是「同时几个」；撞限流降速时（L6）rate 会砍半。
    只在点赞协程所在的单个事件循环里使用，不需要线程锁。
    """

    def __init__(self, rate_per_sec, burst=None):
        self.rate = max(0.1, float(rate_per_sec))
        self.capacity = max(1.0, float(burst if burst is not None
                                       else rate_per_sec))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            wait = 0.0
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait)

    def slow_down(self):
        """L6：撞一次限流就把全局速率砍半（不低于 _MIN_LIKE_RATE）。"""
        self.rate = max(_MIN_LIKE_RATE, self.rate / 2.0)
        self.capacity = max(1.0, min(self.capacity, self.rate))


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


def _note_last_run(data, sub, elapsed_ms):
    """把本轮分段耗时写进内存 store；由调用方在结束时统一落盘。

    点赞是隔天首跑才准的（当天点过的吧会被跳过），所以数据必须落盘，
    不能只留在内存状态里。
    """
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
        "tier_level": sub.get("tier_level"),
        "tier_name": sub.get("tier_name"),
    }


def _get_forum_record(data, account_id, fname):
    acct = data["accounts"].get(str(account_id)) or {}
    if acct.get("date") != _today():
        return None
    return (acct.get("forums") or {}).get(fname)


def _record_forum(data, account_id, fname, ok, tid=None, title=None,
                  code=None, reason=None, advice=None, detail=None, raw=None):
    """把一次点赞结果写进内存 store，返回 ``ok`` 状态是否变化。

    L1：每个吧不再整文件 load + save，只在进程内更新字典；
    L2：只有「ok 状态变化」才计入批量落盘的计数，失败重试仍是失败时不写盘。
    """
    aid = str(account_id)
    acct = data["accounts"].get(aid) or {}
    if acct.get("date") != _today():
        acct = {"date": _today(), "forums": {}}
    forums = acct.get("forums")
    if not isinstance(forums, dict):
        forums = {}
    prev = forums.get(fname)
    changed = prev is None or bool(prev.get("ok")) != bool(ok)
    forums[fname] = {
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
    acct["forums"] = forums
    data["accounts"][aid] = acct
    return changed


def get_settings():
    data = _load()
    stored = tiers.clamp(data.get("level", tiers.DEFAULT_LEVEL))
    level = min(stored, tiers.max_open_level())
    tier = tiers.get(level)
    accounts = []
    for aid, acct in sorted(data["accounts"].items(),
                            key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        accounts.append({
            "account_id": int(aid) if str(aid).isdigit() else aid,
            "date": acct.get("date"),
            "forums": acct.get("forums") or {},
        })
    return {
        "enabled": bool(data.get("enabled", True)),
        "accounts": accounts,
        "level": tier.level,
        "stored_level": stored,
        "name": tier.name,
        "icon": tier.icon,
        "color": tier.color,
        "like_concurrency": tier.like_concurrency,
        "like_delay": list(tier.like_delay),
        "like_rate_per_sec": round(tiers.like_rate_per_sec(tier), 1),
        "max_level": tiers.TIER_COUNT - 1,
        "max_open_level": tiers.max_open_level(),
        "red_unlocked": tiers.RED_UNLOCKED,
    }


def save_settings(payload):
    """保存点赞侧的速度档位；红档未解锁时服务端拒绝。"""
    if not isinstance(payload, dict):
        raise ValueError("设置格式不对")
    level = tiers.clamp(payload.get("level", tiers.DEFAULT_LEVEL))
    if not tiers.can_open(level):
        raise ValueError("危险档位尚未解锁（0.6.0 只开放到「极速」）")
    data = _load()
    data["level"] = level
    _save(data)
    return get_settings()


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
            "tier_level": None,
            "tier_name": None,
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


async def _agree_with_retry(client, tid, sem, limiter, cooldown, sub, delay):
    """点赞；3280004（操作太频繁）退避后重试。

    L4：退避等待放在信号量**外面**，并写进全局冷却窗 ``cooldown``：撞一次之后
    所有任务都先等冷却过去，而不是每个任务各拿一个并发槽睡觉。
    L6：每次撞限流都把令牌桶速率砍半（``limiter.slow_down``），避免连环撞。
    """
    last_err = None
    for attempt in range(_LIKE_RETRIES + 1):
        wait = cooldown["until"] - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        await limiter.acquire()
        await sem.acquire()
        try:
            if attempt == 0:
                sleep_started = _now_ms()
                try:
                    if delay > 0:
                        await asyncio.sleep(delay)
                finally:
                    _phase_add(sub, PHASE_SLEEP, sleep_started)
            agree_started = _now_ms()
            try:
                resp = await client.agree(tid)
            finally:
                _phase_add(sub, PHASE_AGREE, agree_started)
        except Exception as exc:  # noqa: BLE001
            return None, exc
        finally:
            sem.release()
        err = getattr(resp, "err", None)
        if err is None:
            return resp, None
        last_err = err
        code = tieba_codes.parse_code(getattr(err, "code", None))
        if code != _RL_CODE or attempt >= _LIKE_RETRIES:
            return resp, err
        backoff = _RL_BACKOFF[0] if attempt == 0 else _RL_BACKOFF[1]
        cooldown["until"] = time.monotonic() + backoff
        limiter.slow_down()
    return None, last_err


async def _like_account_async(account, bduss):
    import aiotieba

    aid = account["id"]
    sub = _sub_for(aid)
    # M3a / D4：档位只在入口读一次；协程里不再读盘。
    cfg = _load()
    tier = tiers.get(min(tiers.clamp(cfg.get("level", tiers.DEFAULT_LEVEL)),
                         tiers.max_open_level()))
    like_concurrency = max(1, tier.like_concurrency)
    like_delay = tuple(tier.like_delay)
    like_rate = tiers.like_rate_per_sec(tier)
    _update_account(aid, tier_level=tier.level, tier_name=tier.name)
    run_started = _now_ms()
    store = _load()
    flush = {"changes": 0}

    def _flush():
        try:
            # 运行期间用户可能在设置页改了档位：落盘前先把 level 从磁盘
            # 合回来，别让本轮开始时读到的旧值把新设置覆盖掉。
            fresh = _load()
            if "level" in fresh:
                store["level"] = fresh["level"]
            _save(store)
        except Exception:  # noqa: BLE001 - 落盘失败不能打断点赞
            pass

    def _record(*args, **kwargs):
        # L1/L2：内存累积；每 _FLUSH_EVERY 个 ok 变化落盘一次，结束再落一次。
        changed = _record_forum(store, *args, **kwargs)
        if changed:
            flush["changes"] += 1
            if flush["changes"] % _FLUSH_EVERY == 0:
                _flush()
        return changed

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

            # L3：取帖与点赞分开限流，取帖不会把点赞的槽位占死。
            fetch_sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
            like_sem = asyncio.Semaphore(like_concurrency)
            limiter = _RateLimiter(like_rate, like_concurrency)
            cooldown = {"until": 0.0}
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
                rec = _get_forum_record(store, aid, fname)
                if rec and rec.get("ok"):
                    _bump(aid, "skipped")
                    _bump(aid, "checked")
                    return
                fetch_started = _now_ms()
                try:
                    async with fetch_sem:
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
                # 点赞接口对频率很敏感：首次请求前停一小段，限流退避见
                # _agree_with_retry（冷却窗在信号量外面）。
                resp, err = await _agree_with_retry(
                    client, tid, like_sem, limiter, cooldown, sub,
                    rng.uniform(*like_delay))
                if err is not None:
                    info = _describe(err)
                    _add_failure(sub, fname, info)
                    _record(aid, fname, False, tid, title,
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
                _record(aid, fname, True, tid, title)
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
        _note_last_run(store, sub, _now_ms() - run_started)
        _flush()
