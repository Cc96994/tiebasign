# -*- coding: utf-8 -*-
"""M2.2.3 Baidu WebView login -> extract BDUSS from CookieManager.

CONTAINER WORKAROUND (Zhuoyitong / HarmonyOS NEXT):
  pyjnius `autoclass("org.kivy.android.PythonActivity")` fails with
  ClassNotFoundException because JNI FindClass cannot see app classes there.
  All app-class access therefore goes through a reflection bridge built from
  BOOT-class objects only:
      ActivityThread.currentApplication().getClassLoader()
          .loadClass("org.kivy.android.PythonActivity")
  - navigate : Class.getMethods() -> find static loadUrl(String) -> invoke
  - read WebView URL: NOT USED on this container. Doing it on the UI thread
    needs a Python-implemented java.lang.Runnable, which pyjnius registers via
    org.jnius.NativeInvocationHandler; FindClass cannot see that app class in
    Zhuoyitong (ClassNotFoundException -> flow failure), so the flow polls
    CookieManager directly after navigating and never touches WebView from
    Python.

Flow (Python background thread):
  1. bridge self-check (caller shows the error if it fails)
  2. clear WebView cookies (a previous login leaves a stale BDUSS behind)
  3. navigate the WebView to https://passport.baidu.com/v2/?login
  4. poll android.webkit.CookieManager for a BDUSS cookie every 0.8 s
  5. format-check + aiotieba get_self_info validate the BDUSS
  6. Android Keystore AES-GCM encrypt -> storage.add_account()
  7. navigate back to /weblogin/done?ok=..., then clear WebView cookies

Reopening /weblogin cancels any stale flow (token + cancel event).

Full BDUSS is never logged and never rendered; only its length is printed.
"""

import re
import threading
import time
from urllib.parse import unquote, urlencode

from jnius import autoclass

import keystore_crypto
import storage
import tieba_api


# --------------------------------------------------------------------------
# pyjnius 1.7.0 对 java.lang.reflect.Field/Method 返回的是受限代理类
# （reflect.Field 只声明 getName/toString/getType/getModifiers，reflect.Method
# 只声明 getName/toString/getParameterTypes/getReturnType/getModifiers/
# isVarArgs/isDefault），直接调用 field.get / method.invoke 会 AttributeError。
# 解法：在本模块第一次使用反射前，给这两个代理类动态补上缺失的
# JavaMethod 描述符。代理实例在创建时（instanciate_from -> resolve_methods）
# 会遍历类 __dict__，所以先补丁再拿 Field/Method 即可正常调用。
# --------------------------------------------------------------------------

def _patch_reflect_proxies():
    """Add Field.get / Field.setAccessible / Method.invoke /
    Method.setAccessible to pyjnius' limited reflection proxies."""
    import jnius.reflect as _jreflect
    from jnius import JavaMethod
    if "get" not in _jreflect.Field.__dict__:
        _jreflect.Field.get = JavaMethod(
            "(Ljava/lang/Object;)Ljava/lang/Object;")
    if "setAccessible" not in _jreflect.Field.__dict__:
        _jreflect.Field.setAccessible = JavaMethod("(Z)V")
    if "invoke" not in _jreflect.Method.__dict__:
        _jreflect.Method.invoke = JavaMethod(
            "(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;")
    if "setAccessible" not in _jreflect.Method.__dict__:
        _jreflect.Method.setAccessible = JavaMethod("(Z)V")


_patch_reflect_proxies()

LOGIN_URL = "https://passport.baidu.com/v2/?login"
# TiebaLite's login URL: the mobile passport page that redirects to the
# tieba index after a successful login. The native Java side uses the same
# success trigger as TiebaLite's LoginPage (tieba index URL + BDUSS cookie).
LOGIN_URL_WAPPASS = (
    "https://wappass.baidu.com/passport?login&u="
    "https%3A%2F%2Ftieba.baidu.com%2Findex%2Ftbwise%2Fmine"
)
LOCAL_ORIGIN = "http://127.0.0.1:5000"
TIMEOUT_SECONDS = 300
POLL_INTERVAL = 0.8
NAV_SETTLE_DELAY = 2.0
VALIDATE_RETRIES = 3

_BDUSS_RE = re.compile(r"(?:^|;\s*)BDUSS=([^;]+)")
_BAIDU_URLS = (
    "https://tieba.baidu.com/",
    "https://www.baidu.com/",
    "https://passport.baidu.com/",
)

_LOCK = threading.Lock()
_ACTIVE = False
_FLOW_ID = 0
_CANCEL = threading.Event()

_CookieManager = None
_APP_CLS = None
_APP_CLS_ERR = None


# --------------------------------------------------------------------------
# reflection bridge (boot classes only)
# --------------------------------------------------------------------------

def _cm():
    global _CookieManager
    if _CookieManager is None:
        _CookieManager = autoclass("android.webkit.CookieManager")
    return _CookieManager


def _app_class():
    """Resolve org.kivy.android.PythonActivity via the app ClassLoader."""
    global _APP_CLS, _APP_CLS_ERR
    if _APP_CLS is not None:
        return _APP_CLS, None
    if _APP_CLS_ERR:
        return None, _APP_CLS_ERR
    try:
        activity_thread = autoclass("android.app.ActivityThread")
        app = activity_thread.currentApplication()
        loader = app.getClassLoader()
        cls = loader.loadClass("org.kivy.android.PythonActivity")
        _APP_CLS = cls
        return cls, None
    except Exception as exc:  # noqa: BLE001
        _APP_CLS_ERR = "{}".format(exc)
        return None, _APP_CLS_ERR


def _find_static_method(cls, name, nparams):
    methods = cls.getMethods()
    for method in methods:
        if method.getName() == name and len(method.getParameterTypes()) == nparams:
            return method
    return None


def _find_static_field(cls, name):
    fields = cls.getFields()
    for field in fields:
        if field.getName() == name:
            return field, None
    # mWebView etc. are protected in p4a's PythonActivity; getFields()
    # cannot see them, so fall back to getDeclaredField.
    try:
        field = cls.getDeclaredField(name)
    except Exception as exc:  # noqa: BLE001
        return None, "getDeclaredField 失败：{}".format(exc)
    try:
        field.setAccessible(True)
    except Exception as exc:  # noqa: BLE001 - not fatal for app's own fields
        print("[weblogin] setAccessible {} failed (non-fatal): {}"
              .format(name, exc))
    return field, None


def _navigate(url):
    """Navigate the WebView; loadUrl itself dispatches to the UI thread."""
    cls, err = _app_class()
    if err:
        return err
    try:
        method = _find_static_method(cls, "loadUrl", 1)
        if method is None:
            return "PythonActivity.loadUrl 方法未找到"
        method.invoke(None, [url])  # static: obj=null, args=Object[]{url}
        return None
    except Exception as exc:  # noqa: BLE001
        return "{}".format(exc)


def _call_static(name, args=None):
    """Call a public static PythonActivity method through the reflection
    bridge. Returns (result, error). Never touches app classes via
    autoclass, which is broken in the Zhuoyitong container."""
    cls, err = _app_class()
    if err:
        return None, err
    args = list(args or [])
    try:
        method = _find_static_method(cls, name, len(args))
        if method is None:
            return None, "PythonActivity.{} 方法未找到".format(name)
        return method.invoke(None, args), None
    except Exception as exc:  # noqa: BLE001
        return None, "{}".format(exc)


def _clear_login_session():
    """Call PythonActivity.clearLoginSession() on the UI thread. It wipes
    cookies + WebStorage + WebViewDatabase + WebView history/cache/form data,
    which plain CookieManager.removeAllCookies() alone did not achieve in the
    Zhuoyitong container (Baidu auto-login wrote the old BDUSS back)."""
    cls, err = _app_class()
    if err:
        return err
    try:
        method = _find_static_method(cls, "clearLoginSession", 0)
        if method is None:
            return "PythonActivity.clearLoginSession 方法未找到"
        method.invoke(None, [])  # static: obj=null, args=Object[]{}
        return None
    except Exception as exc:  # noqa: BLE001
        return "{}".format(exc)


def bridge_check():
    """Non-destructive self test used before starting a login flow."""
    cls, err = _app_class()
    if err:
        return False, "无法解析 PythonActivity：{}".format(err)
    try:
        name = cls.getName()
        if name != "org.kivy.android.PythonActivity":
            return False, "解析到非预期类：{}".format(name)
    except Exception as exc:  # noqa: BLE001
        return False, "cls.getName 失败：{}".format(exc)
    try:
        if _find_static_method(cls, "loadUrl", 1) is None:
            return False, "loadUrl(String) 方法未找到"
    except Exception as exc:  # noqa: BLE001
        return False, "枚举方法失败：{}".format(exc)
    try:
        field, ferr = _find_static_field(cls, "mWebView")
        if field is None:
            return False, "mWebView 字段未找到：{}".format(ferr or "未知")
        webview = field.get(None)  # static field, obj == null
        if webview is None:
            return False, "mWebView 当前为 null"
    except Exception as exc:  # noqa: BLE001
        return False, "读取 mWebView 失败：{}".format(exc)
    return True, "bridge ok"


# --------------------------------------------------------------------------
# cookie helpers / flow
# --------------------------------------------------------------------------

def _clear_cookies():
    """Best-effort; never allowed to block the login flow."""
    try:
        _cm().getInstance().removeAllCookies()
        _cm().getInstance().flush()
    except Exception as exc:  # noqa: BLE001
        print("[weblogin] clear cookies failed: {}".format(exc))


def _wait_cookies_cleared(timeout=2.5):
    """Return True once no BDUSS cookie is visible. If a stale BDUSS survives
    the wipe, we refuse to continue instead of silently adding the old
    account again."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get_bduss() is None:
            return True
        time.sleep(0.3)
    return False


def _get_bduss():
    try:
        mgr = _cm().getInstance()
        for url in _BAIDU_URLS:
            raw = mgr.getCookie(url) or ""
            match = _BDUSS_RE.search(raw)
            if match:
                value = match.group(1).strip()
                # Some WebView/CookieManager combinations return URL-encoded
                # cookie values (e.g. %2B). Decode once when '%' is present;
                # '+' is NOT converted (that would corrupt base64-ish BDUSS).
                if "%" in value:
                    try:
                        value = unquote(value)
                    except Exception:  # noqa: BLE001
                        pass
                return value.strip()
    except Exception as exc:  # noqa: BLE001
        print("[weblogin] getCookie failed: {}".format(exc))
    return None


def _done(ok, name="", detail=""):
    query = urlencode({
        "ok": "1" if ok else "0",
        "name": (name or "")[:60],
        "detail": (detail or "")[:400],
    })
    _navigate(LOCAL_ORIGIN + "/weblogin/done?" + query)


def _sanitize(text, bduss):
    if isinstance(text, str) and bduss and bduss in text:
        text = text.replace(bduss, "***")
    return text


def cancel_login():
    """Cancel any in-flight flow. Called every time the /weblogin page opens."""
    global _ACTIVE
    with _LOCK:
        _ACTIVE = False
    _CANCEL.set()


def start_login():
    """Return (started: bool, error: str|None)."""
    global _ACTIVE, _FLOW_ID
    ok, err = bridge_check()
    if not ok:
        return False, err
    with _LOCK:
        if _ACTIVE:
            return False, "已有登录流程进行中"
        _ACTIVE = True
        _FLOW_ID += 1
        token = _FLOW_ID
        _CANCEL.clear()
    threading.Thread(target=_run, args=(token,), daemon=True,
                     name="weblogin").start()
    return True, None


def _finish(token, ok, name="", detail=""):
    if token != _FLOW_ID or _CANCEL.is_set():
        return
    _done(ok, name=name, detail=detail)


def _run(token):
    global _ACTIVE
    bduss = None
    try:
        # Let the local page receive its "started" JSON before we leave it.
        time.sleep(0.6)
        if _CANCEL.is_set() or token != _FLOW_ID:
            return

        # Clear any BDUSS left by a previous Baidu login BEFORE navigating;
        # otherwise the poll below instantly captures the old account.
        # Runs on this background thread, so it cannot block the UI.
        _clear_cookies()
        clear_err = _clear_login_session()
        if clear_err:
            print("[weblogin] clearLoginSession failed (cookie-only fallback "
                  "still applied): {}".format(clear_err))
        time.sleep(0.8)
        if not _wait_cookies_cleared():
            _finish(token, False,
                    detail="旧登录态清理失败：BDUSS Cookie 仍残留，"
                           "请退出 App 重试或改用「手动输入 BDUSS」。")
            return

        nav_err = _navigate(LOGIN_URL)
        if nav_err:
            _finish(token, False,
                    detail="无法打开百度登录页：{}".format(nav_err))
            return

        # NOTE: we intentionally do NOT read WebView.getUrl() here.
        # Reading it on the UI thread would require a Python-implemented
        # java.lang.Runnable, which pyjnius registers through the app class
        # org.jnius.NativeInvocationHandler — FindClass cannot see app
        # classes in the Zhuoyitong container (ClassNotFoundException).
        # Let the page settle, then go straight to CookieManager polling.
        time.sleep(NAV_SETTLE_DELAY)

        deadline = time.time() + TIMEOUT_SECONDS
        while not _CANCEL.is_set() and time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            bduss = _get_bduss()
            if bduss:
                break

        if _CANCEL.is_set():
            return
        if not bduss:
            _finish(token, False,
                    detail="登录超时：未检测到 BDUSS Cookie，请重试。")
            return

        print("[weblogin] BDUSS found in cookie (length {}, no content "
              "logged)".format(len(bduss)))

        fmt_err = tieba_api.format_check(bduss, strict=False)
        if fmt_err:
            _finish(token, False,
                    detail="Cookie 中 BDUSS 格式异常：" + fmt_err)
            return

        result = None
        for attempt in range(VALIDATE_RETRIES):
            if _CANCEL.is_set():
                return
            result = tieba_api.validate_bduss(bduss)
            if result.get("ok") or result.get("category") == "账号失效":
                break
            time.sleep(3)

        if not result or not result.get("ok"):
            detail = (result or {}).get("detail") or "验证失败"
            detail = _sanitize(detail, bduss)
            category = (result or {}).get("category") or "验证失败"
            _finish(token, False,
                    detail="{}：{}".format(category, detail))
            return

        name = result.get("display_name") or "百度账号"
        bduss_enc = keystore_crypto.encrypt_text(bduss)
        account = storage.add_account(name, bduss, bduss_enc)
        _finish(token, True, name=account.get("name") or name)
        # Cleanup only after the result page is on its way.
        _clear_cookies()
    except Exception as exc:  # noqa: BLE001 - show sanitized text on device
        detail = _sanitize(str(exc), bduss)
        print("[weblogin] flow error: {}".format(detail))
        _finish(token, False, detail="登录处理异常：" + detail)
    finally:
        with _LOCK:
            if token == _FLOW_ID:
                _ACTIVE = False


# --------------------------------------------------------------------------
# native login flow (0.1.31): TiebaLite-style "built-in" login.
# The whole WebView navigates to the wappass login page while a native blue
# top bar (Java side, TiebaSignNativeLoginChrome) stays visible with a back
# button. Login itself is the OFFICIAL Baidu page inside the app WebView;
# no request is simulated. On success the page lands on the tieba index and
# the Java side flags native_detected (same trigger as TiebaLite); Python
# still polls CookieManager and runs validate -> Keystore-encrypt -> store.
# --------------------------------------------------------------------------

_NATIVE_LOCK = threading.Lock()
_NATIVE_ACTIVE = False
_NATIVE_FLOW_ID = 0
_NATIVE_CANCEL = threading.Event()
_NATIVE_STATE = {"phase": "idle", "detail": "", "elapsed": 0}


def _set_native_state(phase, detail=""):
    global _NATIVE_STATE
    with _NATIVE_LOCK:
        _NATIVE_STATE = {
            "phase": phase,
            "detail": detail,
            "elapsed": int(time.time() - _NATIVE_START),
        }


def native_state():
    with _NATIVE_LOCK:
        return dict(_NATIVE_STATE)


def _stop_native_java():
    _call_static("stopNativeLogin")


def cancel_native_login():
    """Cancel any in-flight native flow. Called every time /login opens."""
    global _NATIVE_ACTIVE
    with _NATIVE_LOCK:
        _NATIVE_ACTIVE = False
    _NATIVE_CANCEL.set()
    _stop_native_java()


def start_native_login():
    """Return (started: bool, error: str|None)."""
    global _NATIVE_ACTIVE, _NATIVE_FLOW_ID, _NATIVE_START
    ok, err = bridge_check()
    if not ok:
        return False, err
    with _NATIVE_LOCK:
        if _NATIVE_ACTIVE:
            return False, "已有登录流程进行中"
        _NATIVE_ACTIVE = True
        _NATIVE_FLOW_ID += 1
        token = _NATIVE_FLOW_ID
        _NATIVE_CANCEL.clear()
        _NATIVE_START = time.time()
        _NATIVE_STATE = {"phase": "starting", "detail": "正在准备…",
                         "elapsed": 0}
    threading.Thread(target=_run_native, args=(token,), daemon=True,
                     name="nativelogin").start()
    return True, None


def _finish_native(token, ok, name="", detail=""):
    if token != _NATIVE_FLOW_ID or _NATIVE_CANCEL.is_set():
        return
    query = urlencode({
        "ok": "1" if ok else "0",
        "name": (name or "")[:60],
        "detail": (detail or "")[:400],
    })
    _navigate(LOCAL_ORIGIN + "/login/done?" + query)


def _run_native(token):
    global _NATIVE_ACTIVE
    bduss = None
    try:
        _set_native_state("clearing", "正在清理旧登录状态…")
        _clear_cookies()
        clear_err = _clear_login_session()
        if clear_err:
            print("[nativelogin] clearLoginSession failed (cookie-only "
                  "fallback still applied): {}".format(clear_err))
        time.sleep(0.8)
        if not _wait_cookies_cleared():
            _finish_native(token, False,
                           detail="旧登录态清理失败：BDUSS Cookie 仍残留，"
                                  "请退出 App 重试或改用「手动输入 BDUSS」。")
            return

        _set_native_state("navigating", "正在打开百度登录页…")
        _, start_err = _call_static("startNativeLogin", [LOGIN_URL_WAPPASS])
        if start_err:
            _finish_native(token, False,
                           detail="无法打开百度登录页：{}".format(start_err))
            return

        deadline = time.time() + TIMEOUT_SECONDS
        java_detected = False
        while not _NATIVE_CANCEL.is_set() and time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            bduss = _get_bduss()
            if bduss:
                break
            status, _ = _call_static("getNativeLoginStatus")
            if status == "native_detected":
                java_detected = True
            _set_native_state(
                "waiting",
                "等待登录完成…"
                + ("（已到达贴吧首页）" if java_detected else ""))

        if _NATIVE_CANCEL.is_set():
            return
        if not bduss:
            _finish_native(token, False,
                           detail="登录超时：未检测到 BDUSS Cookie，请重试。")
            return

        print("[nativelogin] BDUSS found in cookie (length {}, no content "
              "logged)".format(len(bduss)))

        _set_native_state("validating", "正在验证账号…")
        fmt_err = tieba_api.format_check(bduss, strict=False)
        if fmt_err:
            _finish_native(token, False,
                           detail="Cookie 中 BDUSS 格式异常：" + fmt_err)
            return

        result = None
        for attempt in range(VALIDATE_RETRIES):
            if _NATIVE_CANCEL.is_set():
                return
            result = tieba_api.validate_bduss(bduss)
            if result.get("ok") or result.get("category") == "账号失效":
                break
            time.sleep(3)

        if not result or not result.get("ok"):
            detail = (result or {}).get("detail") or "验证失败"
            detail = _sanitize(detail, bduss)
            category = (result or {}).get("category") or "验证失败"
            _finish_native(token, False,
                           detail="{}：{}".format(category, detail))
            return

        _set_native_state("saving", "正在加密保存账号…")
        name = result.get("display_name") or "百度账号"
        bduss_enc = keystore_crypto.encrypt_text(bduss)
        account = storage.add_account(name, bduss, bduss_enc)
        _finish_native(token, True, name=account.get("name") or name)
        # Cleanup only after the result page is on its way.
        _clear_cookies()
    except Exception as exc:  # noqa: BLE001 - show sanitized text on device
        detail = _sanitize(str(exc), bduss)
        print("[nativelogin] flow error: {}".format(detail))
        _set_native_state("error", detail)
        _finish_native(token, False, detail="登录处理异常：" + detail)
    finally:
        _stop_native_java()
        with _NATIVE_LOCK:
            if token == _NATIVE_FLOW_ID:
                _NATIVE_ACTIVE = False

