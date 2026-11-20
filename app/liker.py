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
  * 每个账号串行跑自己的关注吧；同一账号内按速度档位并发（3/5/8/12）
    分批点赞，一批点完再按 ``batch_interval_seconds``（默认 10 秒）等待，
    然后进入下一批；遇到 3280004（操作太频繁）自动暂停，到点续跑；
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
import re
import threading
import time

import errors
import invalid
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
#: 0.6.5：撞限流后的自动暂停时长（分钟，用户可改），以及解析百度文案的规则。
_LIMIT_PAUSE_DEFAULT = 5
_LIMIT_PAUSE_MIN = 1
_LIMIT_PAUSE_MAX = 60
_LIMIT_RE = re.compile(
    r"(\d+)\s*(秒|分钟|小时|min|mins|minute|minutes|sec|secs|second|seconds|"
    r"hour|hours)", re.IGNORECASE)
#: 0.6.6：每个吧提前缓存 7 个候选帖子；每 10 个赞休息 rest_minutes 分钟
#: （默认 2，可改）。0.6.7：点赞按档位并发分批，批间等待可改。
_CACHE_BATCH = 7
_CACHE_USED_MAX = 30
#: 0.6.7：每批最多「档位并发数」个点赞同时发出，批与批之间停
#: ``batch_interval_seconds`` 秒（默认 10，可改 1–120）。
_BATCH_INTERVAL_DEFAULT = 10
_BATCH_INTERVAL_MIN = 1
_BATCH_INTERVAL_MAX = 120
_REST_EVERY = 10
_REST_DEFAULT_MINUTES = 2
_REST_MAX_MINUTES = 60
#: L1/L2：进程内累积，每 20 个「ok 状态变化」才整文件落盘一次，结束再落一次。
_FLUSH_EVERY = 20
_MIN_LIKE_RATE = 1.0
#: 没撞过限流时从 6 次/秒起步；每次整轮干净就 +1，撞一次就减半并记住。
_LIKE_START_RATE = 6.0
#: 0.6.4：点赞速率硬上限。四档显式速率 4/7/10/12，令牌桶 burst=1。
_LIKE_RATE_CAP = 12.0
#: L4：撞 3280004 后的自动暂停由 ``limit_pause_minutes`` 或百度文案决定。

#: 分段耗时（P0-2）：拉列表 / 取帖 / 点赞前等待 / 点赞请求。
PHASE_LIST = "list"
PHASE_FETCH = "fetch"
PHASE_SLEEP = "sleep"
PHASE_AGREE = "agree"
PHASE_LIKE = "like"

_lock = threading.RLock()
_state = None
_run_counter = 0
_cache_state = None
_cache_counter = 0
#: 手动暂停（0.6.2 用户反馈）：set 时不再发新的取帖/点赞请求。
_pause = threading.Event()


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


def _clamp_pause_minutes(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = _LIMIT_PAUSE_DEFAULT
    return max(_LIMIT_PAUSE_MIN, min(_LIMIT_PAUSE_MAX, minutes))


def _limit_pause_minutes(data):
    return _clamp_pause_minutes(
        data.get("limit_pause_minutes", _LIMIT_PAUSE_DEFAULT))


def _clamp_rest_minutes(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = _REST_DEFAULT_MINUTES
    return max(0, min(_REST_MAX_MINUTES, minutes))


def _rest_minutes(data):
    return _clamp_rest_minutes(
        data.get("rest_minutes", _REST_DEFAULT_MINUTES))


def _clamp_batch_interval(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = _BATCH_INTERVAL_DEFAULT
    return max(_BATCH_INTERVAL_MIN, min(_BATCH_INTERVAL_MAX, seconds))


def _batch_interval_seconds(data):
    return _clamp_batch_interval(
        data.get("batch_interval_seconds", _BATCH_INTERVAL_DEFAULT))


def _rest_seconds(minutes):
    return int(minutes) * 60


def _fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "{} 秒".format(seconds)
    if seconds < 3600:
        return "{:.0f} 分钟".format(seconds / 60.0)
    return "{:.1f} 小时".format(seconds / 3600.0)


def _limit_wait_seconds(err, default_minutes):
    """优先读百度文案里的解除时间；读不到就用设置的分钟数。

    返回 (等待秒数, 是否来自百度文案)。aiotieba 4.7.1 的
    ``TiebaServerError`` 只有 code/msg，没有单独的 reset 时间字段，所以
    「读文案」是方向 1，读不到就是方向 2。
    """
    message = str(getattr(err, "msg", None) or err or "")
    match = _LIMIT_RE.search(message)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("秒") or unit.startswith("sec"):
            seconds = amount
        elif unit.startswith("分钟") or unit.startswith("min"):
            seconds = amount * 60
        else:
            seconds = amount * 3600
        return max(1, min(seconds, 3600)), True
    return max(60, _clamp_pause_minutes(default_minutes) * 60), False


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


def _cache_map(data):
    cache = data.get("cache")
    if not isinstance(cache, dict):
        cache = {}
        data["cache"] = cache
    return cache


def _cache_tids(data, fname):
    entry = _cache_map(data).get(fname) or {}
    tids = entry.get("tids") if isinstance(entry, dict) else None
    if not isinstance(tids, list):
        return []
    out = []
    for tid in tids:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        if tid > 0 and tid not in out:
            out.append(tid)
    return out


def _cache_set(data, fname, tids):
    used = _cache_used(data, fname)
    _cache_map(data)[fname] = {
        "tids": [int(t) for t in tids][:_CACHE_BATCH],
        "used": used[-_CACHE_USED_MAX:],
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _cache_remove(data, fname, tid):
    tid = int(tid)
    tids = [t for t in _cache_tids(data, fname) if t != tid]
    _cache_set(data, fname, tids)


def _cache_used(data, fname):
    entry = _cache_map(data).get(fname) or {}
    used = entry.get("used") if isinstance(entry, dict) else None
    out = []
    if isinstance(used, list):
        for tid in used:
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                continue
            if tid > 0 and tid not in out:
                out.append(tid)
    return out


def _cache_mark_used(data, fname, tid):
    tid = int(tid)
    used = [t for t in _cache_used(data, fname) if t != tid]
    used.append(tid)
    _cache_set_used(data, fname, used[-_CACHE_USED_MAX:])


def _cache_set_used(data, fname, used):
    entry = _cache_map(data).get(fname)
    if not isinstance(entry, dict):
        entry = {"tids": [], "used": []}
    entry["used"] = [int(t) for t in used][-_CACHE_USED_MAX:]
    _cache_map(data)[fname] = entry


def _cache_candidates(threads, used):
    """从一批帖子里挑最多 7 个可用 tid，排除置顶/隐藏/已点赞过的。"""
    used = set(int(t) for t in used)
    out = []
    for thread in threads:
        if getattr(thread, "is_top", False) or getattr(thread, "is_hide",
                                                       False):
            continue
        try:
            tid = int(getattr(thread, "tid", 0))
        except (TypeError, ValueError):
            continue
        if tid <= 0 or tid in used or tid in out:
            continue
        out.append(tid)
        if len(out) >= _CACHE_BATCH:
            break
    return out


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
    stored = tiers.clamp_like(data.get("level", tiers.LIKE_DEFAULT_LEVEL))
    level = min(stored, tiers.max_like_level())
    tier = tiers.get_like(level)
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
        "like_concurrency": tier.concurrency,
        "like_delay": list(tier.delay),
        "like_rate_per_sec": round(tiers.like_rate_per_sec(tier), 1),
        "limit_pause_minutes": _limit_pause_minutes(data),
        "cache_enabled": bool(data.get("cache_enabled", True)),
        "cache_batch": _CACHE_BATCH,
        "rest_minutes": _rest_minutes(data),
        "batch_interval_seconds": _batch_interval_seconds(data),
        "max_level": tiers.LIKE_TIER_COUNT - 1,
        "max_open_level": tiers.max_like_level(),
        "red_unlocked": True,
    }


def save_settings(payload):
    """保存点赞侧设置：速度档位（四档 0–3）和限流自动暂停分钟数。"""
    if not isinstance(payload, dict):
        raise ValueError("设置格式不对")
    data = _load()
    if "level" in payload:
        try:
            level = int(payload.get("level"))
        except (TypeError, ValueError):
            level = tiers.LIKE_DEFAULT_LEVEL
        if level < 0 or level > tiers.max_like_level():
            raise ValueError("点赞档位只有四档（0–3）")
        data["level"] = level
    if "limit_pause_minutes" in payload:
        data["limit_pause_minutes"] = _clamp_pause_minutes(
            payload.get("limit_pause_minutes"))
    if "cache_enabled" in payload:
        data["cache_enabled"] = bool(payload.get("cache_enabled"))
    if "rest_minutes" in payload:
        data["rest_minutes"] = _clamp_rest_minutes(
            payload.get("rest_minutes"))
    if "batch_interval_seconds" in payload:
        data["batch_interval_seconds"] = _clamp_batch_interval(
            payload.get("batch_interval_seconds"))
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
            "like_concurrency": 0,
            "batch_interval_seconds": 0,
            "like_rate": 0,
            "paused": False,
            "auto_pause_until": 0,
            "auto_pause_text": "",
            "rest_until": 0,
            "rest_text": "",
            "invalid_skipped": 0,
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
        "paused": False,
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


def is_paused():
    return _pause.is_set()


async def _wait_until_resumed():
    while _pause.is_set():
        await asyncio.sleep(0.2)


def set_paused(paused):
    """手动暂停/继续：只停新的取帖/点赞请求，进行中的会自然完成。"""
    if paused:
        _pause.set()
    else:
        _pause.clear()
    paused = bool(paused)
    with _lock:
        if _state is not None and _state.get("status") in ("starting",
                                                           "running"):
            _state["paused"] = paused
            _state["step"] = ("已暂停（不再发新请求）" if paused
                              else "逐个吧点赞")
            for sub in _state.get("accounts") or []:
                if sub.get("status") in ("pending", "running"):
                    sub["paused"] = paused
                    sub["step"] = ("已暂停（不再发新请求）" if paused
                                   else "逐个吧点赞")
    return paused


def get_cache_state():
    with _lock:
        if _cache_state is None:
            return {"status": "idle"}
        return dict(_cache_state)


def start_cache(pairs):
    """手动预缓存：为每个账号的关注吧各缓存最多 7 个候选帖子。"""
    global _cache_state, _cache_counter
    with _lock:
        if _cache_state is not None and _cache_state.get("status") in (
                "starting", "running"):
            return None, "缓存正在进行，请等待完成"
        if _state is not None and _state.get("status") in ("starting",
                                                           "running"):
            return None, "随机点赞正在进行，稍后再缓存"
        if not pairs:
            return None, "未选择要缓存的账号"
        _cache_counter += 1
        run_id = "cache{:d}".format(_cache_counter)
        _cache_state = {
            "run_id": run_id,
            "status": "starting",
            "step": "准备缓存",
            "done": 0,
            "total": 0,
            "error": None,
            "started_at": int(time.time() * 1000),
            "finished_at": None,
        }
        state = _cache_state
    threading.Thread(target=_cache_worker, args=(pairs, state),
                     name="tieba-like-cache", daemon=True).start()
    return run_id, None


async def _cache_account(account, bduss, state):
    import aiotieba

    async with aiotieba.Client(bduss) as client:
        forums = await _fetch_forums(client, {"phase_ms": {},
                                              "account_id": account["id"]})
        if forums is None:
            return
        state["total"] += len(forums)
        store = _load()
        for forum in forums:
            fname = str(getattr(forum, "fname", "") or "")
            if not fname:
                continue
            existing = _cache_tids(store, fname)
            if len(existing) >= _CACHE_BATCH:
                state["done"] += 1
                continue
            res = await client.get_threads(fname, pn=1, rn=_RN)
            if getattr(res, "err", None) is not None:
                state["done"] += 1
                continue
            new = _cache_candidates(
                res, _cache_used(store, fname) + existing)
            merged = (existing + new)[:_CACHE_BATCH]
            if merged:
                _cache_set(store, fname, merged)
            state["done"] += 1
            state["step"] = "缓存 {}".format(fname)
            if state["done"] % 10 == 0:
                _save(store)
        _save(store)


def _cache_worker(pairs, state):
    try:
        for account, bduss in pairs:
            asyncio.run(_cache_account(account, bduss, state))
        state["status"] = "done"
        state["step"] = "缓存完成"
    except Exception as exc:  # noqa: BLE001
        state["status"] = "error"
        state["error"] = "{}: {}".format(type(exc).__name__, exc)
        state["step"] = "缓存失败"
    finally:
        state["finished_at"] = int(time.time() * 1000)


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
        _pause.clear()
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


def _learned_rate(data):
    """之前运行学到的点赞速率；没有则 None。"""
    info = data.get("rate_limit") or {}
    try:
        rate = float(info.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def _like_rate_for_tier(tier):
    return min(_LIKE_RATE_CAP, tiers.like_rate_per_sec(tier))


def _starting_rate(tier, data):
    """没学过就从保守速率起步；学过就沿用，随后由 AIMD 慢慢加。"""
    base = _like_rate_for_tier(tier)
    learned = _learned_rate(data)
    if learned:
        return max(_MIN_LIKE_RATE, min(base, learned))
    return max(_MIN_LIKE_RATE, min(base, _LIKE_START_RATE))


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


def _mark_invalid(fname):
    """吧已查封：自动记入失效贴吧，下次运行不再请求它。"""
    try:
        invalid.add_forum(fname)
    except Exception:  # noqa: BLE001 - 标记失败不影响点赞
        pass


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
        await _wait_until_resumed()
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


async def _wait_rest(flags, sub):
    """0.6.6：每 10 个赞后的休息窗，所有任务都要等。"""
    while True:
        until = flags.get("rest_until") or 0.0
        wait = until - time.monotonic()
        if wait <= 0:
            break
        await asyncio.sleep(min(wait, 1.0))
    if flags.get("rest_text"):
        flags["rest_text"] = ""
        flags["rest_until"] = 0
        _update_account(sub["account_id"], rest_until=0, rest_text="",
                        step="逐个吧点赞")


async def _agree_with_retry(client, tid, sem, limiter, cooldown, flags, sub,
                            pause_minutes):
    """点赞；3280004 时自动暂停，再按百度解除时间/设置时长后重试一次。

    L4：等待放在信号量**外面**，并写进全局冷却窗 ``cooldown``：撞一次之后
    所有任务都先等冷却过去，而不是每个任务各拿一个并发槽睡觉。
    L6：每次撞限流都把令牌桶速率砍半（``limiter.slow_down``）。
    0.6.5：优先从百度文案里解析「X 分钟/秒/小时」作为解除时间；读不到就用
    ``limit_pause_minutes``（默认 5 分钟）。等待结束后自动继续。
    0.6.7：逐请求的固定间隔已经去掉，节拍由调用方的「按档位分批 + 批间
    等待」控制；这里只保留令牌桶这一层安全阈值。
    """
    last_err = None
    for attempt in range(_LIKE_RETRIES + 1):
        if flags.get("stop"):
            return None, last_err
        await _wait_until_resumed()
        await _wait_rest(flags, sub)
        wait = cooldown["until"] - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
            if time.monotonic() >= cooldown["until"]:
                flags["auto_pause_until"] = 0
                _update_account(
                    sub["account_id"], auto_pause_until=0,
                    auto_pause_text="", step="逐个吧点赞")
        await limiter.acquire()
        await sem.acquire()
        try:
            flags["agree_attempts"] = flags.get("agree_attempts", 0) + 1
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
            flags["auto_pause_until"] = 0
            _update_account(sub["account_id"], auto_pause_until=0,
                            auto_pause_text="")
            return resp, None
        last_err = err
        code = tieba_codes.parse_code(getattr(err, "code", None))
        if code != _RL_CODE:
            return resp, err
        wait_sec, parsed = _limit_wait_seconds(err, pause_minutes)
        flags["auto_pause_until"] = time.monotonic() + wait_sec
        cooldown["until"] = max(cooldown["until"], flags["auto_pause_until"])
        limiter.slow_down()
        if parsed:
            text = "百度提示 {} 后解除限制，自动暂停".format(
                _fmt_duration(wait_sec))
        else:
            text = "百度没有给出解除时间，按设置暂停 {} 分钟".format(
                _clamp_pause_minutes(pause_minutes))
        _update_account(
            sub["account_id"],
            auto_pause_until=int(time.time() * 1000) + wait_sec * 1000,
            auto_pause_text=text,
            step="被限流，自动暂停中")
        if attempt >= 1:
            return resp, err
    return None, last_err


async def _like_account_async(account, bduss):
    import aiotieba

    aid = account["id"]
    sub = _sub_for(aid)
    # M3a / D4：档位只在入口读一次；协程里不再读盘。
    cfg = _load()
    tier = tiers.get_like(min(
        tiers.clamp_like(cfg.get("level", tiers.LIKE_DEFAULT_LEVEL)),
        tiers.max_like_level()))
    like_concurrency = max(1, tier.concurrency)
    like_rate = _starting_rate(tier, cfg)
    rest_minutes = _rest_minutes(cfg)
    batch_interval = _batch_interval_seconds(cfg)
    cache_enabled = bool(cfg.get("cache_enabled", True))
    limiter = None
    flags = {"stop": False, "rate_limited": 0}
    _update_account(aid, tier_level=tier.level, tier_name=tier.name,
                    like_concurrency=like_concurrency,
                    batch_interval_seconds=int(batch_interval),
                    like_rate=like_rate)
    run_started = _now_ms()
    store = _load()
    flush = {"changes": 0}
    pause_minutes = _limit_pause_minutes(cfg)

    def _flush():
        try:
            # 运行期间用户可能在设置页改了档位/间隔/缓存/休息：落盘前先把
            # 这些设置从磁盘合回来，别让本轮开始时读到的旧值把新设置覆盖掉。
            fresh = _load()
            for key in ("level", "limit_pause_minutes", "cache_enabled",
                        "rest_minutes", "batch_interval_seconds"):
                if key in fresh:
                    store[key] = fresh[key]
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
            forums, invalid_skipped = invalid.filter_forums(forums)
            if invalid_skipped:
                sub["invalid_skipped"] = invalid_skipped
            _update_account(aid, total=len(forums), status="running",
                            step="逐个吧点赞")
            if not forums:
                _update_account(aid, status="done", step="没有关注的吧")
                return

            # L3：取帖与点赞分开限流，取帖不会把点赞的槽位占死。
            fetch_sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
            like_sem = asyncio.Semaphore(like_concurrency)
            # 0.6.3：burst=1，避免提升档位后开跑瞬间同时打出一批请求；
            # 速率本身也被 _LIKE_RATE_CAP 和当天学到的降速值压住。
            # 0.6.7：节拍改由「按档位分批 + 批间等待」控制。令牌桶的突发额度
            # 给到一批的并发数，批内才真能同时发 N 个；批间空闲足够回满。
            limiter = _RateLimiter(like_rate, float(like_concurrency))
            cooldown = {"until": 0.0}
            rng = random.SystemRandom()

            async def _wait_if_paused():
                while _pause.is_set() and not flags["stop"]:
                    await asyncio.sleep(0.2)

            async def one(forum):
                fname = str(getattr(forum, "fname", "") or "")
                if not fname:
                    _bump(aid, "checked")
                    return
                if flags["stop"]:
                    _bump(aid, "skipped")
                    return
                await _wait_if_paused()
                if flags["stop"]:
                    _bump(aid, "skipped")
                    return
                rec = _get_forum_record(store, aid, fname)
                if rec and rec.get("ok"):
                    _bump(aid, "skipped")
                    _bump(aid, "checked")
                    return
                tid = None
                title = ""
                threads = None
                if cache_enabled:
                    cached = _cache_tids(store, fname)
                    if cached:
                        tid = rng.choice(cached)
                if tid is None:
                    fetch_started = _now_ms()
                    try:
                        async with fetch_sem:
                            threads = await client.get_threads(fname, pn=1,
                                                               rn=_RN)
                    except Exception as exc:  # noqa: BLE001
                        info = _describe(exc)
                        if info["category"] == tieba_codes.CAT_FORUM_BANNED:
                            # 0.6.2：吧已查封/关闭，跳过、不算失败、不硬停。
                            _mark_invalid(fname)
                            _bump(aid, "skipped")
                            _bump(aid, "checked")
                            return
                        _add_failure(sub, fname, info)
                        _bump(aid, "failed")
                        _bump(aid, "checked")
                        return
                    finally:
                        _phase_add(sub, PHASE_FETCH, fetch_started)
                    terr = getattr(threads, "err", None)
                    if terr is not None:
                        info = _describe(terr)
                        if info["category"] == tieba_codes.CAT_FORUM_BANNED:
                            _mark_invalid(fname)
                            _bump(aid, "skipped")
                            _bump(aid, "checked")
                            return
                        _add_failure(sub, fname, info)
                        if info["category"] in (tieba_codes.CAT_ACCOUNT,
                                                tieba_codes.CAT_CAPTCHA):
                            # M3b 硬停：登录问题或验证码，继续点只会撞墙。
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
                    if cache_enabled:
                        candidates = _cache_candidates(
                            pool, _cache_used(store, fname))
                        if candidates:
                            _cache_set(store, fname, candidates)
                            tid = candidates[0]
                    if tid is None:
                        if cache_enabled:
                            _bump(aid, "skipped")
                            _bump(aid, "checked")
                            return
                        thread = rng.choice(pool)
                        tid = int(thread.tid)
                        title = str(getattr(thread, "title", "") or "")
                await _wait_if_paused()
                if flags["stop"]:
                    _bump(aid, "skipped")
                    _bump(aid, "checked")
                    return
                # 点赞接口对频率很敏感；0.6.7 的节拍在批与批之间（见下面的
                # 分批循环），这里只处理限流退避（冷却窗在信号量外面）。
                resp, err = await _agree_with_retry(
                    client, tid, like_sem, limiter, cooldown, flags, sub,
                    pause_minutes)
                if flags["stop"]:
                    _bump(aid, "skipped")
                    _bump(aid, "checked")
                    return
                if err is not None:
                    info = _describe(err)
                    if info["code"] == _RL_CODE:
                        # 自动暂停后仍被限流：结束本轮，不把后面的吧记成失败。
                        flags["rate_limited"] = flags.get(
                            "rate_limited", 0) + 1
                        flags["limit_stop"] = True
                        flags["stop"] = True
                        _update_account(
                            aid,
                            step="仍被限流，已自动暂停并结束本轮；稍后可重新开始",
                            auto_pause_until=0,
                            auto_pause_text="仍被限流，本轮已停止；稍后可重新开始")
                        _bump(aid, "skipped")
                        _bump(aid, "checked")
                        return
                    if info["category"] == tieba_codes.CAT_FORUM_BANNED:
                        if cache_enabled:
                            _cache_remove(store, fname, tid)
                            _cache_mark_used(store, fname, tid)
                        _mark_invalid(fname)
                        _bump(aid, "skipped")
                        _bump(aid, "checked")
                        return
                    if cache_enabled and info["category"] not in (
                            tieba_codes.CAT_NETWORK, tieba_codes.CAT_ACCOUNT,
                            tieba_codes.CAT_CAPTCHA):
                        _cache_remove(store, fname, tid)
                        _cache_mark_used(store, fname, tid)
                    _add_failure(sub, fname, info)
                    _record(aid, fname, False, tid, title,
                            info["code"], info["reason"],
                            info["advice"], info["detail"],
                            info["raw"])
                    if info["category"] in (tieba_codes.CAT_ACCOUNT,
                                            tieba_codes.CAT_CAPTCHA):
                        flags["stop"] = True
                    _bump(aid, "failed")
                    _bump(aid, "checked")
                    return
                if cache_enabled:
                    _cache_remove(store, fname, tid)
                    _cache_mark_used(store, fname, tid)
                _record(aid, fname, True, tid, title)
                _bump(aid, "success")
                _bump(aid, "checked")
                flags["likes_done"] = flags.get("likes_done", 0) + 1
                if (rest_minutes > 0 and _REST_EVERY > 0
                        and flags["likes_done"] % _REST_EVERY == 0):
                    rest_sec = _rest_seconds(rest_minutes)
                    flags["rest_until"] = time.monotonic() + rest_sec
                    flags["rest_text"] = "每 {} 个赞休息 {} 分钟".format(
                        _REST_EVERY, rest_minutes)
                    _update_account(
                        aid,
                        rest_until=int(time.time() * 1000) + rest_sec * 1000,
                        rest_text=flags["rest_text"],
                        step="点赞休息中")

            # 0.6.7：按档位并发分批。当天已经点过的吧在本地就能判定，
            # 先剔出去，免得整批都是「跳过」还傻等一个批间隔。
            pending = []
            for forum in forums:
                fname = str(getattr(forum, "fname", "") or "")
                rec = _get_forum_record(store, aid, fname) if fname else None
                if rec and rec.get("ok"):
                    _bump(aid, "skipped")
                    _bump(aid, "checked")
                else:
                    pending.append(forum)
            like_started = _now_ms()
            try:
                for start in range(0, len(pending), like_concurrency):
                    if flags["stop"]:
                        for _forum in pending[start:]:
                            _bump(aid, "skipped")
                        break
                    await _wait_if_paused()
                    batch = pending[start:start + like_concurrency]
                    agrees_before = flags.get("agree_attempts", 0)
                    await asyncio.gather(*(one(f) for f in batch))
                    # 只有这一批真的发过点赞请求，才需要为下一批等间隔；
                    # 全是本地跳过/取帖失败时没必要空等。
                    if (start + like_concurrency < len(pending)
                            and not flags["stop"]
                            and flags.get("agree_attempts", 0) > agrees_before):
                        await asyncio.sleep(batch_interval)
            finally:
                _phase_add(sub, PHASE_LIKE, like_started)
            if flags.get("limit_stop"):
                _update_account(
                    aid, status="done",
                    step="被限流，已自动暂停并结束本轮；稍后可重新开始")
            else:
                _update_account(aid, status="done", step="随机点赞完成")
    except Exception as exc:  # noqa: BLE001
        _update_account(aid, status="error", step="随机点赞异常",
                        error={"detail": "{}: {}".format(
                            type(exc).__name__, exc)})
    finally:
        # 当天点过的吧重跑会直接 skip，只有隔天首跑的数据才能证明提速；
        # 所以分段耗时必须落进 tiebasign_like.json，不能只在内存里。
        # 0.6.4：AIMD——整轮干净就 +1 次/秒，撞过 3280004 就沿用减半后的速率。
        attempts = (int(sub.get("success") or 0)
                    + int(sub.get("failed") or 0))
        if limiter is not None and attempts > 0:
            if flags.get("rate_limited"):
                new_rate = max(_MIN_LIKE_RATE, limiter.rate)
            else:
                new_rate = min(_like_rate_for_tier(tier),
                               (limiter.rate or like_rate) + 1.0)
            store["rate_limit"] = {
                "rate": round(new_rate, 2),
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "limited": bool(flags.get("rate_limited")),
            }
        _note_last_run(store, sub, _now_ms() - run_started)
        _flush()
