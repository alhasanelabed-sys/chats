package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.io.InterruptedIOException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

import javax.net.ssl.SSLException;

/** Uploads only after the activity has obtained the user's explicit approval. */
public final class MeetingApi {
    private static final long MAX_AUDIO_BYTES = 24_000_000L;
    private static final long MAX_REFERENCE_BYTES = 1_000_000L;
    private static final int MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
    private static final int MAX_JSON_REQUEST_BYTES = 2_000_000;
    private static final Set<String> TRANSLATION_TARGETS = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
            "en", "fr", "de", "es", "tr", "he", "ru", "el", "uk", "zh", "fa", "ur")));

    private MeetingApi() { }

    public static String validateBaseUrl(String value) throws Exception {
        String input = value == null ? "" : value.trim();
        if (input.isEmpty()) throw new Exception("أدخل عنوان خدمة المعالجة أولًا.");
        final URI uri;
        try {
            uri = new URI(input);
        } catch (Exception e) {
            throw new Exception("عنوان خدمة المعالجة غير صالح.");
        }
        String scheme = uri.getScheme();
        String host = uri.getHost();
        if (scheme == null || host == null || host.isEmpty() || uri.isOpaque()
                || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                || uri.getRawFragment() != null || uri.getPort() == 0
                || uri.getPort() > 65535 || uri.getPort() < -1) {
            throw new Exception("استخدم عنوان خدمة صالحًا دون بيانات دخول أو استعلام أو جزء #.");
        }
        scheme = scheme.toLowerCase(Locale.ROOT);
        boolean local = host.equalsIgnoreCase("localhost") || host.equals("127.0.0.1")
                || host.equals("10.0.2.2");
        if (!scheme.equals("https") && !(scheme.equals("http") && local)) {
            throw new Exception("يجب استخدام HTTPS. يُسمح بـ HTTP فقط للخدمة المحلية أو محاكي أندرويد.");
        }
        String normalized = uri.normalize().toASCIIString();
        while (normalized.endsWith("/")) normalized = normalized.substring(0, normalized.length() - 1);
        return normalized;
    }

    public static JSONObject analyze(String baseUrl, String accessToken, File audio,
                                     String title, JSONArray profiles) throws Exception {
        String base = validateBaseUrl(baseUrl);
        validateAudio(audio, MAX_AUDIO_BYTES, "التسجيل", false);
        String meetingTitle = title == null ? "" : title.trim();
        if (meetingTitle.isEmpty() || meetingTitle.length() > 160) {
            throw new Exception("أدخل عنوان اجتماع بين حرف واحد و160 حرفًا.");
        }
        String token = validateToken(accessToken);
        JSONArray references = profiles == null ? new JSONArray() : profiles;
        if (references.length() > 4) throw new Exception("يمكن إرسال أربعة مراجع صوتية كحد أقصى.");
        File[] referenceFiles = new File[references.length()];
        JSONArray participants = new JSONArray();
        Set<String> participantIds = new HashSet<>();
        File recordingParent = audio.getAbsoluteFile().getParentFile().getCanonicalFile();
        if (!recordingParent.equals(audio.getCanonicalFile().getParentFile())) {
            throw new Exception("التسجيل لا يقع داخل مجلد التسجيلات المتوقع.");
        }
        for (int i = 0; i < references.length(); i++) {
            JSONObject profile = references.getJSONObject(i);
            String id = profile.optString("id", "");
            String name = profile.optString("name", "").trim();
            if (!id.matches("[A-Za-z0-9_-]{1,64}") || !participantIds.add(id)
                    || name.isEmpty() || name.length() > 80) {
                throw new Exception("بيانات أسماء المتحدثين غير صالحة؛ عدّلها قبل الإرسال.");
            }
            File reference = new File(profile.optString("path", ""));
            validateAudio(reference, MAX_REFERENCE_BYTES, "المرجع الصوتي لـ " + name, true);
            if (!recordingParent.equals(reference.getCanonicalFile().getParentFile())) {
                throw new Exception("يجب أن يكون المرجع الصوتي محفوظًا داخل مجلد تسجيلات التطبيق.");
            }
            referenceFiles[i] = reference;
            JSONObject participant = new JSONObject();
            participant.put("id", id);
            participant.put("name", name);
            participant.put("reference_field", "reference_" + i);
            participants.put(participant);
        }

        String boundary = "Majlis-" + UUID.randomUUID();
        HttpURLConnection connection = null;
        try {
            connection = open(base, "/v1/meetings/analyze", token, "POST", 15 * 60 * 1000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            connection.setChunkedStreamingMode(64 * 1024);
            try (OutputStream output = connection.getOutputStream()) {
                writeText(output, boundary, "title", meetingTitle);
                writeText(output, boundary, "participants", participants.toString());
                writeFile(output, boundary, "audio", audio, MAX_AUDIO_BYTES);
                for (int i = 0; i < referenceFiles.length; i++) {
                    writeFile(output, boundary, "reference_" + i, referenceFiles[i], MAX_REFERENCE_BYTES);
                }
                write(output, "--" + boundary + "--\r\n");
            }
            return analysisResponse(readResponse(connection, token));
        } catch (SocketTimeoutException e) {
            throw new Exception("انتهت مهلة الاتصال بخدمة المعالجة. التسجيل محفوظ محليًا ويمكن إعادة المحاولة.");
        } catch (UnknownHostException e) {
            throw new Exception("تعذر العثور على خدمة المعالجة. تحقق من عنوانها والاتصال بالإنترنت.");
        } catch (SSLException e) {
            throw new Exception("تعذر إنشاء اتصال HTTPS موثوق. تحقق من شهادة خدمة المعالجة.");
        } catch (InterruptedIOException e) {
            throw new Exception("أُلغي الطلب. التسجيل محفوظ محليًا.");
        } catch (IOException e) {
            throw new Exception("تعذر إرسال التسجيل أو قراءة الرد. تحقق من الاتصال والخدمة ثم أعد المحاولة.");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Checks credentials and capabilities; never sends a meeting, recording, or speaker reference. */
    public static JSONObject status(String baseUrl, String accessToken) throws Exception {
        HttpURLConnection connection = null;
        try {
            String token = validateToken(accessToken);
            connection = open(validateBaseUrl(baseUrl), "/v1/status", token, "GET", 15_000);
            JSONObject result = new JSONObject(readResponse(connection, token));
            String version = result.optString("version");
            if (!"ok".equals(result.optString("status")) || !("0.2".equals(version) || "0.3".equals(version))
                    || !positiveInteger(result.opt("max_audio_bytes"))
                    || !positiveInteger(result.opt("max_duration_seconds"))
                    || !(result.opt("reference_speakers") instanceof Number)
                    || result.getDouble("reference_speakers") != 4) {
                throw new Exception("ردّ الخدمة لا يطابق إمكانات مجلس 0.2 أو 0.3. حدّث الخادم ثم أعد المحاولة.");
            }
            JSONArray formats = result.optJSONArray("formats");
            Set<String> supported = new HashSet<>();
            if (formats != null) for (int i = 0; i < formats.length(); i++) {
                if (!(formats.get(i) instanceof String)) throw new Exception("قائمة صيغ الخدمة غير صالحة.");
                supported.add(formats.getString(i));
            }
            if (!supported.contains("m4a") || !supported.contains("mp3") || !supported.contains("wav")) {
                throw new Exception("الخدمة لا تدعم صيغ M4A وMP3 وWAV المطلوبة. حدّث الخادم.");
            }
            return result;
        } catch (SocketTimeoutException e) {
            throw new Exception("انتهت مهلة فحص الخدمة. تحقق من العنوان والاتصال ثم أعد المحاولة.");
        } catch (UnknownHostException e) {
            throw new Exception("تعذر العثور على خدمة المعالجة. تحقق من عنوانها والاتصال بالإنترنت.");
        } catch (SSLException e) {
            throw new Exception("تعذر إنشاء اتصال HTTPS موثوق. تحقق من شهادة خدمة المعالجة.");
        } catch (InterruptedIOException e) {
            throw new Exception("أُلغي فحص الخدمة.");
        } catch (JSONException e) {
            throw new Exception("ردّ فحص الخدمة ليس JSON صالحًا أو يتضمن حقولًا ناقصة. تحقق من إعداد الخادم.");
        } catch (IOException e) {
            throw new Exception("تعذر الاتصال بخدمة المعالجة. تحقق من العنوان والاتصال ثم أعد المحاولة.");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Rebuilds minutes from an explicitly approved transcript; local metadata is never serialized. */
    public static JSONObject summarize(String baseUrl, String accessToken, JSONObject meeting) throws Exception {
        JSONObject payload = transcriptPayload(meeting);
        byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_JSON_REQUEST_BYTES) throw new Exception("النص يتجاوز الحد المسموح لإعادة التلخيص.");
        HttpURLConnection connection = null;
        try {
            String token = validateToken(accessToken);
            connection = open(validateBaseUrl(baseUrl), "/v1/meetings/summarize", token,
                    "POST", 15 * 60 * 1000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                writeBytes(output, bytes);
            }
            JSONObject result = analysisResponse(readResponse(connection, token));
            // Summary generation must not rewrite the user's corrected transcript or speaker labels.
            if (!transcriptPayload(result).toString().equals(payload.toString())) {
                throw new Exception("غيّرت الخدمة النص أو المتحدثين أثناء التلخيص. بقي المحضر السابق محفوظًا.");
            }
            return result;
        } catch (SocketTimeoutException e) {
            throw new Exception("انتهت مهلة إعادة التلخيص. بقي النص المعدّل محفوظًا ويمكن إعادة المحاولة.");
        } catch (UnknownHostException e) {
            throw new Exception("تعذر العثور على خدمة المعالجة. تحقق من عنوانها والاتصال بالإنترنت.");
        } catch (SSLException e) {
            throw new Exception("تعذر إنشاء اتصال HTTPS موثوق. تحقق من شهادة خدمة المعالجة.");
        } catch (InterruptedIOException e) {
            throw new Exception("أُلغي الطلب. بقي النص المعدّل محفوظًا.");
        } catch (IOException e) {
            throw new Exception("تعذر إرسال النص أو قراءة الرد. بقي النص المعدّل محفوظًا؛ أعد المحاولة.");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Translates an explicitly approved text report without sending audio or local metadata. */
    public static JSONObject translate(String baseUrl, String accessToken, JSONObject meeting,
                                       String targetLanguage) throws Exception {
        String target = targetLanguage == null ? "" : targetLanguage.trim();
        if (!TRANSLATION_TARGETS.contains(target)) {
            throw new Exception("اختر لغة ترجمة مدعومة: الإنجليزية، الفرنسية، الألمانية، الإسبانية، التركية، "
                    + "العبرية، الروسية، اليونانية، الأوكرانية، الصينية، الفارسية أو الأردية.");
        }
        if (meeting == null) throw new Exception("محضر الاجتماع غير موجود.");
        JSONObject cleanMeeting;
        try {
            cleanMeeting = analysisFields(meeting);
        } catch (Exception e) {
            throw new Exception("محضر الاجتماع غير صالح للترجمة. راجع النص والمتحدثين والمراجع أولًا.");
        }
        JSONArray sourceSegments = cleanMeeting.getJSONArray("segments");
        if (sourceSegments.length() > 2000) throw new Exception("عدد مقاطع النص يتجاوز حد الترجمة: 2000 مقطع.");
        JSONObject payload = new JSONObject().put("meeting", cleanMeeting).put("target_language", target);
        byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_JSON_REQUEST_BYTES) throw new Exception("المحضر يتجاوز الحد المسموح للترجمة.");
        HttpURLConnection connection = null;
        try {
            String token = validateToken(accessToken);
            connection = open(validateBaseUrl(baseUrl), "/v1/meetings/translate", token,
                    "POST", 15 * 60 * 1000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                writeBytes(output, bytes);
            }
            return translationResponse(readResponse(connection, token), target, sourceSegments);
        } catch (SocketTimeoutException e) {
            throw new Exception("انتهت مهلة الترجمة. بقي المحضر الأصلي محفوظًا ويمكن إعادة المحاولة.");
        } catch (UnknownHostException e) {
            throw new Exception("تعذر العثور على خدمة المعالجة. تحقق من عنوانها والاتصال بالإنترنت.");
        } catch (SSLException e) {
            throw new Exception("تعذر إنشاء اتصال HTTPS موثوق. تحقق من شهادة خدمة المعالجة.");
        } catch (InterruptedIOException e) {
            throw new Exception("أُلغي طلب الترجمة. بقي المحضر الأصلي محفوظًا.");
        } catch (IOException e) {
            throw new Exception("تعذر إرسال المحضر أو قراءة الترجمة. بقي المحضر الأصلي محفوظًا؛ أعد المحاولة.");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static JSONObject translationResponse(String body, String target, JSONArray sourceSegments) throws Exception {
        try {
            JSONObject result = new JSONObject(body);
            if (!target.equals(nonempty(result, "target_language"))) throw new Exception();
            String report = nonempty(result, "translated_report");
            if (report.length() > 100_000) throw new Exception();
            JSONArray segments = result.getJSONArray("segments");
            if (segments.length() != sourceSegments.length()) throw new Exception();
            JSONArray cleanSegments = new JSONArray();
            for (int i = 0; i < segments.length(); i++) {
                JSONObject segment = segments.getJSONObject(i);
                String id = nonempty(segment, "id");
                String text = nonempty(segment, "text");
                if (!id.equals(sourceSegments.getJSONObject(i).getString("id")) || text.length() > 20_000) {
                    throw new Exception();
                }
                cleanSegments.put(new JSONObject().put("id", id).put("text", text));
            }
            return new JSONObject().put("target_language", target).put("translated_report", report)
                    .put("segments", cleanSegments);
        } catch (Exception e) {
            throw new Exception("ردّ الترجمة ناقص أو غير صالح أو لا يطابق لغة ومقاطع الاجتماع. بقي المحضر الأصلي محفوظًا.");
        }
    }

    static JSONObject transcriptPayload(JSONObject meeting) throws Exception {
        if (meeting == null) throw new Exception("محضر الاجتماع غير موجود.");
        validateResult(meeting);
        JSONObject payload = copyFields(meeting, "title", "language", "duration_seconds");
        JSONArray speakers = new JSONArray(), segments = new JSONArray();
        JSONArray originalSpeakers = meeting.getJSONArray("speakers");
        for (int i = 0; i < originalSpeakers.length(); i++) {
            speakers.put(copyFields(originalSpeakers.getJSONObject(i), "id", "name", "matched_reference"));
        }
        JSONArray originalSegments = meeting.getJSONArray("segments");
        for (int i = 0; i < originalSegments.length(); i++) {
            segments.put(copyFields(originalSegments.getJSONObject(i), "id", "speaker_id", "start", "end", "text"));
        }
        payload.put("speakers", speakers);
        payload.put("segments", segments);
        return payload;
    }

    private static JSONObject copyFields(JSONObject source, String... keys) throws Exception {
        JSONObject result = new JSONObject();
        for (String key : keys) result.put(key, source.get(key));
        return result;
    }

    private static JSONObject analysisResponse(String body) throws Exception {
        try {
            return analysisFields(new JSONObject(body));
        } catch (Exception e) {
            throw new Exception("ردّ خدمة المعالجة ناقص أو غير صالح. لم يُحفظ محضر فارغ؛ تحقق من إعداد الخدمة.");
        }
    }

    private static JSONObject analysisFields(JSONObject result) throws Exception {
        JSONObject clean = transcriptPayload(result);
        JSONObject minutes = result.getJSONObject("minutes");
        JSONObject cleanMinutes = copyFields(minutes, "summary", "discussion_points", "open_questions");
        JSONArray decisions = new JSONArray(), actions = new JSONArray(), annotations = new JSONArray();
        JSONArray originalDecisions = minutes.getJSONArray("decisions");
        for (int i = 0; i < originalDecisions.length(); i++) {
            decisions.put(copyFields(originalDecisions.getJSONObject(i), "text", "segment_ids"));
        }
        JSONArray originalActions = minutes.getJSONArray("action_items");
        for (int i = 0; i < originalActions.length(); i++) {
            actions.put(copyFields(originalActions.getJSONObject(i), "task", "owner", "due_date", "segment_ids"));
        }
        JSONArray originalAnnotations = minutes.optJSONArray("speech_annotations");
        if (originalAnnotations != null) for (int i = 0; i < originalAnnotations.length(); i++) {
            annotations.put(copyFields(originalAnnotations.getJSONObject(i), "segment_id", "language", "status", "reason"));
        }
        cleanMinutes.put("decisions", decisions);
        cleanMinutes.put("action_items", actions);
        cleanMinutes.put("speech_annotations", annotations);
        clean.put("minutes", cleanMinutes);
        return clean;
    }

    private static HttpURLConnection open(String base, String endpoint, String token,
                                           String method, int readTimeout) throws Exception {
        checkCancelled();
        HttpURLConnection connection = (HttpURLConnection) new URI(base + endpoint).toURL().openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setRequestMethod(method);
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(readTimeout);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");
        if (!token.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + token);
        return connection;
    }

    private static String validateToken(String accessToken) throws Exception {
        String supplied = accessToken == null ? "" : accessToken;
        if (supplied.matches("(?s).*[\\p{Cntrl}].*")) throw new Exception("رمز الوصول غير صالح.");
        return supplied.trim();
    }

    private static boolean positiveInteger(Object value) {
        if (!(value instanceof Number)) return false;
        double number = ((Number) value).doubleValue();
        return !Double.isNaN(number) && !Double.isInfinite(number) && number > 0 && number == Math.floor(number);
    }

    private static String readResponse(HttpURLConnection connection, String token) throws Exception {
        checkCancelled();
        int status = connection.getResponseCode();
        if (status >= 300 && status < 400) {
            throw new Exception("الخدمة أعادت توجيه الطلب. أدخل عنوان الخدمة النهائي مباشرةً لحماية رمز الوصول.");
        }
        String body;
        InputStream stream = status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream();
        try (InputStream input = stream) {
            body = input == null ? "" : read(input, status >= 200 && status < 300 ? MAX_RESPONSE_BYTES : 16 * 1024);
        }
        if (status < 200 || status >= 300) {
            throw new Exception(responseError(status, body, token));
        }
        return body;
    }

    private static void validateAudio(File file, long limit, String label, boolean reference) throws Exception {
        if (file == null || !file.isFile() || !file.canRead() || file.length() == 0) {
            throw new Exception(label + " غير موجود أو فارغ. سجّله مرة أخرى.");
        }
        String extension = extension(file);
        if (!(extension.equals("m4a") || (!reference && (extension.equals("mp3") || extension.equals("wav"))))) {
            throw new Exception(label + (reference ? " يجب أن يكون بصيغة M4A." : " يجب أن يكون بصيغة M4A أو MP3 أو WAV."));
        }
        if (file.length() > limit) {
            throw new Exception(label + " يتجاوز الحد المسموح ("
                    + String.format(Locale.ROOT, "%.1f", limit / 1_000_000.0) + " ميغابايت).");
        }
    }

    private static void writeText(OutputStream output, String boundary, String name, String value)
            throws IOException {
        write(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name
                + "\"\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n" + value + "\r\n");
    }

    private static void writeFile(OutputStream output, String boundary, String name,
                                  File file, long limit) throws IOException {
        String extension = extension(file);
        String mime = extension.equals("mp3") ? "audio/mpeg" : extension.equals("wav") ? "audio/wav" : "audio/mp4";
        write(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name
                + "\"; filename=\"" + name + "." + extension + "\"\r\nContent-Type: " + mime + "\r\n\r\n");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[16 * 1024];
            long total = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                checkCancelled();
                total += count;
                if (total > limit) throw new IOException("Audio size changed during upload");
                output.write(buffer, 0, count);
            }
        }
        write(output, "\r\n");
    }

    private static void write(OutputStream output, String text) throws IOException {
        checkCancelled();
        output.write(text.getBytes(StandardCharsets.UTF_8));
    }

    private static String extension(File file) {
        String name = file.getName().toLowerCase(Locale.ROOT);
        int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot + 1);
    }

    private static void writeBytes(OutputStream output, byte[] bytes) throws IOException {
        for (int start = 0; start < bytes.length; start += 16 * 1024) {
            checkCancelled();
            output.write(bytes, start, Math.min(16 * 1024, bytes.length - start));
        }
    }

    private static void checkCancelled() throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("Request cancelled");
    }

    private static String read(InputStream input, int limit) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = input.read(buffer)) != -1) {
            checkCancelled();
            if (bytes.size() + count > limit) throw new IOException("Response exceeded limit");
            bytes.write(buffer, 0, count);
        }
        return new String(bytes.toByteArray(), StandardCharsets.UTF_8);
    }

    private static String responseError(int status, String body, String token) {
        String detail = "";
        try {
            JSONObject error = new JSONObject(body);
            Object value = error.opt("detail");
            if (value instanceof JSONObject) {
                JSONObject object = (JSONObject) value;
                detail = object.optString("message", object.optString("error", object.toString()));
            } else if (value != null && value != JSONObject.NULL) {
                detail = value.toString();
            }
        } catch (Exception ignored) { }
        if (!token.isEmpty()) detail = detail.replace(token, "[رمز محجوب]");
        detail = detail.replaceAll("[\\p{Cntrl}]", " ").trim();
        if (detail.length() > 500) detail = detail.substring(0, 500) + "…";
        if (detail.isEmpty()) {
            if (status == 401 || status == 403) detail = "تحقق من رمز الوصول للخدمة.";
            else if (status == 413) detail = "حجم التسجيل أكبر من الحد الذي تقبله الخدمة.";
            else if (status == 429) detail = "الخدمة مشغولة؛ أعد المحاولة لاحقًا.";
            else if (status >= 500) detail = "تعذر إكمال المعالجة على الخادم؛ أعد المحاولة لاحقًا.";
            else detail = "تحقق من إعداد الخدمة وصيغة الطلب.";
        }
        return "رفضت خدمة المعالجة الطلب (HTTP " + status + "): " + detail;
    }

    static void validateResult(JSONObject result) throws Exception {
        nonempty(result, "title");
        nonempty(result, "language");
        double duration = result.getDouble("duration_seconds");
        if (Double.isNaN(duration) || Double.isInfinite(duration) || duration < 0) throw new Exception();
        JSONArray speakers = result.getJSONArray("speakers");
        JSONArray segments = result.getJSONArray("segments");
        boolean silence = speakers.length() == 0 && segments.length() == 0;
        if (!silence && (speakers.length() == 0 || segments.length() == 0)) throw new Exception();
        Set<String> speakerIds = new HashSet<>();
        Set<String> segmentIds = new HashSet<>();
        for (int i = 0; i < speakers.length(); i++) {
            JSONObject speaker = speakers.getJSONObject(i);
            if (!speakerIds.add(nonempty(speaker, "id"))) throw new Exception();
            nonempty(speaker, "name");
            if (!(speaker.get("matched_reference") instanceof Boolean)) throw new Exception();
        }
        for (int i = 0; i < segments.length(); i++) {
            JSONObject segment = segments.getJSONObject(i);
            if (!segmentIds.add(nonempty(segment, "id"))) throw new Exception();
            if (!speakerIds.contains(nonempty(segment, "speaker_id"))) throw new Exception();
            nonempty(segment, "text");
            double start = segment.getDouble("start"), end = segment.getDouble("end");
            if (Double.isNaN(start) || Double.isNaN(end) || Double.isInfinite(start)
                    || Double.isInfinite(end) || start < 0 || end < start) throw new Exception();
        }
        JSONObject minutes = result.getJSONObject("minutes");
        nonempty(minutes, "summary");
        validateStrings(minutes.getJSONArray("discussion_points"));
        validateStrings(minutes.getJSONArray("open_questions"));
        validateSpeechAnnotations(minutes, segmentIds);
        JSONArray decisions = minutes.getJSONArray("decisions");
        if (silence && decisions.length() != 0) throw new Exception();
        for (int i = 0; i < decisions.length(); i++) {
            JSONObject decision = decisions.getJSONObject(i);
            nonempty(decision, "text");
            validateCitations(decision.getJSONArray("segment_ids"), segmentIds);
        }
        JSONArray actions = minutes.getJSONArray("action_items");
        if (silence && actions.length() != 0) throw new Exception();
        for (int i = 0; i < actions.length(); i++) {
            JSONObject action = actions.getJSONObject(i);
            nonempty(action, "task");
            validateNullableString(action, "owner");
            validateNullableString(action, "due_date");
            validateCitations(action.getJSONArray("segment_ids"), segmentIds);
        }
    }

    /** Optional provider observations; missing or empty arrays keep legacy meetings compatible. */
    private static void validateSpeechAnnotations(JSONObject minutes, Set<String> segmentIds) throws Exception {
        if (!minutes.has("speech_annotations")) return;
        JSONArray annotations = minutes.getJSONArray("speech_annotations");
        if (annotations.length() == 0) return;
        if (annotations.length() > 2000 || annotations.length() != segmentIds.size()) throw new Exception();
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < annotations.length(); i++) {
            JSONObject annotation = annotations.getJSONObject(i);
            String id = nonempty(annotation, "segment_id");
            String language = nonempty(annotation, "language");
            String status = nonempty(annotation, "status");
            String reason = nonempty(annotation, "reason");
            if (!segmentIds.contains(id) || !seen.add(id)
                    || !(TRANSLATION_TARGETS.contains(language) || language.equals("ar") || language.equals("mul")
                    || language.equals("other") || language.equals("unknown"))
                    || !(status.equals("clear") || status.equals("unclear") || status.equals("uninterpretable"))
                    || reason.length() > 300) throw new Exception();
        }
    }

    private static String nonempty(JSONObject object, String key) throws Exception {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String) value).trim().isEmpty()) throw new Exception();
        return (String) value;
    }

    private static void validateStrings(JSONArray values) throws Exception {
        for (int i = 0; i < values.length(); i++) {
            if (!(values.get(i) instanceof String) || values.getString(i).trim().isEmpty()) throw new Exception();
        }
    }

    private static void validateCitations(JSONArray ids, Set<String> allowed) throws Exception {
        if (ids.length() == 0) throw new Exception();
        for (int i = 0; i < ids.length(); i++) {
            if (!(ids.get(i) instanceof String) || !allowed.contains(ids.getString(i))) throw new Exception();
        }
    }

    private static void validateNullableString(JSONObject object, String key) throws Exception {
        Object value = object.get(key);
        if (value != JSONObject.NULL && !(value instanceof String)) throw new Exception();
    }
}
