# -*- coding: utf-8 -*-
"""TiebaSign M2.4 - auto sign scheduler (in-app scheduler + foreground
keep-alive + launch catch-up).

Rules (PRD v1.0):
  * Multi-account: every stored account participates.
  * Global default time 08:00; each account may follow global or use its own
    time.
  * Runs once per account per day. No automatic retry of failed forums.
  * Failure-only notifications controlled by two independent switches:
      notify_system -> Android notification (via Java helper)
      notify_dot    -> red dot on the 签到 tab (cleared when sign page opens)
  * No sign history. Only the latest auto-run snapshot per account is kept.

Data file: <ANDROID_PRIVATE>/tiebasign_autosign.json  (no BDUSS stored here)
"""

import json
import os
import re
import threading
import time
import traceback

_DATA_FILENAME = "tiebasign_autosign.json"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_DEFAULTS = {
    "enabled": False,
    "global_time": "08:00",
    "notify_system": True,
    "notify_dot": True,
    "accounts": {},   # {str(account_id): {"follow_global": bool, "custom_time": "HH:MM"}}
    "runs": {},       # {str(account_id): {"date": "YYYY-MM-DD", "ts": iso}}
    "results": {},    # {str(account_id): latest snapshot}
}

_lock = threading.RLock()
_cache = None
_sched_thread = None
_run_now_flag = False
_keepalive_state = None
_perm_requested = False


# --------------------------------------------------------------------------
# JSON store (same pattern as storage.py: atomic tmp + os.replace)
# --------------------------------------------------------------------------

def _data_path():
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, _DATA_FILENAME)


def _load():
    global _cache
    with _lock:
        if _cache is not None:
            return json.loads(json.dumps(_cache))
        data = dict(_DEFAULTS)
        try:
            with open(_data_path(), "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            for key, value in _DEFAULTS.items():
                if key in loaded and isinstance(loaded[key], type(value)):
                    data[key] = loaded[key]
            # normalize account configs
            data["accounts"] = {str(k): v for k, v in data["accounts"].items()
                                if isinstance(v, dict)}
            data["runs"] = {str(k): v for k, v in data["runs"].items()
                            if isinstance(v, dict)}
            data["results"] = {str(k): v for k, v in data["results"].items()
                               if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 - corrupt settings: start fresh
            _log("settings load failed, using defaults: {} {}".format(
                type(exc).__name__, exc))
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


def _log(msg):
    try:
        with open(os.path.join(os.getcwd(), "autosign.log"), "a",
                  encoding="utf-8") as fh:
            fh.write("[{}] {}\n".format(time.strftime("%m-%d %H:%M:%S"), msg))
            fh.flush()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Settings API
# --------------------------------------------------------------------------

def _validate_time(value):
    value = str(value or "").strip()
    return value if _TIME_RE.match(value) else None


def get_settings():
    """Public settings plus per-account effective values (no secrets)."""
    import storage

    data = _load()
    accounts = []
    for acc in storage.list_accounts():
        aid = str(acc["id"])
        cfg = data["accounts"].get(aid, {})
        follow_global = bool(cfg.get("follow_global", True))
        custom_time = _validate_time(cfg.get("custom_time")) or data["global_time"]
        accounts.append({
            "id": acc["id"],
            "name": acc["name"],
            "mask": acc["mask"],
            "follow_global": follow_global,
            "custom_time": custom_time,
            "effective_time": data["global_time"] if follow_global else custom_time,
        })
    return {
        "enabled": bool(data["enabled"]),
        "global_time": _validate_time(data["global_time"]) or "08:00",
        "notify_system": bool(data["notify_system"]),
        "notify_dot": bool(data["notify_dot"]),
        "accounts": accounts,
    }


def save_settings(payload):
    """payload keys: enabled/global_time/notify_system/notify_dot/accounts."""
    data = _load()
    if "enabled" in payload:
        data["enabled"] = bool(payload["enabled"])
    gt = _validate_time(payload.get("global_time"))
    if gt:
        data["global_time"] = gt
    if "notify_system" in payload:
        data["notify_system"] = bool(payload["notify_system"])
    if "notify_dot" in payload:
        data["notify_dot"] = bool(payload["notify_dot"])
    accounts_payload = payload.get("accounts")
    if isinstance(accounts_payload, dict):
        for aid, cfg in accounts_payload.items():
            if not isinstance(cfg, dict):
                continue
            entry = {"follow_global": bool(cfg.get("follow_global", True)),
                     "custom_time": ""}
            ct = _validate_time(cfg.get("custom_time"))
            if ct:
                entry["custom_time"] = ct
            data["accounts"][str(aid)] = entry
    _save(data)
    _log("settings saved enabled={} notify_system={} notify_dot={}".format(
        data["enabled"], data["notify_system"], data["notify_dot"]))
    return get_settings()


def dot_pending():
    data = _load()
    return any(r.get("dot_pending") for r in data["results"].values())


def clear_dot():
    data = _load()
    changed = False
    for r in data["results"].values():
        if r.get("dot_pending"):
            r["dot_pending"] = False
            changed = True
    if changed:
        _save(data)
    return True


def last_results():
    import storage

    data = _load()
    out = []
    for acc in storage.list_accounts():
        snap = data["results"].get(str(acc["id"]))
        if snap:
            item = dict(snap)
            item["account_id"] = acc["id"]
            item["name"] = acc["name"]
            item["mask"] = acc["mask"]
            out.append(item)
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return out


def request_run_now():
    """Trigger one immediate run of every account (test button)."""
    global _run_now_flag
    with _lock:
        _run_now_flag = True
    _log("run-now requested")
    return True


# --------------------------------------------------------------------------
# Java bridge (boot-classpath reflection, same approach as weblogin.py)
# --------------------------------------------------------------------------

_APP_CLS = None
_APP_CLS_ERR = None
_METHOD_CACHE = {}


def _patch_reflect_proxies():
    try:
        import jnius.reflect as _jreflect  # noqa: F401
        from jnius import JavaMethod
        if not hasattr(_jreflect.Field, "get"):
            _jreflect.Field.get = JavaMethod(
                "(Ljava/lang/Object;)Ljava/lang/Object;")
        if not hasattr(_jreflect.Field, "setAccessible"):
            _jreflect.Field.setAccessible = JavaMethod("(Z)V")
        if not hasattr(_jreflect.Method, "invoke"):
            _jreflect.Method.invoke = JavaMethod(
                "(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;")
        if not hasattr(_jreflect.Method, "setAccessible"):
            _jreflect.Method.setAccessible = JavaMethod("(Z)V")
        return True
    except Exception:
        return False


def _app_class():
    global _APP_CLS, _APP_CLS_ERR
    if _APP_CLS is not None or _APP_CLS_ERR is not None:
        return _APP_CLS, _APP_CLS_ERR
    try:
        _patch_reflect_proxies()
        from jnius import autoclass
        at = autoclass("android.app.ActivityThread")
        app = at.currentApplication()
        cl = app.getClassLoader()
        _APP_CLS = cl.loadClass("org.kivy.android.PythonActivity")
    except Exception as exc:  # noqa: BLE001
        _APP_CLS_ERR = "{}: {}".format(type(exc).__name__, exc)
    return _APP_CLS, _APP_CLS_ERR


def _call_static(name, args):
    cls, err = _app_class()
    if cls is None:
        return False, err or "activity class unavailable"
    key = (name, len(args))
    method = _METHOD_CACHE.get(key)
    if method is None:
        method = None
        for m in cls.getMethods():
            if m.getName() == name and len(m.getParameterTypes()) == len(args):
                method = m
                break
        if method is not None:
            _METHOD_CACHE[key] = method
    if method is None:
        return False, "static method {} not found".format(name)
    try:
        result = method.invoke(None, list(args))
        if result is None or result is False:
            return bool(result), None
        return bool(result), None
    except Exception as exc:  # noqa: BLE001
        return False, "{}: {}".format(type(exc).__name__, exc)


def _service_start():
    global _keepalive_state
    ok, err = _call_static("startKeepAliveService", [])
    _keepalive_state = ok
    if not ok:
        _log("keepalive start failed: {}".format(err))
    return ok


def _service_stop():
    global _keepalive_state
    ok, err = _call_static("stopKeepAliveService", [])
    if ok:
        _keepalive_state = False
    else:
        _log("keepalive stop failed: {}".format(err))
    return ok


def _ensure_keepalive(enabled):
    global _keepalive_state
    if enabled:
        if _keepalive_state is not True:
            _service_start()
        if not _perm_requested:
            _request_notify_permission()
    else:
        if _keepalive_state is not False:
            _service_stop()


def _request_notify_permission():
    global _perm_requested
    try:
        _call_static("requestNotificationPermission", [])
        _perm_requested = True
    except Exception as exc:  # noqa: BLE001
        _log("notify permission request failed: {}".format(exc))


def notify_fail(title, text):
    """System notification for auto-sign failures (only when switch is on)."""
    ok, err = _call_static("notifySignFail", [str(title)[:80], str(text)[:300]])
    if not ok:
        _log("notifySignFail failed: {}".format(err))
    return ok


# --------------------------------------------------------------------------
# Auto-run engine
# --------------------------------------------------------------------------

def _effective_time(account_id, data):
    cfg = data["accounts"].get(str(account_id), {})
    if cfg.get("follow_global", True):
        return data["global_time"]
    return _validate_time(cfg.get("custom_time")) or data["global_time"]


def _wait_for_signer_start(account):
    """signer.start() rejects concurrent runs; wait for the other run."""
    import signer
    import storage
    import keystore_crypto
    import tieba_api

    enc = storage.get_encrypted_bduss(account["id"])
    if not enc:
        return None, "no encrypted BDUSS for account {}".format(account["id"])
    try:
        bduss = keystore_crypto.decrypt_text(enc)
    except Exception as exc:  # noqa: BLE001
        return None, "decrypt failed for account {}: {}".format(
            account["id"], exc)
    fmt_err = tieba_api.format_check(bduss)
    if fmt_err:
        return None, "format check failed for account {}: {}".format(
            account["id"], fmt_err)
    _log("auto run account_id={} bduss_len={}".format(
        account["id"], len(bduss)))
    deadline = time.time() + 120
    run_id = None
    err = None
    while time.time() < deadline:
        run_id, err = signer.start(account, bduss)
        if err is None:
            return run_id, None
        time.sleep(5)
    return None, err or "signer busy"


def _wait_signer_done(timeout=900):
    import signer

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = signer.get_state()
        if state and state.get("status") in ("done", "error"):
            return state
        time.sleep(2)
    return signer.get_state()


def _finalize(account, state):
    data = _load()
    msign = state.get("msign") or {}
    failures = list(state.get("failures") or [])
    failed = int(state.get("forum_failed") or 0)
    success = int(state.get("forum_success") or 0)

    if msign and msign.get("status") != "success":
        failed += 1
        failures.insert(0, {
            "forum": "网页一键签到(msign)",
            "category": msign.get("category") or "风控其他",
            "code": msign.get("code"),
            "detail": msign.get("detail") or "",
        })
    if state.get("error"):
        failed += 1
        failures.append({
            "forum": "签到流程",
            "category": (state.get("error") or {}).get("category") or "网络异常",
            "code": (state.get("error") or {}).get("code"),
            "detail": (state.get("error") or {}).get("detail") or "",
        })

    ok = (state.get("status") == "done") and failed == 0
    snapshot = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": bool(ok),
        "success": int(success),
        "failed": int(failed),
        "failures": failures[:50],
        "dot_pending": bool(not ok and data["notify_dot"]),
    }
    data["results"][str(account["id"])] = snapshot
    _save(data)

    if not ok:
        _log("auto run account_id={} FAILED ok=0 failed={}".format(
            account["id"], failed))
        if data["notify_system"]:
            text = _notify_text(account, snapshot)
            try:
                notify_fail("贴吧签到助手 自动签到失败", text)
            except Exception as exc:  # noqa: BLE001
                _log("notify exception: {}: {}".format(
                    type(exc).__name__, exc))
    else:
        _log("auto run account_id={} ok success={}".format(
            account["id"], success))
    return snapshot


def _notify_text(account, snapshot):
    parts = []
    name = account.get("name") or "贴吧账号"
    parts.append("账号「{}」自动签到：成功 {}，失败 {}".format(
        name, snapshot["success"], snapshot["failed"]))
    shown = []
    for f in snapshot["failures"][:3]:
        shown.append("{}（{}）".format(f.get("forum", ""),
                                       f.get("category", "")))
    if shown:
        parts.append("；".join(shown))
    if len(snapshot["failures"]) > 3:
        parts.append("等 {} 项".format(len(snapshot["failures"])))
    return " ".join(parts)[:300]


def _run_due(accounts, today):
    global _run_now_flag
    import storage

    for account in accounts:
        run_id, err = _wait_for_signer_start(account)
        if err:
            _log("account_id={} start failed: {}".format(account["id"], err))
            continue
        _log("account_id={} run_id={} started".format(account["id"], run_id))
        state = _wait_signer_done()
        try:
            _finalize(account, state or {})
        except Exception as exc:  # noqa: BLE001
            _log("finalize error: {} {}".format(type(exc).__name__, exc))
        if not _run_now_flag:
            data = _load()
            data["runs"][str(account["id"])] = {
                "date": today,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            _save(data)
    with _lock:
        _run_now_flag = False


def _scheduler_loop():
    global _run_now_flag
    first = True
    while True:
        try:
            data = _load()
            enabled = bool(data["enabled"])
            try:
                if enabled:
                    _ensure_keepalive(True)
                else:
                    _ensure_keepalive(False)
            except Exception as exc:  # noqa: BLE001
                _log("keepalive error: {}".format(exc))

            if first:
                first = False
                _log("scheduler started enabled={}".format(enabled))
            if enabled:
                import storage

                now = time.localtime()
                hhmm = time.strftime("%H:%M", now)
                today = time.strftime("%Y-%m-%d", now)
                due = []
                run_now = False
                with _lock:
                    run_now = _run_now_flag
                for acc in storage.list_accounts():
                    if run_now:
                        due.append(acc)
                        continue
                    ran = data["runs"].get(str(acc["id"]), {}).get("date")
                    if ran != today and hhmm >= _effective_time(
                            acc["id"], data):
                        due.append(acc)
                if due:
                    _log("due accounts: {} (run_now={})".format(
                        [a["id"] for a in due], run_now))
                    _run_due(due, today)
        except Exception as exc:  # noqa: BLE001
            _log("scheduler loop error: {} {}\n{}".format(
                type(exc).__name__, exc, traceback.format_exc()))
        time.sleep(20)


def apply_settings_immediately(enabled):
    """After the user toggles the master switch, start/stop keep-alive and
    request the notification permission right away instead of waiting for the
    20s scheduler tick."""
    try:
        _ensure_keepalive(bool(enabled))
        return True
    except Exception as exc:  # noqa: BLE001
        _log("apply_settings_immediately failed: {} {}".format(
            type(exc).__name__, exc))
        return False


def start_background():
    """Start the scheduler thread (idempotent). Called once after app_ok."""
    global _sched_thread
    if _sched_thread is not None and _sched_thread.is_alive():
        return False
    _sched_thread = threading.Thread(target=_scheduler_loop,
                                     name="autosign-scheduler", daemon=True)
    _sched_thread.start()
    _log("scheduler thread started")
    return True


def keepalive_running():
    return bool(_keepalive_state)
