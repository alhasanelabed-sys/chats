package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Firebase AI Logic requests. The activity must obtain upload consent before calling. */
public final class ManagedAiClient {
    public static final String UNAVAILABLE = "خدمة التحليل غير متاحة في هذه النسخة. التسجيل محفوظ؛ حاول بعد تحديث التطبيق.";
    public static final String DEFAULT_MODEL = "gemini-3.5-flash-lite";
    private static final String ENDPOINT = "https://firebasevertexai.googleapis.com/v1beta/projects/";
    private static final int MAX_AUDIO = 13_000_000, MAX_RESPONSE = 4 * 1024 * 1024;
    private static final int MAX_JSON = 2_000_000, MAX_INLINE_REQUEST = 18_000_000;
    private static final int READ_TIMEOUT = 15 * 60 * 1000;

    interface TokenProvider { String limitedUseToken() throws Exception; }
    interface ConnectionFactory { HttpURLConnection open(URI officialUri) throws Exception; }
    private static final ThreadLocal<ConnectionFactory> TEST_CONNECTIONS = new ThreadLocal<>();
    static void setConnectionsForTests(ConnectionFactory factory) {
        if (factory == null) TEST_CONNECTIONS.remove(); else TEST_CONNECTIONS.set(factory);
    }

    static final class Configuration {
        final String projectId, projectNumber, apiKey, appId, model;
        Configuration(String projectId, String projectNumber, String apiKey, String appId, String model) {
            this.projectId = projectId; this.projectNumber = projectNumber;
            this.apiKey = apiKey; this.appId = appId; this.model = model;
        }
    }

    /** Public Firebase configuration identifies the app; it is never a Gemini API secret. */
    static Configuration configuration(JSONObject value) throws Exception {
        try {
            if (value == null || !(value.opt("enabled") instanceof Boolean) || !value.getBoolean("enabled"))
                throw new Exception();
            String project = requireString(value, "project_id", 30, true);
            String key = requireString(value, "api_key", 512, true);
            String app = requireString(value, "app_id", 160, true);
            Object configuredModel = value.opt("model");
            if (configuredModel != null && !(configuredModel instanceof String)) throw new Exception();
            String model = configuredModel == null || ((String) configuredModel).trim().isEmpty()
                    ? DEFAULT_MODEL : ((String) configuredModel).trim();
            if (!project.matches("[a-z][a-z0-9-]{4,28}[a-z0-9]")
                    || !app.matches("1:[0-9]+:android:[0-9a-fA-F]+")
                    || !key.matches("AIza[A-Za-z0-9_-]{35}")
                    || !model.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,119}")) throw new Exception();
            return new Configuration(project, projectNumber(app), key, app, model);
        } catch (Exception ignored) { throw new Failure(UNAVAILABLE); }
    }

    static String projectNumber(String appId) throws Exception {
        try {
            String number = appId.split(":")[1];
            if (Long.parseLong(number) <= 0) throw new Exception();
            return number;
        } catch (Exception ignored) { throw new Failure(UNAVAILABLE); }
    }

    private final Configuration config;
    private final String packageName, certificate;
    private final TokenProvider tokens;

    ManagedAiClient(Configuration config, String packageName, String certificate, TokenProvider tokens) throws Exception {
        if (config == null || !"com.majlis.app".equals(packageName) || certificate == null
                || !certificate.matches("[0-9A-F]{40}") || tokens == null) throw new Failure(UNAVAILABLE);
        this.config = config; this.packageName = packageName; this.certificate = certificate; this.tokens = tokens;
    }

    /** Stored profiles stay local. Voice labels are anonymous and names can be assigned manually. */
    public synchronized JSONObject analyze(File audio, String title, JSONArray profiles, double duration) throws Exception {
        try {
            checkCancelled();
            validateAudio(audio);
            if (!finite(duration) || duration <= 0 || duration > 3600)
                throw new Failure("يجب أن يكون التسجيل قابلًا للقراءة وألا تزيد مدته على ساعة.");
            String heading = title == null ? "" : title.trim();
            if (heading.isEmpty() || heading.length() > 160)
                throw new Failure("أدخل عنوان اجتماع بين حرف واحد و160 حرفًا.");
            long expected = audio.length();
            byte[] bytes;
            try (InputStream input = new FileInputStream(audio)) { bytes = readBytes(input, MAX_AUDIO); }
            if (bytes.length != expected) throw invalidResponse();
            JSONObject inline = new JSONObject().put("inlineData", new JSONObject().put("mimeType", mime(audio))
                    .put("data", Base64.getEncoder().encodeToString(bytes)));
            JSONObject raw = structured(ProviderPrompts.TRANSCRIBE,
                    new JSONObject().put("actual_duration_seconds", duration), ProviderPrompts.transcriptSchema(), inline);
            return summarizeTranscript(normalizeTranscript(raw.getJSONArray("segments"), heading, duration));
        } catch (Failure e) { throw e; }
        catch (SocketTimeoutException e) { throw timeout(); }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (IOException e) { throw networkFailure(); }
        catch (Exception e) { throw invalidResponse(); }
    }

    /** Sends only approved transcript fields, preserving local names and user corrections. */
    public synchronized JSONObject summarize(JSONObject meeting) throws Exception {
        try {
            checkCancelled();
            return summarizeTranscript(MeetingApi.transcriptPayload(meeting));
        } catch (Failure e) { throw e; }
        catch (SocketTimeoutException e) { throw timeout(); }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (IOException e) { throw networkFailure(); }
        catch (Exception e) { throw invalidResponse(); }
    }

    /** Audio, stored profiles, notes, previous translations and credentials never enter this payload. */
    public synchronized JSONObject translate(JSONObject meeting, String target) throws Exception {
        try {
            checkCancelled();
            if (target == null || !target.matches("en|fr|de|es|tr|he|ru|el|uk|zh|fa|ur"))
                throw new Failure("اختر لغة ترجمة مدعومة من قائمة اللغات.");
            JSONObject clean = MeetingApi.analysisFields(meeting);
            validateTranscript(clean);
            validateSchema(clean.getJSONObject("minutes"), ProviderPrompts.minutesSchema());
            JSONObject result = structured(ProviderPrompts.TRANSLATION,
                    new JSONObject().put("meeting", clean).put("target_language", target),
                    ProviderPrompts.translationSchema(), null);
            return MeetingApi.translationResponse(result.toString(), target, clean.getJSONArray("segments"));
        } catch (Failure e) { throw e; }
        catch (SocketTimeoutException e) { throw timeout(); }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (IOException e) { throw networkFailure(); }
        catch (Exception e) { throw invalidResponse(); }
    }

    /** Verifies app attestation only. No model call, billable generation or meeting data is sent. */
    public synchronized JSONObject status() throws Exception {
        checkCancelledOrFailure();
        token();
        return new JSONObject().put("status", "ok").put("provider", "firebase").put("model", config.model)
                .put("availability", "attestation_only").put("max_audio_bytes", MAX_AUDIO)
                .put("max_duration_seconds", 3600).put("reference_speakers", 0)
                .put("formats", new JSONArray().put("m4a").put("mp3").put("wav").put("aac"));
    }

    private JSONObject summarizeTranscript(JSONObject transcript) throws Exception {
        validateTranscript(transcript);
        JSONObject clean = new JSONObject(transcript.toString());
        clean.remove("minutes");
        JSONObject minutes;
        if (clean.getJSONArray("segments").length() == 0) {
            minutes = new JSONObject().put("summary", "لم يُكتشف كلام واضح في التسجيل.")
                    .put("discussion_points", new JSONArray()).put("decisions", new JSONArray())
                    .put("action_items", new JSONArray()).put("open_questions", new JSONArray())
                    .put("speech_annotations", new JSONArray());
        } else {
            minutes = structured(ProviderPrompts.SUMMARY, clean, ProviderPrompts.minutesSchema(), null);
            if (minutes.getJSONArray("speech_annotations").length() != clean.getJSONArray("segments").length())
                throw invalidResponse();
        }
        clean.put("minutes", minutes);
        JSONObject result = MeetingApi.analysisResponse(clean.toString());
        result.put("language", transcriptLanguage(minutes.getJSONArray("speech_annotations")));
        return result;
    }

    /** Annotations estimate language from transcript text; Arabic minutes do not identify the source language. */
    private static String transcriptLanguage(JSONArray annotations) throws Exception {
        Set<String> known = new HashSet<>();
        for (int i = 0; i < annotations.length(); i++) {
            String language = annotations.getJSONObject(i).getString("language");
            if (language.equals("mul")) return "mul";
            if (!language.equals("unknown")) known.add(language);
        }
        return known.isEmpty() ? "unknown" : known.size() > 1 ? "mul" : known.iterator().next();
    }

    private JSONObject structured(String instructions, JSONObject payload, JSONObject schema, JSONObject inline) throws Exception {
        String source = payload.toString();
        if (source.getBytes(StandardCharsets.UTF_8).length > MAX_JSON)
            throw new Failure("النص يتجاوز الحد المسموح للمعالجة. المحضر محفوظ محليًا.");
        JSONArray parts = new JSONArray();
        if (inline != null) parts.put(inline);
        parts.put(new JSONObject().put("text", source));
        JSONObject request = new JSONObject().put("systemInstruction", new JSONObject().put("parts", new JSONArray()
                        .put(new JSONObject().put("text", instructions))))
                .put("contents", new JSONArray().put(new JSONObject().put("role", "user").put("parts", parts)))
                .put("generationConfig", new JSONObject().put("responseMimeType", "application/json")
                        .put("responseJsonSchema", schema));
        JSONObject envelope = new JSONObject(request(request, inline != null));
        if (envelope.has("promptFeedback") && envelope.getJSONObject("promptFeedback").has("blockReason"))
            throw invalidResponse();
        JSONArray candidates = envelope.getJSONArray("candidates");
        if (candidates.length() != 1) throw invalidResponse();
        JSONObject candidate = candidates.getJSONObject(0);
        if (!"STOP".equals(candidate.optString("finishReason"))) throw invalidResponse();
        JSONArray safety = candidate.optJSONArray("safetyRatings");
        if (safety != null) for (int i = 0; i < safety.length(); i++)
            if (safety.getJSONObject(i).optBoolean("blocked", false)) throw invalidResponse();
        JSONArray output = candidate.getJSONObject("content").getJSONArray("parts");
        StringBuilder text = new StringBuilder();
        for (int i = 0; i < output.length(); i++) {
            JSONObject part = output.getJSONObject(i);
            // A thought marker does not authorize any executable or multimodal output.
            if (part.has("functionCall") || part.has("functionResponse") || part.has("inlineData") || part.has("fileData")
                    || part.has("executableCode") || part.has("codeExecutionResult"))
                throw invalidResponse();
            if (part.optBoolean("thought", false)) continue;
            text.append(requireString(part, "text", MAX_RESPONSE, false));
            if (text.length() > MAX_RESPONSE) throw invalidResponse();
        }
        if (text.toString().trim().isEmpty()) throw invalidResponse();
        JSONObject result = new JSONObject(text.toString());
        validateSchema(result, schema);
        return result;
    }

    private String request(JSONObject payload, boolean audio) throws Exception {
        checkCancelled();
        byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
        if (bytes.length > (audio ? MAX_INLINE_REQUEST : MAX_JSON + 64 * 1024))
            throw new Failure("حجم الطلب يتجاوز الحد المسموح. التسجيل والمحضر محفوظان محليًا.");
        String attestation = token();
        URI uri = new URI(ENDPOINT + config.projectId + "/models/" + config.model + ":generateContent");
        HttpURLConnection connection = null;
        try {
            checkCancelled();
            ConnectionFactory factory = TEST_CONNECTIONS.get();
            connection = factory == null ? (HttpURLConnection) uri.toURL().openConnection() : factory.open(uri);
            connection.setInstanceFollowRedirects(false); connection.setRequestMethod("POST");
            connection.setConnectTimeout(15_000); connection.setReadTimeout(READ_TIMEOUT); connection.setUseCaches(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            connection.setRequestProperty("x-goog-api-key", config.apiKey);
            connection.setRequestProperty("X-Firebase-AppCheck", attestation);
            connection.setRequestProperty("X-Android-Package", packageName);
            connection.setRequestProperty("X-Android-Cert", certificate);
            connection.setRequestProperty("X-Firebase-AppId", config.appId);
            connection.setRequestProperty("X-Firebase-AppVersion", "6");
            connection.setDoOutput(true); connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                for (int i = 0; i < bytes.length; i += 16 * 1024) {
                    checkCancelled(); output.write(bytes, i, Math.min(16 * 1024, bytes.length - i));
                }
            }
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) {
                // Error bodies can contain tokens or source material. Never display, log, or retry them.
                try (InputStream ignored = connection.getErrorStream()) { }
                if (code == 401 || code == 403) throw attestationFailure();
                if (code == 429) throw new Failure("بلغت خدمة التحليل حد الاستخدام مؤقتًا. التسجيل محفوظ؛ حاول لاحقًا.");
                if (code >= 300 && code < 400) throw new Failure("رُفض تحويل طلب التحليل إلى عنوان آخر. التسجيل محفوظ؛ حاول لاحقًا.");
                throw new Failure("خدمة التحليل غير متاحة حاليًا. التسجيل والمحضر محفوظان؛ حاول لاحقًا.");
            }
            if (connection.getContentLengthLong() > MAX_RESPONSE) throw invalidResponse();
            try (InputStream input = connection.getInputStream()) {
                return new String(readBytes(input, MAX_RESPONSE), StandardCharsets.UTF_8);
            }
        } finally { if (connection != null) connection.disconnect(); }
    }

    private String token() throws Exception {
        try {
            checkCancelled();
            String value = tokens.limitedUseToken();
            checkCancelled();
            if (!headerValue(value, 8192)) throw attestationFailure();
            return value;
        } catch (InterruptedException e) { Thread.currentThread().interrupt(); throw cancelled(); }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (Exception e) { throw attestationFailure(); }
    }

    private static JSONObject normalizeTranscript(JSONArray source, String title, double duration) throws Exception {
        if (source.length() > 2000) throw invalidResponse();
        JSONArray segments = new JSONArray(), speakers = new JSONArray();
        Map<String, String> speakerIds = new LinkedHashMap<>();
        Set<String> ids = new HashSet<>();
        long chars = 0;
        for (int i = 0; i < source.length(); i++) {
            JSONObject raw = source.getJSONObject(i);
            String id = requireString(raw, "id", 128, true), tag = requireString(raw, "speaker", 128, true);
            String text = requireString(raw, "text", 20000, true);
            double start = requireNumber(raw, "start"), end = requireNumber(raw, "end");
            if (!ids.add(id) || start < 0 || end < start || end > duration + 1 || (chars += text.length()) > 300000)
                throw invalidResponse();
            String speakerId = speakerIds.get(tag);
            if (speakerId == null) {
                if (speakerIds.size() >= 128) throw invalidResponse();
                speakerId = "speaker_" + (speakerIds.size() + 1);
                speakerIds.put(tag, speakerId);
                speakers.put(new JSONObject().put("id", speakerId).put("name", "متحدث " + speakerIds.size())
                        .put("matched_reference", false));
            }
            segments.put(new JSONObject().put("id", id).put("speaker_id", speakerId)
                    .put("start", start).put("end", end).put("text", text));
        }
        JSONObject result = new JSONObject().put("title", title).put("language", "unknown").put("duration_seconds", duration)
                .put("segments", segments).put("speakers", speakers);
        validateTranscript(result);
        return result;
    }

    private static void validateTranscript(JSONObject transcript) throws Exception {
        requireString(transcript, "title", 160, true); requireString(transcript, "language", 80, true);
        double duration = requireNumber(transcript, "duration_seconds");
        if (duration < 0 || duration > 3600) throw invalidResponse();
        JSONArray speakers = transcript.getJSONArray("speakers"), segments = transcript.getJSONArray("segments");
        if (speakers.length() > 128 || segments.length() > 2000 || (speakers.length() == 0) != (segments.length() == 0))
            throw invalidResponse();
        Set<String> speakerIds = new HashSet<>(), segmentIds = new HashSet<>();
        for (int i = 0; i < speakers.length(); i++) {
            JSONObject speaker = speakers.getJSONObject(i);
            if (!speakerIds.add(requireString(speaker, "id", 128, true))) throw invalidResponse();
            requireString(speaker, "name", 80, true);
            if (!(speaker.get("matched_reference") instanceof Boolean)) throw invalidResponse();
        }
        long chars = 0;
        for (int i = 0; i < segments.length(); i++) {
            JSONObject segment = segments.getJSONObject(i);
            if (!segmentIds.add(requireString(segment, "id", 128, true))
                    || !speakerIds.contains(requireString(segment, "speaker_id", 128, true))) throw invalidResponse();
            double start = requireNumber(segment, "start"), end = requireNumber(segment, "end");
            if (duration <= 0 || start < 0 || end < start || end > duration + 1) throw invalidResponse();
            chars += requireString(segment, "text", 20000, true).length();
            if (chars > 300000) throw invalidResponse();
        }
    }

    /** Provider schema enforcement complements meeting-level citations and ordered-id validation. */
    private static void validateSchema(Object value, JSONObject schema) throws Exception {
        JSONArray alternatives = schema.optJSONArray("anyOf");
        if (alternatives != null) {
            for (int i = 0; i < alternatives.length(); i++) {
                try { validateSchema(value, alternatives.getJSONObject(i)); return; } catch (Failure ignored) { }
            }
            throw invalidResponse();
        }
        String type = schema.getString("type");
        if (type.equals("null")) { if (value != JSONObject.NULL) throw invalidResponse(); return; }
        if (type.equals("object")) {
            if (!(value instanceof JSONObject)) throw invalidResponse();
            JSONObject object = (JSONObject) value, properties = schema.getJSONObject("properties");
            if (object.length() != properties.length()) throw invalidResponse();
            Iterator<String> keys = properties.keys();
            while (keys.hasNext()) { String key = keys.next(); if (!object.has(key)) throw invalidResponse();
                validateSchema(object.get(key), properties.getJSONObject(key)); }
        } else if (type.equals("array")) {
            if (!(value instanceof JSONArray)) throw invalidResponse();
            JSONArray values = (JSONArray) value;
            if (values.length() < schema.optInt("minItems", 0) || values.length() > schema.optInt("maxItems", Integer.MAX_VALUE))
                throw invalidResponse();
            for (int i = 0; i < values.length(); i++) validateSchema(values.get(i), schema.getJSONObject("items"));
        } else if (type.equals("string")) {
            if (!(value instanceof String)) throw invalidResponse();
            String text = (String) value;
            if (text.length() > schema.optInt("maxLength", Integer.MAX_VALUE)
                    || text.length() < schema.optInt("minLength", 0)
                    || (schema.optInt("minLength", 0) > 0 && text.trim().isEmpty())) throw invalidResponse();
            JSONArray allowed = schema.optJSONArray("enum");
            if (allowed != null) {
                boolean found = false;
                for (int i = 0; i < allowed.length(); i++) if (text.equals(allowed.getString(i))) found = true;
                if (!found) throw invalidResponse();
            }
        } else if (type.equals("number")) {
            if (!(value instanceof Number)) throw invalidResponse();
            double number = ((Number) value).doubleValue();
            if (!finite(number) || number < schema.optDouble("minimum", -Double.MAX_VALUE)
                    || number > schema.optDouble("maximum", Double.MAX_VALUE)) throw invalidResponse();
        } else throw invalidResponse();
    }

    private static void validateAudio(File audio) throws Exception {
        if (audio == null || !audio.isFile() || !audio.canRead() || audio.length() <= 0)
            throw new Failure("التسجيل غير موجود أو فارغ. سجّله مرة أخرى.");
        if (audio.length() > MAX_AUDIO) throw new Failure("التسجيل يتجاوز 13 ميغابايت؛ لم يُرسل. التسجيل الأصلي محفوظ محليًا.");
        if (!extension(audio).matches("m4a|mp3|wav|aac")) throw new Failure("اختر تسجيلًا بصيغة M4A أو MP3 أو WAV أو AAC.");
    }
    private static String extension(File file) {
        String name = file.getName().toLowerCase(Locale.ROOT); int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot + 1);
    }
    private static String mime(File file) {
        String ext = extension(file);
        return ext.equals("mp3") ? "audio/mpeg" : ext.equals("wav") ? "audio/wav" : ext.equals("aac") ? "audio/aac" : "audio/mp4";
    }
    private static byte[] readBytes(InputStream input, int limit) throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buffer = new byte[8192]; int count;
        while ((count = input.read(buffer)) != -1) {
            checkCancelled(); if (bytes.size() + count > limit) throw invalidResponse(); bytes.write(buffer, 0, count);
        }
        return bytes.toByteArray();
    }
    private static String requireString(JSONObject object, String key, int maximum, boolean nonempty) throws Exception {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String) value).length() > maximum || (nonempty && ((String) value).trim().isEmpty()))
            throw invalidResponse();
        return (String) value;
    }
    private static double requireNumber(JSONObject object, String key) throws Exception {
        if (!(object.get(key) instanceof Number)) throw invalidResponse();
        double value = object.getDouble(key); if (!finite(value)) throw invalidResponse(); return value;
    }
    private static boolean headerValue(String value, int maximum) {
        return value != null && !value.trim().isEmpty() && value.length() <= maximum && !value.matches("(?s).*[\\p{Cntrl}].*");
    }
    private static boolean finite(double value) { return !Double.isNaN(value) && !Double.isInfinite(value); }
    private static void checkCancelled() throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("Request cancelled");
    }
    private static void checkCancelledOrFailure() throws Exception {
        try { checkCancelled(); } catch (InterruptedIOException ignored) { throw cancelled(); }
    }
    private static final class Failure extends Exception { Failure(String message) { super(message); } }
    private static Failure cancelled() { return new Failure("أُلغي طلب التحليل. التسجيل والمحضر المحليان محفوظان."); }
    private static Failure timeout() { return new Failure("انتهت مهلة التحليل. التسجيل والمحضر محفوظان؛ حاول لاحقًا."); }
    private static Failure networkFailure() { return new Failure("تعذر الاتصال بخدمة التحليل. تحقق من الإنترنت وحاول لاحقًا؛ التسجيل والمحضر محفوظان."); }
    private static Failure attestationFailure() { return new Failure("تعذر التحقق من هذه النسخة لخدمة التحليل. التسجيل محفوظ؛ تحقق من الإنترنت وحدّث التطبيق ثم حاول لاحقًا."); }
    private static Failure invalidResponse() { return new Failure("ردّ التحليل ناقص أو غير صالح. التسجيل والمحضر المحليان محفوظان؛ حاول لاحقًا."); }
}
