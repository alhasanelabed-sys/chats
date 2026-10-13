package com.majlis.app;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Build;
import android.os.Looper;

import com.google.android.gms.tasks.Tasks;
import com.google.firebase.FirebaseApp;
import com.google.firebase.FirebaseOptions;
import com.google.firebase.appcheck.FirebaseAppCheck;
import com.google.firebase.appcheck.playintegrity.PlayIntegrityAppCheckProviderFactory;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

/** App-owned Firebase configuration and Play Integrity; no user-provided server or API key. */
public final class FirebaseCloud {
    public static final String UNAVAILABLE = ManagedAiClient.UNAVAILABLE;
    private static final String CONFIG_ASSET = "cloud-config.json";
    private static FirebaseApp installedApp;
    private final Context context;
    private final ManagedAiClient.Configuration config;
    private ManagedAiClient client;

    /** Reads local configuration only. SDK initialization and attestation happen off the UI thread. */
    public FirebaseCloud(Context context) {
        this.context = context.getApplicationContext();
        ManagedAiClient.Configuration parsed = null;
        try { parsed = readConfiguration(this.context); } catch (Exception ignored) { }
        this.config = parsed;
    }

    /** Safe for the UI thread: no Firebase initialization, token request, network, or upload. */
    public static boolean configured(Context context) {
        try { return readConfiguration(context.getApplicationContext()) != null; }
        catch (Exception ignored) { return false; }
    }

    public synchronized JSONObject analyze(File audio, String title, JSONArray profiles, double duration) throws Exception {
        return ready().analyze(audio, title, profiles, duration);
    }
    public synchronized JSONObject summarize(JSONObject meeting) throws Exception { return ready().summarize(meeting); }
    public synchronized JSONObject translate(JSONObject meeting, String target) throws Exception { return ready().translate(meeting, target); }
    public synchronized JSONObject status() throws Exception { return ready().status(); }

    private ManagedAiClient ready() throws Exception {
        if (config == null) throw new Exception(UNAVAILABLE);
        if (Looper.myLooper() == Looper.getMainLooper())
            throw new Exception("تعذر بدء التحليل الآن. التسجيل محفوظ؛ حاول لاحقًا.");
        if (Thread.currentThread().isInterrupted())
            throw new Exception("أُلغي طلب التحليل. التسجيل والمحضر المحليان محفوظان.");
        if (client == null) {
            final FirebaseAppCheck appCheck;
            final String certificate;
            try {
                certificate = signingCertificate(context);
                appCheck = initialize(context, config);
            } catch (Exception ignored) { throw new Exception(UNAVAILABLE); }
            client = new ManagedAiClient(config, context.getPackageName(), certificate,
                    () -> Tasks.await(appCheck.getLimitedUseAppCheckToken(), 30, TimeUnit.SECONDS).getToken());
        }
        return client;
    }

    private static synchronized FirebaseAppCheck initialize(Context context, ManagedAiClient.Configuration config) throws Exception {
        FirebaseApp app;
        try { app = FirebaseApp.getInstance(); }
        catch (IllegalStateException missing) {
            // Play Integrity uses this project number to bind the attestation request.
            FirebaseOptions options = new FirebaseOptions.Builder().setProjectId(config.projectId)
                    .setGcmSenderId(config.projectNumber).setApiKey(config.apiKey).setApplicationId(config.appId).build();
            app = FirebaseApp.initializeApp(context, options);
        }
        if (app == null || !config.projectId.equals(app.getOptions().getProjectId())
                || !config.apiKey.equals(app.getOptions().getApiKey())
                || !config.appId.equals(app.getOptions().getApplicationId())
                || !config.projectNumber.equals(app.getOptions().getGcmSenderId())) throw new Exception();
        FirebaseAppCheck check = FirebaseAppCheck.getInstance(app);
        if (installedApp != app) {
            check.installAppCheckProviderFactory(PlayIntegrityAppCheckProviderFactory.getInstance());
            installedApp = app;
        }
        return check;
    }

    private static ManagedAiClient.Configuration readConfiguration(Context context) throws Exception {
        try (InputStream input = context.getAssets().open(CONFIG_ASSET); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096]; int count;
            while ((count = input.read(buffer)) != -1) {
                if (bytes.size() + count > 64 * 1024) throw new Exception();
                bytes.write(buffer, 0, count);
            }
            return ManagedAiClient.configuration(new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8)));
        }
    }

    @SuppressWarnings("deprecation")
    private static String signingCertificate(Context context) throws Exception {
        PackageManager manager = context.getPackageManager();
        Signature[] signatures;
        if (Build.VERSION.SDK_INT >= 28) {
            PackageInfo info = manager.getPackageInfo(context.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
            if (info.signingInfo == null) throw new Exception();
            signatures = info.signingInfo.getApkContentsSigners();
        } else {
            signatures = manager.getPackageInfo(context.getPackageName(), PackageManager.GET_SIGNATURES).signatures;
        }
        if (signatures == null || signatures.length != 1) throw new Exception();
        byte[] digest = MessageDigest.getInstance("SHA-1").digest(signatures[0].toByteArray());
        StringBuilder hex = new StringBuilder();
        for (byte value : digest) hex.append(String.format(Locale.ROOT, "%02X", value & 0xff));
        return hex.toString();
    }
}
