package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
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
        validateAudio(audio, MAX_AUDIO_BYTES, "التسجيل");
        String meetingTitle = title == null ? "" : title.trim();
        if (meetingTitle.isEmpty() || meetingTitle.length() > 160) {
            throw new Exception("أدخل عنوان اجتماع بين حرف واحد و160 حرفًا.");
        }
        String token = accessToken == null ? "" : accessToken.trim();
        if (token.indexOf('\r') >= 0 || token.indexOf('\n') >= 0) {
            throw new Exception("رمز الوصول غير صالح.");
        }
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
            validateAudio(reference, MAX_REFERENCE_BYTES, "المرجع الصوتي لـ " + name);
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
            connection = (HttpURLConnection) new URI(base + "/v1/meetings/analyze").toURL().openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(15 * 60 * 1000);
            connection.setDoOutput(true);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            if (!token.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + token);
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
            int status = connection.getResponseCode();
            if (status >= 300 && status < 400) {
                throw new Exception("الخدمة أعادت توجيه الطلب. أدخل عنوان الخدمة النهائي مباشرةً لحماية رمز الوصول.");
            }
            String body;
            InputStream stream = status >= 200 && status < 300
                    ? connection.getInputStream() : connection.getErrorStream();
            try (InputStream input = stream) {
                body = input == null ? "" : read(input,
                        status >= 200 && status < 300 ? MAX_RESPONSE_BYTES : 16 * 1024);
            }
            if (status < 200 || status >= 300) {
                throw new Exception(responseError(status, body));
            }
            final JSONObject result;
            try {
                result = new JSONObject(body);
                validateResult(result);
                // Local record identities and paths are exclusively assigned by the app.
                result.remove("_id");
                result.remove("_saved_at");
                result.remove("_audio_path");
                result.remove("_demo");
            } catch (Exception e) {
                throw new Exception("ردّ خدمة المعالجة ناقص أو غير صالح. لم يُحفظ محضر فارغ؛ تحقق من إعداد الخدمة.");
            }
            return result;
        } catch (SocketTimeoutException e) {
            throw new Exception("انتهت مهلة الاتصال بخدمة المعالجة. التسجيل محفوظ محليًا ويمكن إعادة المحاولة.");
        } catch (UnknownHostException e) {
            throw new Exception("تعذر العثور على خدمة المعالجة. تحقق من عنوانها والاتصال بالإنترنت.");
        } catch (SSLException e) {
            throw new Exception("تعذر إنشاء اتصال HTTPS موثوق. تحقق من شهادة خدمة المعالجة.");
        } catch (IOException e) {
            throw new Exception("تعذر إرسال التسجيل أو قراءة الرد. تحقق من الاتصال والخدمة ثم أعد المحاولة.");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static void validateAudio(File file, long limit, String label) throws Exception {
        if (file == null || !file.isFile() || !file.canRead() || file.length() == 0) {
            throw new Exception(label + " غير موجود أو فارغ. سجّله مرة أخرى.");
        }
        if (!file.getName().toLowerCase(Locale.ROOT).endsWith(".m4a")) {
            throw new Exception(label + " يجب أن يكون بصيغة M4A.");
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
        write(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name
                + "\"; filename=\"" + name + ".m4a\"\r\nContent-Type: audio/mp4\r\n\r\n");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[16 * 1024];
            long total = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                total += count;
                if (total > limit) throw new IOException("Audio size changed during upload");
                output.write(buffer, 0, count);
            }
        }
        write(output, "\r\n");
    }

    private static void write(OutputStream output, String text) throws IOException {
        output.write(text.getBytes(StandardCharsets.UTF_8));
    }

    private static String read(InputStream input, int limit) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (bytes.size() + count > limit) throw new IOException("Response exceeded limit");
            bytes.write(buffer, 0, count);
        }
        return new String(bytes.toByteArray(), StandardCharsets.UTF_8);
    }

    private static String responseError(int status, String body) {
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
