# -*- coding: utf-8 -*-
"""JSON-file account store for TiebaSign.

Replaces the SQLite store: _sqlite3 is not bundled in this dist and we do
not want any extra native module at startup. Only ever stores the
Keystore-ENCRYPTED BDUSS plus a masked display form (first 4 + last 4).
The plaintext BDUSS exists in memory only during validation / signing.
No history table (PRD v1.0).

The file lives in the ANDROID_PRIVATE root (NOT files/app) so p4a's
unpackAsset() version cleanup cannot wipe accounts.
"""

import json
import os
import threading
import time

DATA_FILENAME = "tiebasign_accounts.json"
_DATA_PATH = os.path.join(
    os.environ.get("ANDROID_PRIVATE") or os.getcwd(), DATA_FILENAME)
_lock = threading.Lock()
_cache = None  # list[dict] or None until first load


def _mask_bduss(bduss):
    if len(bduss) < 10:
        return "*" * 8
    return bduss[:4] + "****" + bduss[-4:]


def _load():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.isfile(_DATA_PATH):
        try:
            with open(_DATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = data if isinstance(data, list) else []
        except (OSError, ValueError):
            _cache = []
    else:
        _cache = []
    return _cache


def _save(data):
    tmp = _DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, _DATA_PATH)
    global _cache
    _cache = data


def _next_id(data):
    return (max((int(a.get("id", 0)) for a in data), default=0)) + 1


def _public(a):
    return {
        "id": a["id"],
        "name": a.get("name", ""),
        "mask": a.get("mask", ""),
        "is_current": bool(a.get("is_current", False)),
        "created_at": a.get("created_at", 0),
        "updated_at": a.get("updated_at", 0),
    }


def selftest():
    """Load the store and persist it back; called once at startup."""
    with _lock:
        data = _load()
        _save(data)
        return _DATA_PATH


def list_accounts():
    with _lock:
        data = _load()
        ordered = sorted(data, key=lambda a: (
            0 if a.get("is_current") else 1, a.get("id", 0)))
        return [_public(a) for a in ordered]


def get_account(account_id):
    with _lock:
        for a in _load():
            if a["id"] == int(account_id):
                return _public(a)
        return None


def get_current_account():
    with _lock:
        for a in _load():
            if a.get("is_current"):
                return _public(a)
        return None


def add_account(name, bduss, bduss_enc):
    """Append; becomes current if it is the first account."""
    now = int(time.time())
    with _lock:
        data = _load()
        is_current = (len(data) == 0)
        acct = {
            "id": _next_id(data),
            "name": name or "",
            "mask": _mask_bduss(bduss),
            "bduss_enc": bduss_enc,
            "is_current": is_current,
            "created_at": now,
            "updated_at": now,
        }
        data.append(acct)
        _save(data)
        return _public(acct)


def rename_account(account_id, name):
    now = int(time.time())
    with _lock:
        data = _load()
        for a in data:
            if a["id"] == int(account_id):
                a["name"] = name or ""
                a["updated_at"] = now
                _save(data)
                return _public(a)
        return None


def activate_account(account_id):
    now = int(time.time())
    with _lock:
        data = _load()
        target = None
        for a in data:
            if a["id"] == int(account_id):
                target = a
        if target is None:
            return None
        for a in data:
            a["is_current"] = (a is target)
            a["updated_at"] = now
        _save(data)
        return _public(target)


def delete_account(account_id):
    with _lock:
        data = _load()
        aid = int(account_id)
        was_current = any(
            a["id"] == aid and a.get("is_current") for a in data)
        data = [a for a in data if a["id"] != aid]
        if was_current and data:
            first = sorted(data, key=lambda a: a["id"])[0]
            first["is_current"] = True
            first["updated_at"] = int(time.time())
        _save(data)
        ordered = sorted(data, key=lambda a: (
            0 if a.get("is_current") else 1, a.get("id", 0)))
        return [_public(a) for a in ordered]


def get_encrypted_bduss(account_id):
    with _lock:
        for a in _load():
            if a["id"] == int(account_id):
                return a.get("bduss_enc")
        return None


def current_encrypted_bduss():
    acct = get_current_account()
    return get_encrypted_bduss(acct["id"]) if acct else None
