package org.dsh.tiebaweb;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * TiebaSign APK 文件提供者（检查更新 / 安装）。
 *
 * 为什么自己写这个而不是用 androidx 的 FileProvider：
 * 本 dist 里**没有 androidx**（`grep -c androidx PythonActivity.java` == 0），
 * 为了一个安装入口去引入整套 androidx 依赖不值得，这 70 行就够了。
 *
 * 为什么需要它：Android 7 (API 24) 起禁止用 file:// 把文件交给别的应用读
 * （FileUriExposedException）。把 APK 交给系统安装器必须走 content://。
 *
 * URI 形状（只有一个）：
 *   content://<包名>.apkprovider/apk?p=<绝对路径>
 *
 * 安全边界（两层，缺一不可）：
 *   1. 本 provider 是 android:exported="false"，只有拿到我们**显式授予**的
 *      URI 权限的应用才能读，而授予是一次性的、只针对某一个 URI；
 *   2. resolve() 只放行「以本应用 getFilesDir() 为前缀、且以 .apk 结尾」的
 *      文件。就算 URI 被谁拿到，也读不到 /data 下别的文件。
 * 把路径放在查询参数里（而不是记在静态变量里）是为了无状态：App 进程在安装
 * 器启动前后被系统回收也不会让安装失败。
 */
public class ApkProvider extends ContentProvider {

    private static final String TAG = "TiebaSignApkProvider";
    private static final String MIME_APK =
            "application/vnd.android.package-archive";

    @Override
    public boolean onCreate() {
        return true;
    }

    /** 把 URI 解析成受允许的 APK 文件；任何不符合条件的输入都返回 null。 */
    private File resolve(Uri uri) {
        try {
            if (uri == null || getContext() == null) {
                return null;
            }
            String p = uri.getQueryParameter("p");
            if (p == null || p.length() == 0 || !p.endsWith(".apk")) {
                Log.w(TAG, "reject: not an .apk path");
                return null;
            }
            File f = new File(p);
            if (!f.isFile()) {
                Log.w(TAG, "reject: no such file");
                return null;
            }
            String base = getContext().getFilesDir().getCanonicalPath();
            String real = f.getCanonicalPath();
            if (!real.startsWith(base + File.separator)) {
                Log.w(TAG, "reject: outside app files dir");
                return null;
            }
            return f;
        } catch (Throwable t) {
            Log.w(TAG, "resolve failed: " + t);
            return null;
        }
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = resolve(uri);
        if (f == null) {
            return null;
        }
        // 安装器会问 _display_name / _size；两个都给得出。
        String[] want = (projection == null)
                ? new String[]{"_display_name", "_size"} : projection;
        MatrixCursor cur = new MatrixCursor(want, 1);
        Object[] row = new Object[want.length];
        for (int i = 0; i < want.length; i++) {
            if ("_display_name".equals(want[i])) {
                row[i] = f.getName();
            } else if ("_size".equals(want[i])) {
                row[i] = f.length();
            } else {
                row[i] = null;
            }
        }
        cur.addRow(row);
        return cur;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode)
            throws FileNotFoundException {
        File f = resolve(uri);
        if (f == null) {
            throw new FileNotFoundException("not an allowed apk uri");
        }
        return ParcelFileDescriptor.open(
                f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public String getType(Uri uri) {
        return MIME_APK;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("read-only provider");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("read-only provider");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection,
                      String[] selectionArgs) {
        throw new UnsupportedOperationException("read-only provider");
    }
}
