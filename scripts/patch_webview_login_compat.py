#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch the p4a webview PythonActivity.java copies with TiebaSignWebLoginCompat:
browser-like User-Agent + accept cookies + third-party cookies for the Baidu
passport login/verification flow. Idempotent (marker check)."""
import sys

FILES = [
    "/home/s7909/p4a-develop/pythonforandroid/bootstraps/webview/build/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/webview/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/dists/tiebaweb/src/main/java/org/kivy/android/PythonActivity.java",
]

ANCHOR = "            mWebView.getSettings().setDomStorageEnabled(true);\n"
MARKER = "TiebaSignWebLoginCompat"

INSERT = ANCHOR + """            // TiebaSignWebLoginCompat: browser-like UA + cookies accepted so
            // Baidu passport login/SMS verification behaves like a normal browser.
            try {
                CookieManager.getInstance().setAcceptCookie(true);
                CookieManager.getInstance().setAcceptThirdPartyCookies(mWebView, true);
                String ua = mWebView.getSettings().getUserAgentString();
                if (ua != null && ua.contains("; wv")) {
                    ua = ua.replace("; wv", "");
                }
                mWebView.getSettings().setUserAgentString(
                        ua + " TiebaSign/" + PythonUtil.getAppVersionName(PythonActivity.mActivity));
            } catch (Exception e) {
                // never block bootstrap because of WebView tuning
            }
"""

failed = False
for path in FILES:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if MARKER in text:
        print("ALREADY_PATCHED {}".format(path))
        continue
    if ANCHOR not in text:
        print("ANCHOR_MISSING {}".format(path))
        failed = True
        continue
    text = text.replace(ANCHOR, INSERT, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCHED {}".format(path))

if failed:
    sys.exit(1)
print("OK")
