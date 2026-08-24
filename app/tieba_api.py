# -*- coding: utf-8 -*-
"""Baidu account validation via aiotieba (async loop in a worker thread).

M2.2.2 needs only "is this BDUSS valid?". Categories follow PRD v1.0:
  账号失效 (server rejected / bad credential) vs 网络异常 (transport failed).
Signing-related codes (160002 已签到 / 340006 etc.) are handled in M2.3.
"""

import asyncio
import re
import threading

_loop = None
_loop_lock = threading.Lock()

BDUSS_RE = re.compile(r"^[A-Za-z0-9=+/_-]{30,240}$")
# CookieManager 取到的值可能带 URL 编码等差异，且最终有 aiotieba 真机验证
# 兜底，所以 WebView 登录路径只做宽松检查（长度 + 无空白/非 ASCII）。
BDUSS_RE_LOOSE = re.compile(r"^[\x21-\x7e]{30,300}$")


def format_check(bduss, strict=True):
    if not isinstance(bduss, str):
        return "BDUSS 必须是字符串"
    bduss = bduss.strip()
    if not bduss:
        return "BDUSS 不能为空"
    if len(bduss) < 30:
        return "BDUSS 太短（{} 字符），不是有效格式".format(len(bduss))
    if len(bduss) > (240 if strict else 300):
        return "BDUSS 太长（{} 字符），请确认粘贴的是 BDUSS".format(len(bduss))
    if strict:
        if not BDUSS_RE.match(bduss):
            return "BDUSS 含非法字符，请确认粘贴完整"
    else:
        if not BDUSS_RE_LOOSE.match(bduss):
            return "BDUSS 含空白或非 ASCII 字符（长度 {}），Cookie 可能不完整".format(
                len(bduss))
    return None


def _ensure_loop():
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()

            def _run():
                asyncio.set_event_loop(_loop)
                _loop.run_forever()

            t = threading.Thread(target=_run, name="tieba-async-loop",
                                 daemon=True)
            t.start()
    return _loop


async def _validate_async(bduss):
    import aiotieba

    async with aiotieba.Client(bduss) as client:
        user = await client.get_self_info()
    err = getattr(user, "err", None)
    if err is not None:
        code = getattr(err, "code", None)
        msg = getattr(err, "msg", None) or str(err)
        return {
            "ok": False,
            "category": "账号失效",
            "detail": "百度返回错误 code={} msg={}".format(code, msg),
        }
    user_id = getattr(user, "user_id", None)
    user_name = getattr(user, "user_name", None)
    nick_name = getattr(user, "nick_name", None)
    if not user_id and not user_name:
        return {
            "ok": False,
            "category": "账号失效",
            "detail": "百度未返回用户信息，BDUSS 可能已失效",
        }
    return {
        "ok": True,
        "user_id": str(user_id or ""),
        "user_name": str(user_name or ""),
        "nick_name": str(nick_name or ""),
        "display_name": str(nick_name or user_name or "贴吧用户"),
    }


def validate_bduss(bduss):
    """Blocking call from any HTTP handler thread. Returns dict."""
    fmt_err = format_check(bduss)
    if fmt_err:
        return {"ok": False, "category": "格式错误", "detail": fmt_err}

    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(_validate_async(bduss), loop)
    try:
        return future.result(timeout=90)
    except asyncio.TimeoutError:
        return {"ok": False, "category": "网络异常",
                "detail": "验证超时（90s），请检查网络后重试"}
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        text = str(exc)
        # transport-level problems: timeout / DNS / TLS / connection
        if any(k in (name + " " + text)
               for k in ("Timeout", "ClientConnector", "ClientOSError",
                         "ClientConnectorError", "Connection", "SSLError",
                         "socket", "OSError")):
            return {"ok": False, "category": "网络异常",
                    "detail": "{}: {}".format(name, text[:300])}
        return {"ok": False, "category": "风控其他",
                "detail": "{}: {}".format(name, text[:300])}
