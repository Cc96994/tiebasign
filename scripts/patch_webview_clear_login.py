#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch p4a webview PythonActivity.java copies with TiebaSignClearLoginSession:
a static clearLoginSession() that runs on the UI thread and wipes cookies
(including explicit expired BDUSS for baidu hosts), WebStorage, WebViewDatabase
and WebView history/cache/form-data. Idempotent (marker check)."""
import sys

FILES = [
    "/home/s7909/p4a-develop/pythonforandroid/bootstraps/webview/build/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/webview/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/dists/tiebaweb/src/main/java/org/kivy/android/PythonActivity.java",
]

ANCHOR = """        Log.i(TAG, "Opening URL: " + url);
        mActivity.runOnUiThread(new LoadUrl(url));
    }
"""
MARKER = "TiebaSignClearLoginSession"

INSERT = ANCHOR + """
    public static void clearLoginSession() {
        mActivity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    android.webkit.CookieManager cm =
                            android.webkit.CookieManager.getInstance();
                    cm.removeAllCookies();
                    cm.removeSessionCookies();
                    String[] hosts = {
                        "https://tieba.baidu.com/",
                        "https://www.baidu.com/",
                        "https://passport.baidu.com/"
                    };
                    for (String h : hosts) {
                        cm.setCookie(h, "BDUSS=; Max-Age=0");
                        cm.setCookie(h, "BDUSS=; Expires=Thu, 01 Jan 1970 00:00:00 GMT");
                    }
                    cm.flush();
                    android.webkit.WebStorage.getInstance().deleteAllData();
                    android.webkit.WebViewDatabase db =
                            android.webkit.WebViewDatabase.getInstance(mActivity);
                    db.clearFormData();
                    db.clearHttpAuthUsernamePassword();
                    if (mWebView != null) {
                        mWebView.clearHistory();
                        mWebView.clearCache(true);
                        mWebView.clearFormData();
                    }
                } catch (Exception e) {
                    Log.w(TAG, "TiebaSignClearLoginSession failed", e);
                }
            }
        });
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
