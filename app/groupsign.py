# -*- coding: utf-8 -*-
"""吧群（聊天频道）签到：在指定群发送一条自定义消息。

用户确认的机制（2026-11-03）：
  * 不是贴吧的“群签到按钮”，而是在指定吧群里发送一句话，例如
    ``@群助手/领取今日福利``，由群里的机器人/管理员完成签到/领福利。
  * 用户要能自行修改发送的话。
  * **只要检测到发送成功，当天就不再发**；发送失败可重试，但每个群每天
    最多尝试 ``_MAX_ATTEMPTS`` 次，避免刷屏。

实现边界：
  * 群列表按账号分开（一个账号只发自己配置的群），和自动水贴的“每账号一组”
    一致；避免多账号把同一句话发进同一个群。
  * ``send_chatroom_msg`` 返回成功（``err is None``）即视为发送成功；机器人
    是否回复不在本模块的判定范围内——没有读取群聊天记录的可靠接口。
  * 只有发送成功才写 ``ok``；失败只记 warning，不影响吧签到。
  * 数据文件 ``<ANDROID_PRIVATE>/tiebasign_groupsign.json``，不含 BDUSS。

群分享链接形如：
  https://tieba.baidu.com/mo/q/wise-main-share/chatShare?chatroom_id=8184512479...
只取 ``chatroom_id``，再用公开的 ``/mo/q/chat/getCardInfo`` 补全群名与 fid。
"""

import asyncio
import json
import os
import re
import ssl
import threading
import time
import urllib.request

import redact
import tieba_codes
import tieba_api

try:
    # 优先复用 aiotieba 已被本项目补丁修好的 SSL 上下文（真机上已验证可用）。
    from aiotieba.core.http import _SSL_CONTEXT as _SSL_CONTEXT
except Exception:  # noqa: BLE001 - 桌面/未打补丁时退回 certifi
    try:
        import certifi
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        _SSL_CONTEXT = ssl.create_default_context()

_DATA_FILENAME = "tiebasign_groupsign.json"
_DEFAULT_TEXT = "@群助手/领取今日福利"
_MAX_ATTEMPTS = 3
_CARD_URL = "https://tieba.baidu.com/mo/q/chat/getCardInfo?chatroom_id={}"
_UA = ("Mozilla/5.0 (Linux; Android 12; Pixel 6) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0.0.0 Mobile Safari/537.36")
_CHATROOM_RE = re.compile(r"(?:^|[?&])chatroom_id=(\d+)")

_DEFAULT = {"text": _DEFAULT_TEXT, "accounts": {}}
_lock = threading.RLock()
_cache = None


def _patch_blcp_ssl():
    """让 aiotieba 的 BLCP 连接也用 certifi 证书。

    Android 上 ``ssl.create_default_context()`` 可能找不到系统 CA，BLCP 连接
    失败时 send_chatroom_msg 只会抛一个没有错误码的异常（用户看到的「百度没给
    错误码」）。aiotieba 的 HTTP 层已经被 p4a 补丁修过，但 BLCP 是裸 TCP+SSL，
    这里在进程内把它的 ssl 引用换成同一个上下文。
    """
    try:
        import types
        import aiotieba.core.blcp as blcp
        if getattr(blcp, "_teiba_ssl_patched", False):
            return
        blcp.ssl = types.SimpleNamespace(
            create_default_context=lambda *a, **k: _SSL_CONTEXT)
        blcp._teiba_ssl_patched = True
    except Exception:  # noqa: BLE001 - 补丁失败不阻塞其他群
        pass


def _log(msg):
    try:
        path = os.path.join(os.environ.get("ANDROID_PRIVATE") or os.getcwd(),
                            "groupsign.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("[{}] {}\n".format(
                time.strftime("%m-%d %H:%M:%S"), redact.scrub(str(msg))))
            fh.flush()
    except Exception:  # noqa: BLE001
        pass


def _data_path():
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, _DATA_FILENAME)


def _clean_text(value, fallback=""):
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:200]


def _clean_group(raw):
    if not isinstance(raw, dict):
        return None
    try:
        chatroom_id = int(raw.get("chatroom_id") or 0)
        fid = int(raw.get("fid") or 0)
    except (TypeError, ValueError):
        return None
    if chatroom_id <= 0 or fid <= 0:
        return None
    return {
        "chatroom_id": chatroom_id,
        "fid": fid,
        "name": redact.scrub(str(raw.get("name") or "吧群")),
        "forum_name": redact.scrub(str(raw.get("forum_name") or "")),
        "text": _clean_text(raw.get("text")),
    }


def _load():
    global _cache
    with _lock:
        if _cache is not None:
            return json.loads(json.dumps(_cache))
        data = {"text": _DEFAULT_TEXT, "accounts": {}}
        try:
            with open(_data_path(), "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data["text"] = _clean_text(loaded.get("text"), _DEFAULT_TEXT)
                accounts = loaded.get("accounts")
                if isinstance(accounts, dict):
                    for aid, acct in accounts.items():
                        if not isinstance(acct, dict):
                            continue
                        groups = []
                        seen = set()
                        for raw in acct.get("groups") or []:
                            group = _clean_group(raw)
                            if group and group["chatroom_id"] not in seen:
                                seen.add(group["chatroom_id"])
                                groups.append(group)
                        runs = acct.get("runs")
                        if not isinstance(runs, dict):
                            runs = {}
                        data["accounts"][str(aid)] = {
                            "groups": groups,
                            "runs": {str(k): v for k, v in runs.items()
                                     if isinstance(v, dict)},
                        }
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


def _account(data, account_id):
    aid = str(account_id)
    acct = data["accounts"].get(aid)
    if acct is None:
        acct = {"groups": [], "runs": {}}
        data["accounts"][aid] = acct
    return acct


def get_settings():
    data = _load()
    accounts = []
    for aid, acct in sorted(data["accounts"].items(),
                            key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        groups = []
        for group in acct["groups"]:
            run = acct["runs"].get(str(group["chatroom_id"])) or {}
            item = dict(group)
            item["last"] = {
                "date": run.get("date"),
                "ok": bool(run.get("ok")),
                "attempts": int(run.get("attempts") or 0),
                "ts": run.get("ts") or "",
                "detail": run.get("detail") or "",
            }
            groups.append(item)
        accounts.append({
            "account_id": int(aid) if str(aid).isdigit() else aid,
            "groups": groups,
        })
    return {"text": data["text"], "accounts": accounts}


def save_settings(payload):
    data = _load()
    if "text" in payload:
        text = _clean_text(payload.get("text"), "")
        if not text:
            raise ValueError("发送内容不能为空")
        data["text"] = text
    _save(data)
    return get_settings()


def _extract_chatroom_id(value):
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    m = _CHATROOM_RE.search(text)
    return int(m.group(1)) if m else None


def _fetch_card(chatroom_id):
    """公开接口取群名/所属吧；失败返回 (None, 原因)。"""
    req = urllib.request.Request(_CARD_URL.format(chatroom_id),
                                 headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15,
                                    context=_SSL_CONTEXT) as resp:
            raw = resp.read()
        payload = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - 网络/证书/解析失败都给人话
        _log("getCardInfo failed chatroom_id={} {}: {}".format(
            chatroom_id, type(exc).__name__, exc))
        return None, ("读取群信息失败（{}），请重试；如果一直失败，"
                      "可以在下面手动填写吧ID/群名".format(
                          type(exc).__name__))
    if not isinstance(payload, dict) or int(payload.get("no") or 0) != 0:
        reason = str(payload.get("error") or "百度没有返回这个群的信息") \
            if isinstance(payload, dict) else "百度没有返回这个群的信息"
        return None, "这个群不存在或已解散：{}".format(reason)
    data = payload.get("data") or {}
    try:
        return {
            "chatroom_id": int(data.get("id") or chatroom_id),
            "fid": int(data.get("forum_id") or 0),
            "name": redact.scrub(str(data.get("name") or "吧群")),
            "forum_name": redact.scrub(str(data.get("forum_name") or "")),
            "text": "",
        }, None
    except (TypeError, ValueError):
        return None, "群信息格式异常，无法添加"


def add_group(account_id, url=None, text=None, chatroom_id=None,
              fid=None, name=None):
    """添加群。优先用分享链接自动取群信息；也可手动给 chatroom_id + fid。

    手动模式是为 Android 上 ``getCardInfo`` 证书/网络失败准备的兜底：
    fid（吧ID）是 ``send_chatroom_msg`` 必需的，群名只用于展示。
    """
    cid = _extract_chatroom_id(chatroom_id) or _extract_chatroom_id(url)
    if not cid:
        return None, "链接里没有 chatroom_id，请粘贴群分享链接或手动填写"
    try:
        manual_fid = int(fid) if fid not in (None, "") else 0
    except (TypeError, ValueError):
        manual_fid = 0
    if manual_fid > 0:
        group = {
            "chatroom_id": cid,
            "fid": manual_fid,
            "name": redact.scrub(str(name or "吧群")),
            "forum_name": "",
            "text": "",
        }
    else:
        group, err = _fetch_card(cid)
        if err:
            return None, err
    if not group.get("fid"):
        return None, "这个群没有关联的吧，暂时无法发送"
    group["text"] = _clean_text(text)
    data = _load()
    acct = _account(data, account_id)
    if any(int(g["chatroom_id"]) == cid for g in acct["groups"]):
        return None, "这个群已经添加过了"
    acct["groups"].append(group)
    _save(data)
    return group, None


def delete_group(account_id, chatroom_id):
    data = _load()
    acct = _account(data, account_id)
    before = len(acct["groups"])
    acct["groups"] = [g for g in acct["groups"]
                      if int(g["chatroom_id"]) != int(chatroom_id)]
    acct["runs"].pop(str(chatroom_id), None)
    changed = len(acct["groups"]) != before
    if changed:
        _save(data)
    return changed


def update_group_text(account_id, chatroom_id, text):
    text = _clean_text(text)
    if not text:
        return None, "发送内容不能为空"
    data = _load()
    acct = _account(data, account_id)
    for group in acct["groups"]:
        if int(group["chatroom_id"]) == int(chatroom_id):
            group["text"] = text
            _save(data)
            return group, None
    return None, "群不存在"


def _today():
    return time.strftime("%Y-%m-%d")


def _describe(err):
    code = tieba_codes.parse_code(getattr(err, "code", None))
    raw = redact.scrub(str(getattr(err, "msg", None) or err).strip())
    reason = tieba_codes.reason(code)
    advice = tieba_codes.advice(code) or (
        "过一会儿再试；如果一直失败，把这份签到报告发给开发者")
    if isinstance(err, Exception) and not hasattr(err, "code"):
        text = "{} {}".format(type(err).__name__, err).lower()
        if any(k in text for k in ("timeout", "connection", "ssl",
                                   "socket", "oserror", "network")):
            return None, None, "检查网络后重试", \
                "网络连接出了问题，这条群消息没有发出去", raw
    if code is None:
        text = str(err or "").strip()
        detail = "群消息没有发出去（{}：{}）".format(
            type(err).__name__, redact.scrub(text)[:120] or "没有返回原因")
    elif reason:
        detail = reason
    else:
        detail = "百度没有给出具体原因，这条群消息没有发出去"
    return code, reason, advice, detail, raw


def _record_run(account_id, chatroom_id, ok, detail="", attempts=1):
    data = _load()
    acct = _account(data, account_id)
    acct["runs"][str(chatroom_id)] = {
        "date": _today(),
        "ok": bool(ok),
        "attempts": int(attempts),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "detail": redact.scrub(detail or ""),
    }
    _save(data)
    return data["accounts"][str(account_id)]["runs"][str(chatroom_id)]


async def run_account_async(client, account, secret=None):
    """在签到流程末尾调用：把该账号配置的群各发一次。

    返回 {"status": "empty|done", "sent", "skipped", "failed",
          "total", "details", "warnings"}；不抛异常给签到主流程。
    """
    data = _load()
    _patch_blcp_ssl()
    aid = str(account["id"])
    acct = data["accounts"].get(aid) or {}
    groups = list(acct.get("groups") or [])
    if not groups:
        return {"status": "empty", "sent": 0, "skipped": 0,
                "failed": 0, "total": 0, "details": [], "warnings": []}

    today = _today()
    sent = skipped = failed = 0
    details = []
    warnings = []
    for idx, group in enumerate(groups):
        cid = int(group["chatroom_id"])
        fid = int(group["fid"])
        name = group.get("name") or "吧群"
        text = _clean_text(group.get("text"), data["text"])
        data = _load()
        run = ((data["accounts"].get(aid) or {}).get("runs") or {}).get(
            str(cid)) or {}
        attempts = int(run.get("attempts") or 0) if run.get("date") == today else 0
        if run.get("date") == today and run.get("ok"):
            skipped += 1
            details.append({"chatroom_id": cid, "name": name,
                            "status": "skipped",
                            "detail": "今天已经发送成功，不再重复发送"})
            continue
        if attempts >= _MAX_ATTEMPTS:
            skipped += 1
            details.append({"chatroom_id": cid, "name": name,
                            "status": "skipped",
                            "detail": "今天已经尝试 {} 次，明天再试".format(
                                attempts)})
            continue
        if not text:
            failed += 1
            details.append({"chatroom_id": cid, "name": name,
                            "status": "error",
                            "detail": "发送内容为空"})
            continue

        attempts += 1
        _record_run(aid, cid, False, "发送中", attempts)

        # 先尝试加入（已加入时通常也返回成功/可忽略错误）；加入失败不阻止发送。
        jerr = None
        try:
            joined = await client.join_chatroom(cid)
            jerr = getattr(joined, "err", None)
        except Exception as exc:  # noqa: BLE001
            jerr = exc
        if jerr is not None:
            code, reason, advice, detail, raw = _describe(jerr)
            _log("join failed cid={} code={} detail={} raw={}".format(
                cid, code, detail, raw))
            if tieba_codes.category_for_code(code) == tieba_codes.CAT_ACCOUNT:
                failed += 1
                detail = "账号登录状态有问题，剩下的群今天不再发送"
                _record_run(aid, cid, False, detail, attempts)
                warnings.append({
                    "forum": "群签到：{}".format(name),
                    "category": tieba_codes.CAT_ACCOUNT,
                    "code": code, "reason": reason, "advice": advice,
                    "detail": detail, "raw": raw,
                })
                details.append({"chatroom_id": cid, "name": name,
                                "status": "error", "detail": detail})
                skipped += len(groups) - idx - 1
                break

        try:
            resp = await client.send_chatroom_msg(cid, fid, text)
        except Exception as exc:  # noqa: BLE001
            code, reason, advice, detail, raw = _describe(exc)
            failed += 1
            _record_run(aid, cid, False, detail, attempts)
            _log("send exception cid={} code={} detail={} raw={}".format(
                cid, code, detail, raw))
            warnings.append({
                "forum": "群签到：{}".format(name),
                "category": tieba_codes.CAT_NETWORK,
                "code": code, "reason": reason, "advice": advice,
                "detail": detail, "raw": raw,
            })
            details.append({"chatroom_id": cid, "name": name,
                            "status": "error", "detail": detail})
            continue

        err = getattr(resp, "err", None)
        if err is None:
            sent += 1
            _record_run(aid, cid, True, "发送成功", attempts)
            details.append({"chatroom_id": cid, "name": name,
                            "status": "success", "detail": "发送成功"})
            continue

        code, reason, advice, detail, raw = _describe(err)
        category = tieba_codes.category_for_code(code) or tieba_codes.CAT_UNKNOWN
        failed += 1
        _record_run(aid, cid, False, detail, attempts)
        _log("send failed cid={} code={} detail={} raw={}".format(
            cid, code, detail, raw))
        warnings.append({
            "forum": "群签到：{}".format(name),
            "category": category, "code": code, "reason": reason,
            "advice": advice, "detail": detail, "raw": raw,
        })
        details.append({"chatroom_id": cid, "name": name,
                        "status": "error", "detail": detail})
        if category == tieba_codes.CAT_ACCOUNT:
            skipped += len(groups) - idx - 1
            break

    return {"status": "done", "sent": sent, "skipped": skipped,
            "failed": failed, "total": len(groups),
            "details": details, "warnings": warnings}


# --------------------------------------------------------------------------
# 独立模块：签到页上的「开始吧群签到」按钮
# --------------------------------------------------------------------------

_state = None
_run_counter = 0


def _new_state(run_id, pairs):
    accounts = []
    for account, _bduss in pairs:
        accounts.append({
            "account_id": account["id"],
            "account_name": account.get("name") or account.get("mask") or "",
            "account_mask": account.get("mask") or "",
            "status": "pending",
            "step": "等待中",
            "total": 0,
            "sent": 0,
            "skipped": 0,
            "failed": 0,
            "details": [],
            "warnings": [],
            "error": None,
        })
    return {"run_id": run_id, "status": "starting", "step": "准备中",
            "mode": "groupsign", "account_total": len(accounts),
            "account_done": 0, "accounts": accounts,
            "started_at": int(time.time() * 1000), "finished_at": None}


def get_state():
    with _lock:
        if _state is None:
            return {"status": "idle"}
        snap = dict(_state)
        snap["accounts"] = []
        for sub in _state["accounts"]:
            item = dict(sub)
            item["details"] = list(sub["details"])
            item["warnings"] = list(sub["warnings"])
            snap["accounts"].append(item)
        return snap


def _update_account(account_id, **kwargs):
    with _lock:
        if _state is None:
            return
        for sub in _state["accounts"]:
            if sub["account_id"] == account_id:
                sub.update(kwargs)
                return


async def _run_one(account, bduss):
    import aiotieba
    async with aiotieba.Client(bduss) as client:
        return await run_account_async(client, account, bduss)


def start(pairs):
    """pairs: [(account_dict, bduss_str), ...]；独立于吧签到执行。"""
    global _state, _run_counter
    with _lock:
        if _state is not None and _state["status"] in ("starting", "running"):
            return None, "吧群签到正在进行，请等待完成"
        if not pairs:
            return None, "未选择要执行吧群签到的账号"
        _run_counter += 1
        run_id = "gs{:d}".format(_run_counter)
        _state = _new_state(run_id, pairs)

    def _worker():
        loop = tieba_api._ensure_loop()
        for idx, (account, bduss) in enumerate(pairs):
            sub = _state["accounts"][idx] if _state is not None else None
            if sub is None:
                break
            _update_account(account["id"], status="running", step="准备中")
            future = asyncio.run_coroutine_threadsafe(
                _run_one(account, bduss), loop)
            try:
                result = future.result(timeout=1800)
                _update_account(
                    account["id"], status=(
                        "done" if result.get("status") != "error" else "error"),
                    step="完成", total=result.get("total") or 0,
                    sent=result.get("sent") or 0,
                    skipped=result.get("skipped") or 0,
                    failed=result.get("failed") or 0,
                    details=result.get("details") or [],
                    warnings=result.get("warnings") or [])
            except asyncio.TimeoutError:
                _update_account(account["id"], status="error",
                                step="超时",
                                error={"detail": "吧群签到超过 30 分钟仍未完成"})
            except Exception as exc:  # noqa: BLE001
                _update_account(account["id"], status="error",
                                step="异常",
                                error={"detail": "{}: {}".format(
                                    type(exc).__name__, exc)})
            with _lock:
                if _state is not None:
                    _state["account_done"] += 1
                    _state["step"] = "账号进度 {}/{}".format(
                        _state["account_done"], _state["account_total"])
        with _lock:
            if _state is not None:
                _state["status"] = "done"
                _state["step"] = "吧群签到完成"
                _state["finished_at"] = int(time.time() * 1000)

    threading.Thread(target=_worker, name="tieba-groupsign-worker",
                     daemon=True).start()
    return run_id, None
