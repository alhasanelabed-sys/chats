package com.majlis.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import java.io.File;
import java.io.IOException;
import java.util.Locale;

/** Owns the microphone for one explicitly started, app-private meeting recording. */
public final class RecordingService extends Service {
    public static final String ACTION_START = "com.majlis.app.START";
    public static final String ACTION_STOP = "com.majlis.app.STOP";
    public static final String ACTION_PAUSE = "com.majlis.app.PAUSE";
    public static final String ACTION_RESUME = "com.majlis.app.RESUME";
    public static final String ACTION_UPDATE = "com.majlis.app.RECORDING_UPDATE";
    public static final String EXTRA_PATH = "path";
    public static final String EXTRA_STATE = "state";
    public static final String EXTRA_ELAPSED_MS = "elapsed_ms";
    public static final String EXTRA_AMPLITUDE = "amplitude";
    public static final String EXTRA_ERROR = "error";

    public static final int MAX_DURATION_MS = 60 * 60 * 1000;
    private static final String CHANNEL_ID = "meeting_recording";
    private static final int NOTIFICATION_ID = 2101;
    private static final long LEVEL_INTERVAL_MS = 500L;

    private static final RecordingClock clock = new RecordingClock();
    private static volatile String selectedPath = "";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private MediaRecorder recorder;
    private File outputFile;
    private PowerManager.WakeLock wakeLock;
    private boolean foreground;

    public static boolean isRecording() {
        return clock.isRecording();
    }

    public static boolean isPaused() {
        return clock.isPaused();
    }

    public static String currentPath() {
        return selectedPath;
    }

    public static long elapsedMillis() {
        return clock.elapsedMillis(SystemClock.elapsedRealtime());
    }

    private final Runnable levels = new Runnable() {
        @Override public void run() {
            if (!isRecording() || recorder == null) return;
            if (elapsedMillis() >= MAX_DURATION_MS) {
                finishRecording(true);
                return;
            }
            try {
                // Keep the UI timer frozen and never sample a paused microphone.
                boolean paused = isPaused();
                sendUpdate(paused ? "paused" : "recording",
                        paused ? 0 : recorder.getMaxAmplitude(), null, false);
                handler.postDelayed(this, LEVEL_INTERVAL_MS);
            } catch (RuntimeException exception) {
                failRecording("تعذر متابعة التسجيل. قد يكون الميكروفون غير متاح؛ أعد المحاولة.");
            }
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "تسجيل الاجتماع", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("إشعار ظاهر طوال جلسة التسجيل، مع أزرار الإيقاف والاستئناف");
        channel.setSound(null, null);
        channel.enableVibration(false);
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(channel);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_START.equals(action)) {
            if (isRecording()) {
                // An accidental second tap must not replace or interrupt the active file.
                sendUpdate(isPaused() ? "paused" : "recording", 0, null, false);
            } else {
                startRecording(intent.getStringExtra(EXTRA_PATH));
            }
        } else if (ACTION_STOP.equals(action)) {
            if (isRecording()) finishRecording(false);
            else stopSelf();
        } else if (ACTION_PAUSE.equals(action)) {
            if (isRecording()) pauseRecording();
            else stopSelf();
        } else if (ACTION_RESUME.equals(action)) {
            if (isRecording()) resumeRecording();
            else stopSelf();
        } else if (!isRecording()) {
            stopSelf();
        }
        // Never restart the microphone after process death without a new explicit start.
        return START_NOT_STICKY;
    }

    private void startRecording(String requestedPath) {
        selectedPath = "";
        clock.reset();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            failRecording("اسمح باستخدام الميكروفون من إعدادات التطبيق قبل بدء التسجيل.");
            return;
        }

        try {
            outputFile = reserveOutputFile(requestedPath);
            selectedPath = outputFile.getAbsolutePath();
            Notification notification = recordingNotification();
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                startForeground(NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
            foreground = true;

            acquireWakeLock();

            recorder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                    ? new MediaRecorder(this) : new MediaRecorder();
            recorder.setAudioSource(MediaRecorder.AudioSource.MIC);
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            recorder.setAudioChannels(1);
            // One hour of mono AAC at 48 kbps is about 21.6 MB before container overhead.
            recorder.setAudioSamplingRate(32_000);
            recorder.setAudioEncodingBitRate(48_000);
            recorder.setMaxDuration(MAX_DURATION_MS);
            recorder.setOutputFile(selectedPath);
            recorder.setOnErrorListener((source, what, extra) -> {
                if (source == recorder) {
                    failRecording("توقف الميكروفون بسبب خطأ. لم يُحفظ تسجيل صالح لهذه المحاولة.");
                }
            });
            recorder.setOnInfoListener((source, what, extra) -> {
                if (source == recorder && isRecording()
                        && what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED) {
                    finishRecording(true);
                }
            });
            recorder.prepare();
            recorder.start();
            clock.start(SystemClock.elapsedRealtime());
            getSharedPreferences("majlis", MODE_PRIVATE).edit()
                    .putBoolean("recording_incomplete", true).commit();
            sendUpdate("recording", 0, null, false);
            handler.postDelayed(levels, LEVEL_INTERVAL_MS);
        } catch (SecurityException exception) {
            failRecording("تعذر تشغيل الميكروفون. تحقق من الإذن وافتح التطبيق ثم حاول مجددًا.");
        } catch (IOException exception) {
            failRecording("تعذر إنشاء ملف التسجيل داخل التطبيق. تحقق من مساحة التخزين وأعد المحاولة.");
        } catch (IllegalArgumentException exception) {
            failRecording("مسار التسجيل غير صالح. يجب حفظ ملف جديد بصيغة m4a داخل تسجيلات التطبيق.");
        } catch (RuntimeException exception) {
            failRecording("تعذر بدء التسجيل. قد يكون الميكروفون مستخدمًا أو غير متاح.");
        }
    }

    private void pauseRecording() {
        if (!isRecording() || isPaused() || recorder == null) return;
        if (elapsedMillis() >= MAX_DURATION_MS) {
            finishRecording(true);
            return;
        }
        try {
            recorder.pause();
            clock.pause(SystemClock.elapsedRealtime());
            releaseWakeLock();
            refreshNotification();
            sendUpdate("paused", 0, null, false);
        } catch (RuntimeException exception) {
            failRecording("تعذر إيقاف التسجيل مؤقتًا. تحقق من الميكروفون وأعد المحاولة.");
        }
    }

    private void resumeRecording() {
        if (!isRecording() || !isPaused() || recorder == null) return;
        if (elapsedMillis() >= MAX_DURATION_MS) {
            finishRecording(true);
            return;
        }
        try {
            acquireWakeLock();
            recorder.resume();
            clock.resume(SystemClock.elapsedRealtime());
            refreshNotification();
            sendUpdate("recording", 0, null, false);
        } catch (RuntimeException exception) {
            failRecording("تعذر استئناف التسجيل. تحقق من الميكروفون وأعد المحاولة.");
        }
    }

    private void refreshNotification() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (foreground && manager != null) {
            manager.notify(NOTIFICATION_ID, recordingNotification());
        }
    }

    private File reserveOutputFile(String path) throws IOException {
        if (path == null || path.isEmpty()) {
            throw new IllegalArgumentException("Missing recording path");
        }
        File requested = new File(path);
        if (!requested.isAbsolute()
                || !requested.getName().toLowerCase(Locale.ROOT).endsWith(".m4a")) {
            throw new IllegalArgumentException("Expected absolute m4a path");
        }
        File privateFiles = getFilesDir().getCanonicalFile();
        File recordings = new File(privateFiles, "recordings");
        if (!recordings.exists() && !recordings.mkdirs()) {
            throw new IOException("Could not create recording directory");
        }
        File canonicalRecordings = recordings.getCanonicalFile();
        File canonicalOutput = requested.getCanonicalFile();
        // Reject traversal, external output, nested directories, and redirected roots.
        if (!canonicalRecordings.isDirectory()
                || !recordings.equals(canonicalRecordings)
                || !privateFiles.equals(canonicalRecordings.getParentFile())
                || !canonicalRecordings.equals(canonicalOutput.getParentFile())) {
            throw new IllegalArgumentException("Output must be app-private");
        }
        // Do not overwrite either a completed recording or another file.
        if (!canonicalOutput.createNewFile()) {
            throw new IllegalArgumentException("Output file already exists");
        }
        return canonicalOutput;
    }

    private Notification recordingNotification() {
        Intent open = new Intent(this, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent openApp = PendingIntent.getActivity(this, 2101, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, RecordingService.class).setAction(ACTION_STOP);
        PendingIntent stopRecording = PendingIntent.getService(this, 2102, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        boolean paused = isPaused();
        Intent toggle = new Intent(this, RecordingService.class)
                .setAction(paused ? ACTION_RESUME : ACTION_PAUSE);
        PendingIntent toggleRecording = PendingIntent.getService(this,
                paused ? 2104 : 2103, toggle,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle(paused ? "مجلس — التسجيل متوقف مؤقتًا" : "مجلس — التسجيل جارٍ")
                .setContentText(paused ? "التسجيل متوقف مؤقتًا · تابع لإكمال الاجتماع"
                        : "الميكروفون نشط · اضغط للعودة إلى الاجتماع")
                .setContentIntent(openApp)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setWhen(System.currentTimeMillis() - elapsedMillis())
                .setShowWhen(!paused)
                .setUsesChronometer(!paused)
                .addAction(new Notification.Action.Builder(R.drawable.ic_mic,
                        paused ? "متابعة التسجيل" : "إيقاف مؤقت", toggleRecording).build())
                .addAction(new Notification.Action.Builder(R.drawable.ic_mic,
                        "إنهاء التسجيل", stopRecording).build());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }
        return builder.build();
    }

    private void finishRecording(boolean durationLimit) {
        if (!isRecording() || recorder == null) return;
        handler.removeCallbacks(levels);
        clock.stop(SystemClock.elapsedRealtime());
        boolean stopped = false;
        try {
            recorder.setOnErrorListener(null);
            recorder.setOnInfoListener(null);
            recorder.stop();
            stopped = outputFile != null && outputFile.isFile() && outputFile.length() > 0L;
        } catch (RuntimeException exception) {
            // MediaRecorder rejects very short clips or a disconnected microphone.
        } finally {
            releaseRecorder();
            releaseWakeLock();
        }
        if (stopped) {
            getSharedPreferences("majlis", MODE_PRIVATE).edit()
                    .putBoolean("recording_incomplete", false)
                    .putString("last_audio", selectedPath).commit();
            sendUpdate("stopped", 0, null, durationLimit);
            outputFile = null;
        } else {
            getSharedPreferences("majlis", MODE_PRIVATE).edit()
                    .putBoolean("recording_incomplete", false).remove("last_audio").commit();
            discardIncompleteFile();
            sendUpdate("error", 0,
                    "لم يُحفظ تسجيل صالح. سجّل لثوانٍ قليلة على الأقل وتحقق من الميكروفون.", false);
        }
        leaveForeground();
        stopSelf();
    }

    private void failRecording(String message) {
        getSharedPreferences("majlis", MODE_PRIVATE).edit()
                .putBoolean("recording_incomplete", false).remove("last_audio").commit();
        handler.removeCallbacks(levels);
        boolean wasRecording = isRecording();
        clock.stop(SystemClock.elapsedRealtime());
        if (recorder != null) {
            try {
                recorder.setOnErrorListener(null);
                recorder.setOnInfoListener(null);
                if (wasRecording) recorder.stop();
            } catch (RuntimeException exception) {
                // Cleanup remains required when stop itself fails.
            }
        }
        releaseRecorder();
        releaseWakeLock();
        discardIncompleteFile();
        sendUpdate("error", 0, message, false);
        leaveForeground();
        stopSelf();
    }

    private void acquireWakeLock() {
        releaseWakeLock();
        PowerManager power = getSystemService(PowerManager.class);
        if (power != null) {
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,
                    "Majlis:MeetingRecording");
            wakeLock.setReferenceCounted(false);
            // A long pause never holds the CPU awake. A resume acquires only time remaining.
            long remainingMillis = Math.max(1L, MAX_DURATION_MS - elapsedMillis());
            wakeLock.acquire(remainingMillis + 30_000L);
        }
    }

    private void releaseRecorder() {
        MediaRecorder released = recorder;
        recorder = null;
        if (released != null) {
            try {
                released.release();
            } catch (RuntimeException exception) {
                // Keep the remaining cleanup independent of the native recorder.
            }
        }
    }

    private void releaseWakeLock() {
        PowerManager.WakeLock released = wakeLock;
        wakeLock = null;
        try {
            if (released != null && released.isHeld()) released.release();
        } catch (RuntimeException exception) {
            // Its bounded timeout may have expired while shutdown was in progress.
        }
    }

    private void discardIncompleteFile() {
        if (outputFile != null) {
            // This file was newly reserved by this service; existing files are rejected.
            outputFile.delete();
            outputFile = null;
        }
    }

    private void leaveForeground() {
        if (foreground) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            foreground = false;
        }
    }

    private void sendUpdate(String state, int amplitude, String error, boolean durationLimit) {
        Intent update = new Intent(ACTION_UPDATE).setPackage(getPackageName())
                .putExtra(EXTRA_STATE, state)
                .putExtra(EXTRA_PATH, selectedPath)
                .putExtra(EXTRA_ELAPSED_MS, elapsedMillis())
                .putExtra(EXTRA_AMPLITUDE, Math.max(0, amplitude));
        if (error != null) update.putExtra(EXTRA_ERROR, error);
        if (durationLimit) {
            update.putExtra("reason", "duration_limit");
            update.putExtra("message", "اكتمل التسجيل عند الحد الأقصى: ٦٠ دقيقة من الصوت.");
        }
        sendBroadcast(update);
    }

    @Override public void onTaskRemoved(Intent rootIntent) {
        // An ongoing, visibly announced meeting continues while the screen is locked
        // or the activity is dismissed. The notification always exposes Stop.
        super.onTaskRemoved(rootIntent);
    }

    @Override public void onDestroy() {
        if (isRecording()) finishRecording(false);
        handler.removeCallbacks(levels);
        releaseRecorder();
        releaseWakeLock();
        leaveForeground();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) {
        return null;
    }
}
