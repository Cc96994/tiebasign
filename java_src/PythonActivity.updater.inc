// TiebaSign 检查更新 / 安装入口（PythonActivity.java 的拼接片段）
//
// 这个文件不是独立编译单元：scripts/patch_base_java.sh 会把它整段插进
//   <p4a.source_dir>/pythonforandroid/bootstraps/webview/build/src/main/java/
//       org/kivy/android/PythonActivity.java
// 的 `// >>> TiebaSignUpdaterMethods` 与 `// <<< TiebaSignUpdaterMethods`
// 两个标记之间（标记由脚本写入，重复执行会先删旧块再插新块，所以是幂等的）。
//
// 为什么不在仓库里直接改 PythonActivity.java：那是 p4a 的文件、在仓库之外，
// 每个开发者的机器上路径不同（这里默认 /home/s7909/p4a-develop）。把补丁做成
// 脚本 + 片段，底座就变成"可以从仓库重建"的，而不是"只有某台机器上那份对"。
//
// Python 侧是 app/updater.py，全部通过反射调用（autosign._call_static 同款），
// 方法名与签名是本片段与 updater.py 之间的硬契约：
//   installApk(String path)                      -> boolean
//   inspectApk(String path)                      -> "包名|versionCode|签名SHA256"
//   isOnWifi()                                   -> boolean
//   notifyUpdateReady(String title, String text, String apkPath) -> boolean
//
// 刻意用全限定名（android.net.ConnectivityManager 等）而不是加 import：这样
// 补丁只动一个位置，不会和 p4a 自己的 import 块打架；本文件里已有的
// android.os.Build.VERSION 也是同样的写法。

// ---- 检查更新：把 APK 交给系统安装器（不做静默安装） ----------------------
//
// 返回 false 的两种情形都是**如实**返回，绝不假装成功：
//   * 文件不存在/不是文件；
//   * Android 8+ 且用户还没允许"安装未知应用"——此时顺带把用户送到那个开关，
//     Python 侧会显示"系统拒绝打开安装界面"。
public static boolean installApk(String path) {
    try {
        if (mActivity == null || path == null || path.length() == 0) {
            return false;
        }
        File f = new File(path);
        if (!f.isFile()) {
            Log.w(TAG, "TiebaSign install: no such file");
            return false;
        }
        if (android.os.Build.VERSION.SDK_INT >= 26
                && !mActivity.getPackageManager().canRequestPackageInstalls()) {
            try {
                Intent s = new Intent(
                        android.provider.Settings
                                .ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + mActivity.getPackageName()));
                mActivity.startActivity(s);
            } catch (Throwable t) {
                Log.w(TAG, "TiebaSign unknown-sources settings failed: " + t);
            }
            Log.w(TAG, "TiebaSign install: unknown sources not allowed");
            return false;
        }
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(tiebaSignApkUri(path),
                "application/vnd.android.package-archive");
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        mActivity.startActivity(i);
        return true;
    } catch (Throwable t) {
        Log.w(TAG, "TiebaSign install failed: " + t);
        return false;
    }
}

/** 本应用私有目录里的 APK -> 交给系统安装器读的 content:// URI。 */
private static Uri tiebaSignApkUri(String path) {
    return Uri.parse("content://" + mActivity.getPackageName()
            + ".apkprovider/apk?p=" + Uri.encode(path));
}

// ---- 安装前三道校验：包名 / versionCode / 签名 ----------------------------
//
// 这就是"只允许自家密钥签出来的包被装上去"的执行点。sha256 是发布端给的，
// 单靠它挡不住被篡改的发布端；签名指纹才是真正的边界（别人伪造不出来）。
// 任一项读不到就返回空串，Python 侧会拒绝安装而不是放行。
public static String inspectApk(String path) {
    try {
        if (mActivity == null || path == null || path.length() == 0) {
            return "";
        }
        if (!new File(path).isFile()) {
            return "";
        }
        android.content.pm.PackageInfo pi =
                mActivity.getPackageManager().getPackageArchiveInfo(
                        path, PackageManager.GET_SIGNATURES);
        if (pi == null) {
            return "";
        }
        String pkg = (pi.packageName == null) ? "" : pi.packageName;
        long code = (android.os.Build.VERSION.SDK_INT >= 28)
                ? pi.getLongVersionCode() : (long) pi.versionCode;
        String signer = "";
        android.content.pm.Signature[] sigs = pi.signatures;
        if (sigs != null && sigs.length > 0) {
            java.security.MessageDigest md =
                    java.security.MessageDigest.getInstance("SHA-256");
            byte[] d = md.digest(sigs[0].toByteArray());
            StringBuilder sb = new StringBuilder(d.length * 2);
            for (int k = 0; k < d.length; k++) {
                int v = d[k] & 0xff;
                if (v < 16) {
                    sb.append('0');
                }
                sb.append(Integer.toHexString(v));
            }
            signer = sb.toString();
        }
        return pkg + "|" + code + "|" + signer;
    } catch (Throwable t) {
        Log.w(TAG, "TiebaSign inspectApk failed: " + t);
        return "";
    }
}

// ---- 网络类型：只在 Wi-Fi 下自动下载 -------------------------------------
//
// 读不到就返回 false（保守）。Python 侧把"方法不存在"当 None 处理，那是
// fail-open（宁可多下一次），因为方法不存在说明这套原生入口整个没装好，
// 而这里返回 false 是"确实不在 Wi-Fi 上"。两者含义不同，不要混。
public static boolean isOnWifi() {
    try {
        if (mActivity == null) {
            return false;
        }
        android.net.ConnectivityManager cm =
                (android.net.ConnectivityManager) mActivity.getSystemService(
                        Context.CONNECTIVITY_SERVICE);
        if (cm == null) {
            return false;
        }
        android.net.NetworkInfo ni = cm.getActiveNetworkInfo();
        return ni != null && ni.isConnected()
                && ni.getType() == android.net.ConnectivityManager.TYPE_WIFI;
    } catch (Throwable t) {
        Log.w(TAG, "TiebaSign isOnWifi failed: " + t);
        return false;
    }
}

// ---- 通知：发现新版本 / 新版本已下载 -------------------------------------
//
// 第三个参数 apkPath 是**可选**的：
//   * 非空且文件存在（下载完成后那条）-> 点通知直接进系统安装界面；
//   * 空（刚发现新版本那条）        -> 点通知打开 App，设置页里有下载入口。
// 通知渠道用独立的 tieba_sign_update，和签到失败提醒分开，用户可以单独关。
public static boolean notifyUpdateReady(String title, String text,
                                       String apkPath) {
    try {
        if (mActivity == null) {
            return false;
        }
        NotificationManager nm = (NotificationManager)
                mActivity.getSystemService(Context.NOTIFICATION_SERVICE);
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(
                    "tieba_sign_update", "新版本提醒",
                    NotificationManager.IMPORTANCE_DEFAULT);
            nm.createNotificationChannel(ch);
        }
        boolean have = apkPath != null && apkPath.length() > 0
                && new File(apkPath).isFile();
        Intent tap;
        if (have) {
            tap = new Intent(Intent.ACTION_VIEW);
            tap.setDataAndType(tiebaSignApkUri(apkPath),
                    "application/vnd.android.package-archive");
            tap.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } else {
            tap = new Intent(mActivity, PythonActivity.class);
            tap.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        }
        android.app.PendingIntent pi = android.app.PendingIntent.getActivity(
                mActivity, 31, tap,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT
                        | android.app.PendingIntent.FLAG_IMMUTABLE);
        int icon = mActivity.getResources().getIdentifier(
                "icon", "drawable", mActivity.getPackageName());
        if (icon == 0) {
            icon = android.R.drawable.stat_sys_download_done;
        }
        Notification.Builder b = (android.os.Build.VERSION.SDK_INT >= 26)
                ? new Notification.Builder(mActivity, "tieba_sign_update")
                : new Notification.Builder(mActivity);
        b.setSmallIcon(icon)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setAutoCancel(true);
        nm.notify(3, b.build());
        return true;
    } catch (Throwable t) {
        Log.w(TAG, "TiebaSign update notify failed: " + t);
        return false;
    }
}
