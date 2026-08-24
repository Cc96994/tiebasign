#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix CookieManager API mismatch in TiebaSignClearLoginSession: compileSdk 34
only exposes removeAllCookies(ValueCallback<Boolean>) / removeSessionCookies(...),
so pass null. Idempotent."""
import sys

FILES = [
    "/home/s7909/p4a-develop/pythonforandroid/bootstraps/webview/build/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/webview/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/dists/tiebaweb/src/main/java/org/kivy/android/PythonActivity.java",
]

REPL = [
    ("cm.removeAllCookies();", "cm.removeAllCookies(null);"),
    ("cm.removeSessionCookies();", "cm.removeSessionCookies(null);"),
]

failed = False
for path in FILES:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    changed = 0
    for old, new in REPL:
        if old in text and new not in text:
            text = text.replace(old, new)
            changed += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("{} changed={}".format(path, changed))
print("OK")
