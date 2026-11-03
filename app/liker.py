# -*- coding: utf-8 -*-
"""每日随机点赞（v0.5.0 新增）。

需求（工单 #4 追加）：「随机点赞帖子，但设定每日点赞一个」。
实现口径：

* **每个账号每天最多 1 次**（成功才算；失败也记当天已尝试，避免反复重试）。
* 从该账号**关注吧**里随机挑一个吧，再从该吧首页最新主题帖里随机挑一个
  非置顶、非屏蔽帖，调用 aiotieba 4.7.1 的 ``Client.agree(tid)`` 点主楼赞。
* 点赞失败只是 ``warning``，不影响签到结果；不弹系统通知。
* 记录落在 ``<ANDROID_PRIVATE>/tiebasign_like.json``，不含 BDUSS。

为什么不放在签到循环里逐吧点赞：用户明确只要「每日一个」，一次签到跑几百个吧
时逐吧点赞会变成批量刷赞，风险与需求都不对。
"""

import json
import os
import random
import threading
import time

import redact
import tieba_codes

_DATA_FILENAME = "tiebasign_like.json"
_DEFAULT = {"enabled": True, "accounts": {}}
_lock = threading.RLock()
_cache = None
_MAX_FORUMS_TO_TRY = 3
_RN = 30


def _data_path():
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, _DATA_FILENAME)


def _load():
    global _cache
    with _lock:
        if _cache is not None:
            return json.loads(json.dumps(_cache))
        data = dict(_DEFAULT)
        try:
            with open(_data_path(), "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data["enabled"] = bool(loaded.get("enabled", True))
                accts = loaded.get("accounts")
                if isinstance(accts, dict):
                    data["accounts"] = {str(k): v for k, v in accts.items()
                                        if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001 - 坏文件按默认值起步
            pass
        _cache = data
        return json.loads(json.dumps(data))


def _save(data):
    global _cache
    with _lock:
        _cache = json.loads(json.dumps(data))
        path = _data_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def get_settings():
    data = _load()
    accounts = []
    for aid, rec in sorted(data["accounts"].items(),
                           key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        item = dict(rec)
        item["account_id"] = int(aid) if str(aid).isdigit() else aid
        accounts.append(item)
    return {"enabled": bool(data["enabled"]), "accounts": accounts}


def save_settings(payload):
    data = _load()
    if "enabled" in payload:
        data["enabled"] = bool(payload["enabled"])
    _save(data)
    return get_settings()


def last_results():
    return get_settings()["accounts"]


def _today():
    return time.strftime("%Y-%m-%d")


def _describe(err):
    code = tieba_codes.parse_code(getattr(err, "code", None))
    msg = str(getattr(err, "msg", None) or err).strip()
    reason = tieba_codes.reason(code)
    if code is None:
        detail = msg or "未知错误（异常没有错误码）"
    elif reason:
        detail = "{}（{}）".format(
            msg or "百度未返回错误原因", tieba_codes.explain(code))
    else:
        detail = "{}（错误码 {}，未收录）".format(
            msg or "百度未返回错误原因", code)
    return code, reason, detail


def _record(account, ok, fname=None, tid=None, title=None,
            code=None, reason=None, detail=None):
    data = _load()
    data["accounts"][str(account["id"])] = {
        "date": _today(),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": bool(ok),
        "account_name": account.get("name") or account.get("mask") or "",
        "account_mask": account.get("mask") or "",
        "fname": redact.scrub(fname or ""),
        "tid": int(tid) if tid else None,
        "title": redact.scrub(title or "")[:80],
        "code": code,
        "reason": reason,
        "detail": redact.scrub(detail or ""),
    }
    _save(data)
    return data["accounts"][str(account["id"])]


def _already_done(account):
    rec = _load()["accounts"].get(str(account["id"]))
    if not rec:
        return None
    if rec.get("date") != _today():
        return None
    return rec


async def maybe_like_async(client, account, forums, secret=None):
    """在签到流程末尾调用；返回 ``{"status": ...}``，永不抛异常给主流程。

    forums: ``get_self_follow_forums`` 返回的 SelfFollowForum 列表。
    """
    try:
        import autosign
        if not autosign.get_settings().get("daily_like", True):
            return {"status": "disabled"}
    except Exception:  # noqa: BLE001 - 设置读不到就按默认开启
        pass

    rec = _already_done(account)
    if rec is not None:
        return {"status": "done",
                "detail": "今天已经点过赞了（{}）".format(
                    rec.get("ts") or rec.get("date") or "")}

    candidates = [f for f in (forums or [])
                  if str(getattr(f, "fname", "") or "").strip()]
    if not candidates:
        result = {"status": "error", "category": "未关注",
                  "code": None, "reason": None,
                  "detail": "没有可用的关注吧，无法随机点赞"}
        _record(account, False, detail=result["detail"])
        return result

    rng = random.SystemRandom()
    rng.shuffle(candidates)
    last_detail = "没有找到可点赞的主题帖"
    for forum in candidates[:_MAX_FORUMS_TO_TRY]:
        fname = str(forum.fname)
        try:
            threads = await client.get_threads(fname, pn=1, rn=_RN)
        except Exception as exc:  # noqa: BLE001
            last_detail = "{}: {}".format(type(exc).__name__,
                                          redact.scrub(str(exc), secret))
            continue
        terr = getattr(threads, "err", None)
        if terr is not None:
            code, reason, detail = _describe(terr)
            last_detail = redact.scrub(detail, secret)
            continue

        pool = [t for t in threads
                if int(getattr(t, "tid", 0) or 0)
                and not bool(getattr(t, "is_top", False))
                and not bool(getattr(t, "is_hide", False))]
        if not pool:
            continue
        thread = rng.choice(pool)
        tid = int(thread.tid)
        title = str(getattr(thread, "title", "") or "")
        try:
            resp = await client.agree(tid)
        except Exception as exc:  # noqa: BLE001
            detail = redact.scrub("{}: {}".format(type(exc).__name__, exc),
                                  secret)
            result = {"status": "error", "category": "网络异常",
                      "code": None, "reason": None, "detail": detail}
            _record(account, False, fname, tid, title, detail=detail)
            return result

        err = getattr(resp, "err", None)
        if err is None:
            rec = _record(account, True, fname, tid, title)
            return {"status": "success", "fname": fname, "tid": tid,
                    "title": redact.scrub(title)[:80], "ts": rec["ts"]}

        code, reason, detail = _describe(err)
        detail = redact.scrub(detail, secret)
        result = {"status": "error",
                  "category": tieba_codes.category_for_code(code) or
                  "未知错误",
                  "code": code, "reason": reason, "detail": detail,
                  "fname": fname, "tid": tid}
        _record(account, False, fname, tid, title, code, reason, detail)
        return result

    result = {"status": "error", "category": "未知错误",
              "code": None, "reason": None,
              "detail": redact.scrub(last_detail, secret)}
    _record(account, False, detail=result["detail"])
    return result
