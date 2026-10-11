package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Direct official-provider requests. Call only after obtaining the appropriate upload consent. */
public final class DirectAi {
    public static final String DEFAULT_OPENAI_MODEL = "gpt-4.1-mini";
    public static final String DEFAULT_GEMINI_MODEL = "gemini-3.8-flash";
    private static final String OPENAI = "https://api.openai.com/v1/";
    private static final String GEMINI = "https://generativelanguage.googleapis.com/";
    private static final long MAX_AUDIO = 24_000_000L, MAX_REFERENCE = 1_000_000L;
    private static final int MAX_RESPONSE = 4 * 1024 * 1024, MAX_JSON = 2_000_000;
    private static final int READ_TIMEOUT = 15 * 60 * 1000;

    /** Package-local seam substitutes connections, never changes production endpoint validation. */
    interface ConnectionFactory { HttpURLConnection open(URI officialUri) throws Exception; }
    private static final ThreadLocal<ConnectionFactory> TEST_CONNECTIONS = new ThreadLocal<>();
    static void setConnectionsForTests(ConnectionFactory factory) {
        if (factory == null) TEST_CONNECTIONS.remove(); else TEST_CONNECTIONS.set(factory);
    }
    private DirectAi() { }
    private static final class Failure extends Exception {
        Failure(String message) { super(message); }
    }
    private static final class Configuration {
        final String provider, key, model;
        Configuration(String provider, String key, String model) throws Exception {
            if (!("openai".equals(provider) || "gemini".equals(provider)))
                throw new Failure("اختر OpenAI أو Gemini من إعدادات التحليل.");
            this.provider = provider;
            String supplied = key == null ? "" : key;
            if (supplied.matches("(?s).*[\\p{Cntrl}].*") || supplied.trim().isEmpty()
                    || supplied.length() > 512)
                throw new Failure("أدخل مفتاح API صالحًا للمزوّد المختار في الإعدادات.");
            this.key = supplied.trim();
            String chosen = model == null ? "" : model.trim();
            this.model = chosen.isEmpty() ? (provider.equals("openai") ? DEFAULT_OPENAI_MODEL : DEFAULT_GEMINI_MODEL) : chosen;
            if (!this.model.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,119}"))
                throw new Failure("اسم نموذج الذكاء الاصطناعي غير صالح.");
        }
    }

    public static JSONObject analyze(String provider, String key, String model, File audio,
                                     String title, JSONArray profiles, double duration) throws Exception {
        try {
            Configuration config = new Configuration(provider, key, model);
            validateAudio(audio, MAX_AUDIO, false);
            if (!finite(duration) || duration <= 0 || duration > 3600)
                throw new Failure("يجب أن يكون التسجيل قابلًا للقراءة وألا تزيد مدته على ساعة.");
            String heading = title == null ? "" : title.trim();
            if (heading.isEmpty() || heading.length() > 160)
                throw new Failure("أدخل عنوان اجتماع بين حرف واحد و160 حرفًا.");
            JSONObject transcript = config.provider.equals("openai")
                    ? transcribeOpenAi(config, audio, heading, profiles, duration)
                    : transcribeGemini(config, audio, heading, duration);
            boolean cleanupPending = transcript.optBoolean("_provider_cleanup_pending", false);
            JSONObject result;
            try { result = summarizeTranscript(config, transcript); }
            catch (Exception e) {
                if (cleanupPending) throw new Failure("تعذر إكمال المحضر وتعذر تأكيد حذف ملف Gemini المؤقت. "
                        + "التسجيل المحلي محفوظ؛ الملفات المرفوعة تنتهي تلقائيًا لدى المزوّد بعد 48 ساعة.");
                throw e;
            }
            if (cleanupPending) result.put("_provider_cleanup_pending", true);
            return result;
        } catch (Failure e) { throw e; }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (Exception e) { throw genericFailure(); }
    }

    public static JSONObject summarize(String provider, String key, String model, JSONObject meeting) throws Exception {
        try {
            Configuration config = new Configuration(provider, key, model);
            JSONObject transcript = MeetingApi.transcriptPayload(meeting);
            validateTranscript(transcript);
            return summarizeTranscript(config, transcript);
        } catch (Failure e) { throw e; }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (Exception e) { throw genericFailure(); }
    }

    public static JSONObject translate(String provider, String key, String model,
                                       JSONObject meeting, String target) throws Exception {
        try {
            Configuration config = new Configuration(provider, key, model);
            if (target == null || !target.matches("en|fr|de|es|tr|he|ru|el|uk|zh|fa|ur"))
                throw new Failure("اختر لغة ترجمة مدعومة من قائمة اللغات.");
            JSONObject clean = MeetingApi.analysisFields(meeting);
            validateTranscript(clean);
            validateSchema(clean.getJSONObject("minutes"), ProviderPrompts.minutesSchema());
            JSONObject payload = new JSONObject().put("meeting", clean).put("target_language", target);
            JSONObject translated = structured(config, ProviderPrompts.TRANSLATION, payload,
                    "meeting_translation", ProviderPrompts.translationSchema(), null);
            return MeetingApi.translationResponse(translated.toString(), target, clean.getJSONArray("segments"));
        } catch (Failure e) { throw e; }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (Exception e) { throw genericFailure(); }
    }

    /** Model metadata only: no recording, prompt, named voices, or billable generation. */
    public static JSONObject status(String provider, String key, String model) throws Exception {
        try {
            Configuration config = new Configuration(provider, key, model);
            String endpoint = config.provider.equals("openai") ? OPENAI + "models/" + config.model
                    : GEMINI + "v1beta/models/" + config.model;
            JSONObject metadata = new JSONObject(request(config, endpoint, "GET", null, 15_000));
            if (config.provider.equals("openai")) {
                if (!config.model.equals(metadata.optString("id"))) throw genericFailure();
                JSONObject transcription = new JSONObject(request(config,
                        OPENAI + "models/gpt-4o-transcribe-diarize", "GET", null, 15_000));
                if (!"gpt-4o-transcribe-diarize".equals(transcription.optString("id"))) throw genericFailure();
            } else {
                if (!("models/" + config.model).equals(metadata.optString("name"))) throw genericFailure();
                JSONArray methods = metadata.optJSONArray("supportedGenerationMethods");
                boolean supported = false;
                if (methods != null) for (int i = 0; i < methods.length(); i++)
                    if ("generateContent".equals(methods.getString(i))) supported = true;
                if (!supported) throw new Failure("النموذج المختار لا يعلن دعم التحليل عبر generateContent. اختر نموذجًا آخر.");
            }
            return new JSONObject().put("status", "ok").put("provider", provider).put("model", config.model)
                    .put("max_audio_bytes", MAX_AUDIO).put("max_duration_seconds", 3600)
                    .put("reference_speakers", provider.equals("openai") ? 4 : 0)
                    .put("formats", new JSONArray().put("m4a").put("mp3").put("wav"));
        } catch (Failure e) { throw e; }
        catch (InterruptedIOException e) { throw cancelled(); }
        catch (Exception e) { throw genericFailure(); }
    }

    private static JSONObject transcribeOpenAi(Configuration config, File audio, String title,
                                               JSONArray profiles, double duration) throws Exception {
        JSONArray references = profiles == null ? new JSONArray() : profiles;
        if (references.length() > 4) throw new Failure("يمكن إرسال أربعة مراجع صوتية كحد أقصى إلى OpenAI.");
        Map<String, String> names = new HashMap<>();
        File[] files = new File[references.length()];
        Set<String> ids = new HashSet<>();
        File parent = audio.getAbsoluteFile().getParentFile().getCanonicalFile();
        if (!parent.equals(audio.getCanonicalFile().getParentFile()))
            throw new Failure("التسجيل لا يقع داخل مجلد التسجيلات المتوقع.");
        for (int i = 0; i < references.length(); i++) {
            JSONObject profile = references.getJSONObject(i);
            String id = profile.optString("id", ""), name = profile.optString("name", "").trim();
            if (!id.matches("[A-Za-z0-9_-]{1,64}") || !ids.add(id) || name.isEmpty() || name.length() > 80)
                throw new Failure("بيانات أسماء المتحدثين غير صالحة؛ عدّلها قبل الإرسال.");
            files[i] = new File(profile.optString("path", ""));
            validateAudio(files[i], MAX_REFERENCE, true);
            if (!parent.equals(files[i].getCanonicalFile().getParentFile()))
                throw new Failure("يجب حفظ المراجع الصوتية داخل مجلد تسجيلات التطبيق.");
            names.put("known_" + i, name);
        }
        HttpURLConnection connection = null;
        try {
            connection = open(config, OPENAI + "audio/transcriptions", "POST", READ_TIMEOUT, true);
            String boundary = "Majlis-" + UUID.randomUUID();
            connection.setDoOutput(true);
            connection.setChunkedStreamingMode(64 * 1024);
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            try (OutputStream output = connection.getOutputStream()) {
                field(output, boundary, "model", "gpt-4o-transcribe-diarize");
                field(output, boundary, "response_format", "diarized_json");
                field(output, boundary, "chunking_strategy", "auto");
                for (int i = 0; i < files.length; i++) {
                    field(output, boundary, "known_speaker_names[]", "known_" + i);
                    byte[] sample;
                    try (InputStream input = new FileInputStream(files[i])) {
                        sample = readBytes(input, (int) MAX_REFERENCE);
                    }
                    field(output, boundary, "known_speaker_references[]", "data:audio/mp4;base64,"
                            + Base64.getEncoder().encodeToString(sample));
                }
                write(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"meeting."
                        + extension(audio) + "\"\r\nContent-Type: " + mime(audio) + "\r\n\r\n");
                streamFile(output, audio, audio.length());
                write(output, "\r\n--" + boundary + "--\r\n");
            }
            JSONObject raw = new JSONObject(readResponse(connection));
            if (!"transcribe".equals(raw.optString("task")) || !(raw.opt("duration") instanceof Number)
                    || !finite(raw.getDouble("duration")) || raw.getDouble("duration") <= 0
                    || raw.getDouble("duration") > duration + 1) throw genericFailure();
            return normalizeTranscript(raw.getJSONArray("segments"), title, duration, names, true);
        } finally { if (connection != null) connection.disconnect(); }
    }

    private static JSONObject transcribeGemini(Configuration config, File audio, String title,
                                               double duration) throws Exception {
        String uploadedName = null;
        boolean cleanupPending = false;
        JSONObject result = null;
        Exception failure = null;
        try {
            HttpURLConnection start = null;
            String uploadUrl;
            try {
                start = open(config, GEMINI + "upload/v1beta/files", "POST", 60_000, true);
                start.setRequestProperty("X-Goog-Upload-Protocol", "resumable");
                start.setRequestProperty("X-Goog-Upload-Command", "start");
                start.setRequestProperty("X-Goog-Upload-Header-Content-Length", Long.toString(audio.length()));
                start.setRequestProperty("X-Goog-Upload-Header-Content-Type", geminiMime(audio));
                sendJson(start, new JSONObject().put("file", new JSONObject().put("display_name", "Majlis recording")));
                readResponse(start);
                uploadUrl = start.getHeaderField("X-Goog-Upload-URL");
            } finally { if (start != null) start.disconnect(); }
            validateOfficialUrl(uploadUrl, "gemini");
            if (uploadUrl.length() > 8192 || !new URI(uploadUrl).getPath().startsWith("/upload/"))
                throw new Failure("أعاد Gemini عنوان رفع غير متوقع؛ لم يُرسل التسجيل إلى هذا العنوان.");
            JSONObject file;
            HttpURLConnection upload = null;
            try {
                // The resumable URL itself authorizes the byte upload; never append or forward the API key.
                upload = open(config, uploadUrl, "POST", READ_TIMEOUT, false);
                upload.setDoOutput(true);
                upload.setFixedLengthStreamingMode(audio.length());
                upload.setRequestProperty("Content-Type", geminiMime(audio));
                upload.setRequestProperty("X-Goog-Upload-Offset", "0");
                upload.setRequestProperty("X-Goog-Upload-Command", "upload, finalize");
                try (OutputStream output = upload.getOutputStream()) { streamFile(output, audio, audio.length()); }
                file = new JSONObject(readResponse(upload)).getJSONObject("file");
            } finally { if (upload != null) upload.disconnect(); }
            uploadedName = validFileName(file.getString("name"));
            for (int attempt = 0; ; attempt++) {
                if (!uploadedName.equals(file.optString("name"))) throw genericFailure();
                String state = file.optString("state");
                if (state.equals("ACTIVE")) break;
                if (!state.equals("PROCESSING") || attempt >= 60)
                    throw new Failure("لم يجهز ملف الصوت في Gemini خلال المهلة. التسجيل المحلي محفوظ؛ أعد المحاولة.");
                try { Thread.sleep(2000); } catch (InterruptedException e) {
                    Thread.currentThread().interrupt(); throw cancelled();
                }
                file = new JSONObject(request(config, GEMINI + "v1beta/" + uploadedName, "GET", null, 30_000));
            }
            String fileUri = file.getString("uri");
            URI uri = validateOfficialUrl(fileUri, "gemini");
            if (!uri.getPath().equals("/v1beta/" + uploadedName) || uri.getRawQuery() != null) throw genericFailure();
            JSONObject payload = new JSONObject().put("actual_duration_seconds", duration);
            JSONObject attachment = new JSONObject().put("fileData", new JSONObject()
                    .put("fileUri", fileUri).put("mimeType", geminiMime(audio)));
            JSONObject raw = structured(config, ProviderPrompts.TRANSCRIBE, payload, "meeting_transcript",
                    ProviderPrompts.transcriptSchema(), attachment);
            result = normalizeTranscript(raw.getJSONArray("segments"), title, duration, new HashMap<>(), false);
        } catch (Exception e) { failure = e; }
        finally {
            if (uploadedName != null) {
                boolean interrupted = Thread.interrupted();
                try { request(config, GEMINI + "v1beta/" + uploadedName, "DELETE", null, 15_000); }
                catch (Exception ignored) { cleanupPending = true; }
                finally { if (interrupted) Thread.currentThread().interrupt(); }
            }
        }
        if (failure != null) {
            if (cleanupPending) throw new Failure("تعذر إكمال التحليل وتعذر تأكيد حذف ملف Gemini المؤقت. "
                    + "التسجيل المحلي محفوظ؛ الملفات المرفوعة تنتهي تلقائيًا لدى المزوّد بعد 48 ساعة.");
            throw failure;
        }
        if (cleanupPending) result.put("_provider_cleanup_pending", true);
        return result;
    }

    private static String validFileName(String value) throws Exception {
        if (value == null || !value.matches("files/[A-Za-z0-9_-]{1,128}")) throw genericFailure();
        return value;
    }

    private static JSONObject normalizeTranscript(JSONArray source, String title, double duration,
                                                  Map<String, String> knownNames, boolean openAi) throws Exception {
        if (source.length() > 2000) throw genericFailure();
        JSONArray segments = new JSONArray(), speakers = new JSONArray();
        Map<String, String> speakerIds = new LinkedHashMap<>();
        Set<String> ids = new HashSet<>();
        long chars = 0;
        for (int i = 0; i < source.length(); i++) {
            JSONObject raw = source.getJSONObject(i);
            if (openAi && !"transcript.text.segment".equals(raw.optString("type"))) throw genericFailure();
            String text = requireString(raw, "text", 20000, false);
            if (text.trim().isEmpty()) continue;
            String id = requireString(raw, "id", 128, true), tag = requireString(raw, "speaker", 128, true);
            double start = requireNumber(raw, "start"), end = requireNumber(raw, "end");
            if (!ids.add(id) || start < 0 || end < start || end > duration + 1 || (chars += text.length()) > 300000)
                throw genericFailure();
            String speakerId = speakerIds.get(tag);
            if (speakerId == null) {
                if (speakerIds.size() >= 128) throw genericFailure();
                boolean matched = knownNames.containsKey(tag);
                speakerId = matched ? tag : "speaker_" + (speakerIds.size() + 1);
                speakerIds.put(tag, speakerId);
                speakers.put(new JSONObject().put("id", speakerId)
                        .put("name", matched ? knownNames.get(tag) : "متحدث " + speakerIds.size())
                        .put("matched_reference", matched));
            }
            segments.put(new JSONObject().put("id", id).put("speaker_id", speakerId)
                    .put("start", start).put("end", end).put("text", text));
        }
        JSONObject result = new JSONObject().put("title", title).put("language", "ar")
                .put("duration_seconds", duration).put("segments", segments).put("speakers", speakers);
        validateTranscript(result);
        return result;
    }

    private static JSONObject summarizeTranscript(Configuration config, JSONObject transcript) throws Exception {
        JSONObject clean = new JSONObject(transcript.toString());
        clean.remove("minutes"); clean.remove("_provider_cleanup_pending");
        validateTranscript(clean);
        JSONObject minutes;
        if (clean.getJSONArray("segments").length() == 0) {
            minutes = new JSONObject().put("summary", "لم يُكتشف كلام واضح في التسجيل.")
                    .put("discussion_points", new JSONArray()).put("decisions", new JSONArray())
                    .put("action_items", new JSONArray()).put("open_questions", new JSONArray())
                    .put("speech_annotations", new JSONArray());
        } else {
            minutes = structured(config, ProviderPrompts.SUMMARY, clean, "meeting_minutes",
                    ProviderPrompts.minutesSchema(), null);
            if (minutes.getJSONArray("speech_annotations").length() != clean.getJSONArray("segments").length())
                throw genericFailure();
        }
        clean.put("minutes", minutes);
        return MeetingApi.analysisFields(clean);
    }

    private static JSONObject structured(Configuration config, String instructions, JSONObject payload,
                                          String name, JSONObject schema, JSONObject attachment) throws Exception {
        if (payload.toString().getBytes(StandardCharsets.UTF_8).length > MAX_JSON)
            throw new Failure("النص يتجاوز الحد المسموح للمعالجة: مليونا بايت.");
        JSONObject request;
        String endpoint;
        if (config.provider.equals("openai")) {
            request = new JSONObject().put("model", config.model).put("store", false)
                    .put("input", new JSONArray().put(new JSONObject().put("role", "system").put("content", instructions))
                            .put(new JSONObject().put("role", "user").put("content", payload.toString())))
                    .put("text", new JSONObject().put("format", new JSONObject().put("type", "json_schema")
                            .put("name", name).put("strict", true).put("schema", schema)));
            endpoint = OPENAI + "responses";
        } else {
            JSONArray parts = new JSONArray();
            if (attachment != null) parts.put(attachment);
            parts.put(new JSONObject().put("text", payload.toString()));
            request = new JSONObject().put("systemInstruction", new JSONObject().put("parts", new JSONArray()
                            .put(new JSONObject().put("text", instructions))))
                    .put("contents", new JSONArray().put(new JSONObject().put("role", "user").put("parts", parts)))
                    .put("generationConfig", new JSONObject().put("responseMimeType", "application/json")
                            .put("responseJsonSchema", schema));
            endpoint = GEMINI + "v1beta/models/" + config.model + ":generateContent";
        }
        JSONObject envelope = new JSONObject(request(config, endpoint, "POST", request, READ_TIMEOUT));
        StringBuilder text = new StringBuilder();
        if (config.provider.equals("openai")) {
            if (!"completed".equals(envelope.optString("status"))) throw genericFailure();
            JSONArray output = envelope.getJSONArray("output");
            for (int i = 0; i < output.length(); i++) {
                JSONObject item = output.getJSONObject(i);
                if ("message".equals(item.optString("type"))) {
                    JSONArray content = item.getJSONArray("content");
                    for (int j = 0; j < content.length(); j++) {
                        JSONObject part = content.getJSONObject(j);
                        if ("refusal".equals(part.optString("type"))) throw genericFailure();
                        if ("output_text".equals(part.optString("type"))) text.append(requireString(part, "text", MAX_RESPONSE, true));
                    }
                }
            }
        } else {
            if (envelope.has("promptFeedback") && envelope.getJSONObject("promptFeedback").has("blockReason"))
                throw genericFailure();
            JSONArray candidates = envelope.getJSONArray("candidates");
            if (candidates.length() != 1) throw genericFailure();
            JSONObject candidate = candidates.getJSONObject(0);
            if (!"STOP".equals(candidate.optString("finishReason"))) throw genericFailure();
            JSONArray parts = candidate.getJSONObject("content").getJSONArray("parts");
            for (int i = 0; i < parts.length(); i++) {
                JSONObject part = parts.getJSONObject(i);
                if (part.optBoolean("thought", false)) continue;
                if (!part.has("text") || part.has("functionCall")) throw genericFailure();
                text.append(requireString(part, "text", MAX_RESPONSE, true));
            }
        }
        if (text.length() == 0 || text.length() > MAX_RESPONSE) throw genericFailure();
        JSONObject result = new JSONObject(text.toString());
        validateSchema(result, schema);
        return result;
    }

    private static void validateTranscript(JSONObject transcript) throws Exception {
        requireString(transcript, "title", 160, true);
        requireString(transcript, "language", 80, true);
        double duration = requireNumber(transcript, "duration_seconds");
        if (duration < 0 || duration > 3600) throw genericFailure();
        JSONArray speakers = transcript.getJSONArray("speakers"), segments = transcript.getJSONArray("segments");
        if (speakers.length() > 128 || segments.length() > 2000 || (speakers.length() == 0) != (segments.length() == 0))
            throw genericFailure();
        Set<String> speakerIds = new HashSet<>(), segmentIds = new HashSet<>();
        for (int i = 0; i < speakers.length(); i++) {
            JSONObject speaker = speakers.getJSONObject(i);
            if (!speakerIds.add(requireString(speaker, "id", 128, true))) throw genericFailure();
            requireString(speaker, "name", 80, true);
            if (!(speaker.get("matched_reference") instanceof Boolean)) throw genericFailure();
        }
        long chars = 0;
        for (int i = 0; i < segments.length(); i++) {
            JSONObject segment = segments.getJSONObject(i);
            if (!segmentIds.add(requireString(segment, "id", 128, true))
                    || !speakerIds.contains(requireString(segment, "speaker_id", 128, true))) throw genericFailure();
            double start = requireNumber(segment, "start"), end = requireNumber(segment, "end");
            if (duration <= 0 || start < 0 || end < start || end > duration + 1) throw genericFailure();
            chars += requireString(segment, "text", 20000, true).length();
            if (chars > 300000) throw genericFailure();
        }
    }

    /** Independently enforce the subset used by our schemas; provider structured output is still untrusted. */
    private static void validateSchema(Object value, JSONObject schema) throws Exception {
        JSONArray alternatives = schema.optJSONArray("anyOf");
        if (alternatives != null) {
            for (int i = 0; i < alternatives.length(); i++) {
                try { validateSchema(value, alternatives.getJSONObject(i)); return; } catch (Failure ignored) { }
            }
            throw genericFailure();
        }
        String type = schema.getString("type");
        if (type.equals("null")) { if (value != JSONObject.NULL) throw genericFailure(); return; }
        if (type.equals("object")) {
            if (!(value instanceof JSONObject)) throw genericFailure();
            JSONObject object = (JSONObject) value, properties = schema.getJSONObject("properties");
            if (object.length() != properties.length()) throw genericFailure();
            Iterator<String> keys = properties.keys();
            while (keys.hasNext()) { String key = keys.next(); if (!object.has(key)) throw genericFailure();
                validateSchema(object.get(key), properties.getJSONObject(key)); }
        } else if (type.equals("array")) {
            if (!(value instanceof JSONArray)) throw genericFailure();
            JSONArray values = (JSONArray) value;
            if (values.length() < schema.optInt("minItems", 0) || values.length() > schema.optInt("maxItems", Integer.MAX_VALUE))
                throw genericFailure();
            for (int i = 0; i < values.length(); i++) validateSchema(values.get(i), schema.getJSONObject("items"));
        } else if (type.equals("string")) {
            if (!(value instanceof String)) throw genericFailure();
            String text = (String) value;
            if (text.length() > schema.optInt("maxLength", Integer.MAX_VALUE)
                    || text.length() < schema.optInt("minLength", 0)
                    || (schema.optInt("minLength", 0) > 0 && text.trim().isEmpty())) throw genericFailure();
            JSONArray allowed = schema.optJSONArray("enum");
            if (allowed != null) {
                boolean found = false;
                for (int i = 0; i < allowed.length(); i++) if (text.equals(allowed.getString(i))) found = true;
                if (!found) throw genericFailure();
            }
        } else if (type.equals("number")) {
            if (!(value instanceof Number)) throw genericFailure();
            double number = ((Number) value).doubleValue();
            if (!finite(number) || number < schema.optDouble("minimum", -Double.MAX_VALUE)
                    || number > schema.optDouble("maximum", Double.MAX_VALUE)) throw genericFailure();
        } else throw genericFailure();
    }

    static URI validateOfficialUrl(String value, String provider) throws Exception {
        try {
            URI uri = new URI(value == null ? "" : value);
            String host = provider.equals("openai") ? "api.openai.com" : "generativelanguage.googleapis.com";
            if (!"https".equals(uri.getScheme()) || !host.equalsIgnoreCase(uri.getHost()) || uri.getRawUserInfo() != null
                    || uri.getRawFragment() != null || (uri.getPort() != -1 && uri.getPort() != 443)
                    || uri.isOpaque() || uri.getPath() == null || !uri.getPath().startsWith("/")) throw new Exception();
            return uri;
        } catch (Exception e) { throw new Failure("عنوان المزوّد غير موثوق؛ لم يُرسل المفتاح أو التسجيل إليه."); }
    }

    private static HttpURLConnection open(Configuration config, String endpoint, String method,
                                           int timeout, boolean authentication) throws Exception {
        checkCancelled();
        URI uri = validateOfficialUrl(endpoint, config.provider);
        ConnectionFactory factory = TEST_CONNECTIONS.get();
        HttpURLConnection connection = factory == null ? (HttpURLConnection) uri.toURL().openConnection() : factory.open(uri);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestMethod(method);
        connection.setConnectTimeout(15_000); connection.setReadTimeout(timeout); connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");
        if (authentication) connection.setRequestProperty(config.provider.equals("openai") ? "Authorization" : "x-goog-api-key",
                config.provider.equals("openai") ? "Bearer " + config.key : config.key);
        return connection;
    }

    private static String request(Configuration config, String endpoint, String method,
                                   JSONObject payload, int timeout) throws Exception {
        HttpURLConnection connection = null;
        try {
            connection = open(config, endpoint, method, timeout, true);
            if (payload != null) sendJson(connection, payload);
            return readResponse(connection);
        } finally { if (connection != null) connection.disconnect(); }
    }
    private static void sendJson(HttpURLConnection connection, JSONObject payload) throws Exception {
        byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_JSON + 64 * 1024) throw new Failure("النص يتجاوز الحد المسموح للمعالجة.");
        connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
        connection.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream output = connection.getOutputStream()) { writeBytes(output, bytes); }
    }
    private static String readResponse(HttpURLConnection connection) throws Exception {
        checkCancelled();
        int status = connection.getResponseCode();
        if (status < 200 || status >= 300) {
            // Raw provider errors can contain prompts, uploaded URLs, or credentials. Never display them.
            try (InputStream ignored = connection.getErrorStream()) { }
            String detail = status == 401 || status == 403 ? "تحقق من مفتاح API وصلاحياته لدى المزوّد."
                    : status == 429 ? "تحقق من رصيد API وحدود الاستخدام، ثم أعد المحاولة لاحقًا."
                    : status == 404 ? "النموذج غير متاح لهذا المفتاح؛ راجع اسم النموذج وإتاحته لدى المزوّد."
                    : status >= 300 && status < 400 ? "رُفض تحويل الطلب إلى عنوان آخر لحماية التسجيل والمفتاح."
                    : status >= 500 ? "المزوّد غير متاح مؤقتًا؛ أعد المحاولة لاحقًا."
                    : "تعذر قبول الطلب؛ راجع النموذج وصيغة الملف وإعدادات API.";
            throw new Failure("رفض المزوّد الطلب (HTTP " + status + "). " + detail);
        }
        if (connection.getContentLengthLong() > MAX_RESPONSE) throw genericFailure();
        try (InputStream input = connection.getInputStream()) {
            return new String(readBytes(input, MAX_RESPONSE), StandardCharsets.UTF_8);
        }
    }
    private static byte[] readBytes(InputStream input, int limit) throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buffer = new byte[8192]; int count;
        while ((count = input.read(buffer)) != -1) {
            checkCancelled(); if (bytes.size() + count > limit) throw genericFailure(); bytes.write(buffer, 0, count);
        }
        return bytes.toByteArray();
    }
    private static void validateAudio(File file, long limit, boolean reference) throws Exception {
        if (file == null || !file.isFile() || !file.canRead() || file.length() <= 0 || file.length() > limit)
            throw new Failure(reference ? "المرجع الصوتي غير صالح أو يتجاوز ميغابايت واحدًا." : "التسجيل غير صالح أو يتجاوز 24 ميغابايت.");
        String extension = extension(file);
        if (!(extension.equals("m4a") || (!reference && (extension.equals("mp3") || extension.equals("wav")))))
            throw new Failure(reference ? "يجب أن يكون المرجع الصوتي بصيغة M4A." : "اختر تسجيلًا بصيغة M4A أو MP3 أو WAV.");
    }
    private static String extension(File file) {
        String name = file.getName().toLowerCase(Locale.ROOT); int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot + 1);
    }
    private static String geminiMime(File file) { return extension(file).equals("m4a") ? "audio/m4a" : mime(file); }
    private static String mime(File file) { return extension(file).equals("mp3") ? "audio/mpeg" : extension(file).equals("wav") ? "audio/wav" : "audio/mp4"; }
    private static void streamFile(OutputStream output, File file, long expected) throws Exception {
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[16 * 1024]; long total = 0; int count;
            while ((count = input.read(buffer)) != -1) { checkCancelled(); total += count;
                if (total > expected) throw genericFailure(); output.write(buffer, 0, count); }
            if (total != expected) throw genericFailure();
        }
    }
    private static void field(OutputStream output, String boundary, String name, String value) throws Exception {
        write(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name
                + "\"\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n" + value + "\r\n");
    }
    private static void write(OutputStream output, String text) throws Exception { writeBytes(output, text.getBytes(StandardCharsets.UTF_8)); }
    private static void writeBytes(OutputStream output, byte[] bytes) throws Exception {
        for (int i = 0; i < bytes.length; i += 16 * 1024) { checkCancelled(); output.write(bytes, i, Math.min(16 * 1024, bytes.length - i)); }
    }
    private static String requireString(JSONObject object, String key, int max, boolean nonempty) throws Exception {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String) value).length() > max || (nonempty && ((String) value).trim().isEmpty()))
            throw genericFailure(); return (String) value;
    }
    private static double requireNumber(JSONObject object, String key) throws Exception {
        if (!(object.get(key) instanceof Number)) throw genericFailure(); double value = object.getDouble(key);
        if (!finite(value)) throw genericFailure(); return value;
    }
    private static boolean finite(double value) { return !Double.isNaN(value) && !Double.isInfinite(value); }
    private static void checkCancelled() throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("Request cancelled");
    }
    private static Failure cancelled() { return new Failure("أُلغي طلب الذكاء الاصطناعي. التسجيل والمحضر المحليان محفوظان."); }
    private static Failure genericFailure() { return new Failure("تعذر إكمال المعالجة أو كان ردّ المزوّد ناقصًا أو غير صالح. "
            + "التسجيل والمحضر المحليان محفوظان؛ تحقق من الاتصال وإعدادات API ثم أعد المحاولة."); }
}
