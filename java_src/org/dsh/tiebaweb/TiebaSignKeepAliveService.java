package org.dsh.tiebaweb;

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
            ch.setDescription("贴吧签到助手 自动签到保活服务");
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
                .setContentTitle("贴吧签到助手 自动签到运行中")
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
