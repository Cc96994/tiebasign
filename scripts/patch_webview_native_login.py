# -*- coding: utf-8 -*-
"""0.1.31 TiebaSignNativeLoginChrome Java patch (idempotent).

Adds to PythonActivity.java (webview bootstrap):
  - native blue top bar (back + "登录百度账号"), hidden by default
  - vertical LinearLayout root (header + webview area)
  - startNativeLogin(url) / stopNativeLogin() / getNativeLoginStatus()
  - onPageFinished tieba-index + BDUSS cookie detection (status string only,
    the BDUSS value itself is never logged)
  - hardware back inside the native login flow returns to local /login
    (which cancels the flow) instead of web history back

Applied to all three copies:
  1. p4a template
  2. bootstrap_builds cache (apk stage copies from here into dist)
  3. dist (belt and braces)
"""
import io
import sys

FILES = [
    "/home/s7909/p4a-develop/pythonforandroid/bootstraps/webview/build/"
    "src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/"
    "build-arm64-v8a/build/bootstrap_builds/webview/src/main/java/"
    "org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/"
    "build-arm64-v8a/dists/tiebaweb/src/main/java/org/kivy/android/"
    "PythonActivity.java",
]

MARKER = "TiebaSignNativeLoginChrome (marker, idempotent)"

IMPORTS_OLD = """import android.view.KeyEvent;
import android.view.ViewGroup;"""

IMPORTS_NEW = """import android.view.KeyEvent;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;"""

FIELDS_OLD = """    protected static ViewGroup mLayout;
    protected static WebView mWebView;"""

FIELDS_NEW = FIELDS_OLD + """

    // TiebaSignNativeLoginChrome (marker, idempotent)
    public static LinearLayout mLoginHeader = null;
    public static TextView mLoginBack = null;
    public static TextView mLoginTitle = null;
    public static volatile boolean sLoginNativeMode = false;
    public static volatile String sLoginNativeStatus = "";"""

PAGE_FINISHED_OLD = """                        @Override
                        public void onPageFinished(WebView view, String url) {
                            CookieManager.getInstance().flush();
                        }"""

PAGE_FINISHED_NEW = """                        @Override
                        public void onPageFinished(WebView view, String url) {
                            CookieManager.getInstance().flush();
                            // TiebaSignNativeLoginChrome (marker, idempotent):
                            // same success trigger as TiebaLite's LoginPage:
                            // the wappass login flow lands on the tieba index
                            // page and a BDUSS cookie is present there.
                            if (sLoginNativeMode && url != null) {
                                try {
                                    if (url.startsWith(
                                                "https://tieba.baidu.com/index/tbwise/")
                                            || url.startsWith(
                                                "https://tiebac.baidu.com/index/tbwise/")) {
                                        String c = CookieManager.getInstance()
                                                .getCookie(url);
                                        boolean hasBduss = false;
                                        if (c != null) {
                                            String[] parts = c.split(";");
                                            for (String p : parts) {
                                                String k = p.trim();
                                                if (k.startsWith("BDUSS=")
                                                        && k.length()
                                                                > "BDUSS=".length()) {
                                                    hasBduss = true;
                                                    break;
                                                }
                                            }
                                        }
                                        sLoginNativeStatus = hasBduss
                                                ? "native_detected"
                                                : "index_no_bduss";
                                        Log.i(TAG,
                                                "TiebaSignNativeLogin page finished at "
                                                        + (hasBduss
                                                                ? "tieba index with BDUSS"
                                                                : "tieba index without BDUSS"));
                                    }
                                } catch (Exception e) {
                                    Log.w(TAG,
                                            "TiebaSignNativeLogin check failed",
                                            e);
                                }
                            }
                        }"""

LAYOUT_OLD = """            mLayout = new AbsoluteLayout(PythonActivity.mActivity);
            mLayout.addView(mWebView);

            setContentView(mLayout);"""

LAYOUT_NEW = """            // TiebaSignNativeLoginChrome (marker, idempotent):
            // vertical root = hidden native login bar + webview area
            mLayout = new LinearLayout(PythonActivity.mActivity);
            ((LinearLayout) mLayout).setOrientation(LinearLayout.VERTICAL);
            float density = getResources().getDisplayMetrics().density;
            mLoginHeader = new LinearLayout(PythonActivity.mActivity);
            mLoginHeader.setOrientation(LinearLayout.HORIZONTAL);
            mLoginHeader.setGravity(Gravity.CENTER_VERTICAL);
            mLoginHeader.setBackgroundColor(Color.parseColor("#2B5CE6"));
            mLoginHeader.setLayoutParams(new LinearLayout.LayoutParams(
                    LayoutParams.FILL_PARENT, (int) (48 * density + 0.5f)));
            mLoginHeader.setVisibility(View.GONE);
            mLoginBack = new TextView(PythonActivity.mActivity);
            mLoginBack.setText("\\u2039 返回");
            mLoginBack.setTextColor(Color.WHITE);
            mLoginBack.setTextSize(16);
            mLoginBack.setPadding((int) (16 * density), 0,
                    (int) (16 * density), 0);
            mLoginBack.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    // GET /login cancels the Python-side flow and renders
                    // the local login intro page again.
                    mWebView.loadUrl("http://127.0.0.1:5000/login");
                }
            });
            mLoginTitle = new TextView(PythonActivity.mActivity);
            mLoginTitle.setText("登录百度账号");
            mLoginTitle.setTextColor(Color.WHITE);
            mLoginTitle.setTextSize(18);
            mLoginTitle.setGravity(Gravity.CENTER);
            mLoginTitle.setLayoutParams(new LinearLayout.LayoutParams(
                    0, LayoutParams.FILL_PARENT, 1f));
            mLoginHeader.addView(mLoginBack);
            mLoginHeader.addView(mLoginTitle);
            mLayout.addView(mLoginHeader);
            AbsoluteLayout mWebLayout =
                    new AbsoluteLayout(PythonActivity.mActivity);
            mWebLayout.setLayoutParams(new LinearLayout.LayoutParams(
                    LayoutParams.FILL_PARENT, 0, 1f));
            mWebLayout.addView(mWebView);
            mLayout.addView(mWebLayout);

            setContentView(mLayout);"""

METHODS_OLD = """        Log.i(TAG, "Opening URL: " + url);
        mActivity.runOnUiThread(new LoadUrl(url));
    }

    public static void clearLoginSession() {"""

METHODS_NEW = """        Log.i(TAG, "Opening URL: " + url);
        mActivity.runOnUiThread(new LoadUrl(url));
    }

    // TiebaSignNativeLoginChrome (marker, idempotent)
    public static void startNativeLogin(String url) {
        final String target = url;
        mActivity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    sLoginNativeMode = true;
                    sLoginNativeStatus = "";
                    if (mLoginHeader != null) {
                        mLoginHeader.setVisibility(View.VISIBLE);
                    }
                    if (mWebView != null) {
                        mWebView.loadUrl(target);
                    }
                } catch (Exception e) {
                    Log.w(TAG, "TiebaSignNativeLogin start failed", e);
                }
            }
        });
    }

    public static void stopNativeLogin() {
        mActivity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    sLoginNativeMode = false;
                    sLoginNativeStatus = "";
                    if (mLoginHeader != null) {
                        mLoginHeader.setVisibility(View.GONE);
                    }
                } catch (Exception e) {
                    Log.w(TAG, "TiebaSignNativeLogin stop failed", e);
                }
            }
        });
    }

    public static String getNativeLoginStatus() {
        return sLoginNativeStatus;
    }

    public static void clearLoginSession() {"""

BACK_OLD = """        if (keyCode == KeyEvent.KEYCODE_BACK) {
            // Go back if there is web page history behind,"""

BACK_NEW = """        if (keyCode == KeyEvent.KEYCODE_BACK) {
            // TiebaSignNativeLoginChrome (marker, idempotent): back inside
            // the native login flow returns to local /login (cancels flow).
            if (sLoginNativeMode) {
                mWebView.loadUrl("http://127.0.0.1:5000/login");
                return true;
            }
            // Go back if there is web page history behind,"""

PATCHES = [
    ("imports", IMPORTS_OLD, IMPORTS_NEW),
    ("fields", FIELDS_OLD, FIELDS_NEW),
    ("onPageFinished", PAGE_FINISHED_OLD, PAGE_FINISHED_NEW),
    ("layout", LAYOUT_OLD, LAYOUT_NEW),
    ("methods", METHODS_OLD, METHODS_NEW),
    ("backKey", BACK_OLD, BACK_NEW),
]


def patch_one(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        print("SKIP (already patched): {}".format(path))
        return True
    applied = []
    for name, old, new in PATCHES:
        if old not in text:
            print("MISSING anchor {} in {}".format(name, path))
            return False
        if text.count(old) != 1:
            print("ANCHOR {} not unique ({}x) in {}".format(
                name, text.count(old), path))
            return False
        text = text.replace(old, new, 1)
        applied.append(name)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("PATCHED {}: {}".format(path, ",".join(applied)))
    return True


def main():
    ok = True
    for path in FILES:
        try:
            ok = patch_one(path) and ok
        except FileNotFoundError:
            print("NOT FOUND: {}".format(path))
            ok = False
    print("RESULT: {}".format("OK" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
