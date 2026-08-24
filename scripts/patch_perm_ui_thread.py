#!/usr/bin/env python3
"""Patch requestNotificationPermission to dispatch to the UI thread.

Android Activity.requestPermissions() must be called on the main thread
(startActivityForResult checkThread). Scheduler/HTTP threads otherwise get a
caught Throwable and the system permission dialog never shows.
"""
import sys

FILES = [
    "/home/s7909/p4a-develop/pythonforandroid/bootstraps/webview/build/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/webview/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/dists/tiebaweb/src/main/java/org/kivy/android/PythonActivity.java",
]

OLD = """    public static boolean requestNotificationPermission() {
        try {
            if (mActivity == null) {
                return false;
            }
            if (android.os.Build.VERSION.SDK_INT >= 33
                    && mActivity.checkSelfPermission(
                            "android.permission.POST_NOTIFICATIONS")
                            != PackageManager.PERMISSION_GRANTED) {
                mActivity.requestPermissions(
                        new String[]{"android.permission.POST_NOTIFICATIONS"},
                        8123);
                return false;
            }
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "TiebaSign perm request failed: " + t);
            return false;
        }
    }"""

NEW = """    public static boolean requestNotificationPermission() {
        try {
            if (mActivity == null) {
                return false;
            }
            if (android.os.Build.VERSION.SDK_INT < 33
                    || mActivity.checkSelfPermission(
                            "android.permission.POST_NOTIFICATIONS")
                            == PackageManager.PERMISSION_GRANTED) {
                return true;
            }
            final String perm = "android.permission.POST_NOTIFICATIONS";
            mActivity.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        mActivity.requestPermissions(
                                new String[]{perm}, 8123);
                    } catch (Throwable t) {
                        Log.w(TAG, "TiebaSign perm request failed: " + t);
                    }
                }
            });
            return false;
        } catch (Throwable t) {
            Log.w(TAG, "TiebaSign perm request failed: " + t);
            return false;
        }
    }"""


def main():
    ok = True
    for path in FILES:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError as e:
            print("MISSING {}: {}".format(path, e))
            ok = False
            continue
        if "final String perm" in text:
            print("ALREADY_PATCHED {}".format(path))
            continue
        if OLD not in text:
            print("ANCHOR_NOT_FOUND {}".format(path))
            ok = False
            continue
        text = text.replace(OLD, NEW, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("PATCHED {}".format(path))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
