#!/usr/bin/env python3
# patch_webview_keepalive.py - add foreground keep-alive service + failure
# notification Java helpers to the p4a webview bootstrap. Idempotent.
# Run inside WSL: python3 /mnt/c/.../scripts/patch_webview_keepalive.py

import os
import sys

P4A = "/home/s7909/p4a-develop"
BB = ("/home/s7909/tieba_sign_android/.buildozer/android/platform/"
      "build-arm64-v8a/build/bootstrap_builds/webview")
DIST = ("/home/s7909/tieba_sign_android/.buildozer/android/platform/"
        "build-arm64-v8a/dists/tiebaweb")

ACTIVITY_REL = "src/main/java/org/kivy/android/PythonActivity.java"
SERVICE_REL = "src/main/java/org/dsh/tiebaweb/TiebaSignKeepAliveService.java"

ACTIVITY_PATHS = [
    os.path.join(P4A, "pythonforandroid/bootstraps/webview/build", ACTIVITY_REL),
    os.path.join(BB, ACTIVITY_REL),
    os.path.join(DIST, ACTIVITY_REL),
]

SERVICE_JAVA = r"""package org.dsh.tiebaweb;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/** TiebaSignKeepAliveMethods: foreground keep-alive service.
 * Keeps the Python process alive while the activity is swiped away.
 * PythonActivity.onDestroy skips killProcess while this service runs. */
public class TiebaSignKeepAliveService extends Service {
    public static final String CHANNEL_ID = "tieba_sign_service";
    private static final int NOTIF_ID = 1;
    private static volatile boolean sRunning = false;

    public static boolean isRunning() {
        return sRunning;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        sRunning = true;
        NotificationManager nm =
                (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "自动签到服务", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("TiebaSign 自动签到保活服务");
            nm.createNotificationChannel(ch);
        }
        int icon = getResources().getIdentifier(
                "icon", "drawable", getPackageName());
        if (icon == 0) {
            icon = android.R.drawable.stat_notify_sync;
        }
        Notification.Builder b = (Build.VERSION.SDK_INT >= 26)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        b.setSmallIcon(icon)
                .setContentTitle("TiebaSign 自动签到运行中")
                .setContentText("到点自动签到，仅在有失败时提醒")
                .setOngoing(true);
        Notification n = b.build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIF_ID, n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIF_ID, n);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        sRunning = false;
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
"""

METHODS = r"""
    // TiebaSignKeepAliveMethods (marker, idempotent)
    public static boolean startKeepAliveService() {
        try {
            if (mActivity == null) {
                return false;
            }
            Intent intent = new Intent(
                    mActivity, org.dsh.tiebaweb.TiebaSignKeepAliveService.class);
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                mActivity.startForegroundService(intent);
            } else {
                mActivity.startService(intent);
            }
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "TiebaSign keep-alive start failed: " + t);
            return false;
        }
    }

    public static boolean stopKeepAliveService() {
        try {
            if (mActivity == null) {
                return false;
            }
            Intent intent = new Intent(
                    mActivity, org.dsh.tiebaweb.TiebaSignKeepAliveService.class);
            mActivity.stopService(intent);
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "TiebaSign keep-alive stop failed: " + t);
            return false;
        }
    }

    public static boolean requestNotificationPermission() {
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
    }

    public static boolean notifySignFail(String title, String text) {
        try {
            if (mActivity == null) {
                return false;
            }
            NotificationManager nm = (NotificationManager)
                    mActivity.getSystemService(Context.NOTIFICATION_SERVICE);
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                NotificationChannel ch = new NotificationChannel(
                        "tieba_sign_fail", "自动签到失败提醒",
                        NotificationManager.IMPORTANCE_DEFAULT);
                nm.createNotificationChannel(ch);
            }
            int icon = mActivity.getResources().getIdentifier(
                    "icon", "drawable", mActivity.getPackageName());
            if (icon == 0) {
                icon = android.R.drawable.stat_notify_error;
            }
            Notification.Builder b = (android.os.Build.VERSION.SDK_INT >= 26)
                    ? new Notification.Builder(mActivity, "tieba_sign_fail")
                    : new Notification.Builder(mActivity);
            b.setSmallIcon(icon)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setAutoCancel(true);
            nm.notify(2, b.build());
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "TiebaSign fail notify failed: " + t);
            return false;
        }
    }
"""


def patch_activity(path):
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    changed = False

    if "TiebaSignKeepAliveMethods" in src:
        print("  activity already patched: " + path)
        return

    # imports
    for imp in ("import android.app.Notification;",
                "import android.app.NotificationChannel;",
                "import android.app.NotificationManager;"):
        if imp not in src:
            src = src.replace("import android.app.AlertDialog;",
                              "import android.app.AlertDialog;\n" + imp, 1)
            changed = True

    # static flag
    if "public static boolean sPythonStarted" not in src:
        anchor = "    protected static Thread mPythonThread;\n"
        if anchor not in src:
            raise RuntimeError("mPythonThread anchor missing in " + path)
        src = src.replace(anchor, anchor +
                          "    public static boolean sPythonStarted = false;\n",
                          1)
        changed = True

    # helper methods before lastBackClick
    anchor = "    long lastBackClick = 0;\n"
    if anchor not in src:
        raise RuntimeError("lastBackClick anchor missing in " + path)
    src = src.replace(anchor, METHODS + anchor, 1)
    changed = True

    # keep process alive while the service runs
    old = ("        // make sure all child threads (python_thread) are stopped\n"
           "        android.os.Process.killProcess(android.os.Process.myPid());\n")
    new = ("        // TiebaSignKeepAliveMethods: keep the process if the\n"
           "        // foreground keep-alive service is running\n"
           "        if (!org.dsh.tiebaweb.TiebaSignKeepAliveService.isRunning()) {\n"
           "            // make sure all child threads (python_thread) are stopped\n"
           "            android.os.Process.killProcess(android.os.Process.myPid());\n"
           "        }\n")
    if old not in src:
        raise RuntimeError("killProcess anchor missing in " + path)
    src = src.replace(old, new, 1)
    changed = True

    # do not start a second Python interpreter on activity re-entry
    old = ("            final Thread pythonThread = new Thread(new PythonMain(), \"PythonThread\");\n"
           "            PythonActivity.mPythonThread = pythonThread;\n"
           "            pythonThread.start();\n"
           "\n"
           "            final Thread wvThread = new Thread(new WebViewLoaderMain(), \"WvThread\");\n"
           "            wvThread.start();\n")
    new = ("            if (!PythonActivity.sPythonStarted) {\n"
           "                final Thread pythonThread = new Thread(new PythonMain(), \"PythonThread\");\n"
           "                PythonActivity.mPythonThread = pythonThread;\n"
           "                pythonThread.start();\n"
           "                PythonActivity.sPythonStarted = true;\n"
           "            }\n"
           "\n"
           "            final Thread wvThread = new Thread(new WebViewLoaderMain(), \"WvThread\");\n"
           "            wvThread.start();\n")
    if old not in src:
        raise RuntimeError("pythonThread start anchor missing in " + path)
    src = src.replace(old, new, 1)
    changed = True

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print("  activity patched: " + path)


def write_service(path):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            if "TiebaSignKeepAliveMethods" in fh.read():
                print("  service already present: " + path)
                return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SERVICE_JAVA)
    print("  service written: " + path)


def patch_manifest():
    tmpls = [
        os.path.join(P4A,
                     "pythonforandroid/bootstraps/webview/build/templates/"
                     "AndroidManifest.tmpl.xml"),
        os.path.join(BB, "templates/AndroidManifest.tmpl.xml"),
    ]
    entry = ('        <service android:name="org.dsh.tiebaweb.'
             'TiebaSignKeepAliveService"\n'
             '                 android:exported="false"\n'
             '                 android:foregroundServiceType="dataSync"\n'
             '                 android:stopWithTask="false" />\n')
    for path in tmpls:
        if not os.path.exists(path):
            print("  manifest template not found (skip): " + path)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        if "TiebaSignKeepAliveService" in src:
            print("  manifest already patched: " + path)
            continue
        anchor = "    </application>\n"
        if anchor not in src:
            raise RuntimeError("manifest anchor missing in " + path)
        src = src.replace(anchor, entry + anchor, 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        print("  manifest patched: " + path)


def main():
    for p in ACTIVITY_PATHS:
        patch_activity(p)
    for root in (os.path.join(P4A, "pythonforandroid/bootstraps/webview/build"),
                 BB, DIST):
        write_service(os.path.join(root, SERVICE_REL))
    patch_manifest()
    print("PATCH_KEEPALIVE_DONE")


if __name__ == "__main__":
    main()
