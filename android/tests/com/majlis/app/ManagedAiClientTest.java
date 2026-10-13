package com.majlis.app;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Base64;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Real local HTTP/file/JSON contracts. No provider, credentials, or Firebase project is used. */
public final class ManagedAiClientTest {
    private static final String CERT = "0123456789ABCDEF0123456789ABCDEF01234567";
    private static final String PUBLIC_KEY = "AIza0123456789ABCDEFGHIJKLMNOPQRSTUVWXY";
    private static int passed;
    private static File directory, audio;

    public static void main(String[] args) throws Exception {
        directory = Files.createTempDirectory("majlis-managed-ai-").toFile();
        audio = new File(directory, "meeting.m4a");
        Files.write(audio.toPath(), "local recording test bytes".getBytes(StandardCharsets.UTF_8));
        try { run(); } finally {
            ManagedAiClient.setConnectionsForTests(null);
            for (File file : directory.listFiles()) file.delete();
            directory.delete();
        }
        System.out.println("PASS: " + passed + " managed-cloud contract checks");
    }

    private static void run() throws Exception {
        test("configuration is absent or disabled without asking users for a key", () -> {
            for (JSONObject config : new JSONObject[]{new JSONObject(), config().put("enabled", false), config().put("api_key", "")})
                expectFailure(() -> ManagedAiClient.configuration(config), ManagedAiClient.UNAVAILABLE);
            check(ManagedAiClient.configuration(config()).model.equals("gemini-3.5-flash-lite"), "Wrong default model");
        });
        test("configuration rejects endpoint and header injection", () -> {
            for (String field : new String[]{"project_id", "app_id", "api_key", "model"}) {
                JSONObject unsafe = config().put(field, field.equals("api_key") ? "key\nprivate" : "../private?key=secret");
                expectFailure(() -> ManagedAiClient.configuration(unsafe), ManagedAiClient.UNAVAILABLE);
            }
        });
        test("Play Integrity project number is a positive signed long", () -> {
            check(ManagedAiClient.configuration(config()).projectNumber.equals("123456"), "Sender id missing");
            for (String number : new String[]{"0", "-1", "9223372036854775808", "99999999999999999999999999999999999999"})
                expectFailure(() -> ManagedAiClient.configuration(config().put("app_id", "1:" + number + ":android:abcdef123456")), ManagedAiClient.UNAVAILABLE);
            expectFailure(() -> ManagedAiClient.configuration(config().put("api_key", "not-a-public-firebase-key")), ManagedAiClient.UNAVAILABLE);
        });
        test("analysis uses fixed Firebase endpoint inline audio and independent limited-use tokens", () -> {
            AtomicInteger calls = new AtomicInteger(), tokens = new AtomicInteger();
            JSONArray profiles = new JSONArray().put(new JSONObject().put("id", "PRIVATE_ID")
                    .put("name", "PRIVATE_NAME").put("path", "/PRIVATE_PATH/nonexistent.m4a"));
            try (Server server = new Server(exchange -> {
                int call = calls.incrementAndGet();
                check(exchange.getRequestMethod().equals("POST"), "Expected POST");
                check(exchange.getRequestURI().getPath().equals("/v1beta/projects/majlis-test/models/gemini-3.5-flash-lite:generateContent"), "Wrong endpoint");
                check(exchange.getRequestURI().getRawQuery() == null, "Credentials in query");
                check(PUBLIC_KEY.equals(exchange.getRequestHeaders().getFirst("x-goog-api-key")), "Public Firebase key missing");
                check(("limited-token-" + call).equals(exchange.getRequestHeaders().getFirst("X-Firebase-AppCheck")), "Token not acquired per request");
                check("com.majlis.app".equals(exchange.getRequestHeaders().getFirst("X-Android-Package")), "Package missing");
                check(CERT.equals(exchange.getRequestHeaders().getFirst("X-Android-Cert")), "Certificate format changed");
                check("1:123456:android:abcdef123456".equals(exchange.getRequestHeaders().getFirst("X-Firebase-AppId")), "App id missing");
                String body = read(exchange.getRequestBody());
                check(!body.contains("PRIVATE_") && !body.contains(PUBLIC_KEY) && !body.contains("limited-token"), "Private metadata/header in payload");
                JSONObject request = new JSONObject(body), generation = request.getJSONObject("generationConfig");
                check(generation.getString("responseMimeType").equals("application/json") && generation.has("responseJsonSchema")
                        && !generation.has("responseSchema"), "Structured output missing");
                JSONArray parts = request.getJSONArray("contents").getJSONObject(0).getJSONArray("parts");
                if (call == 1) {
                    check(request.getJSONObject("systemInstruction").getJSONArray("parts").getJSONObject(0).getString("text").equals(ProviderPrompts.TRANSCRIBE), "Wrong transcription rules");
                    JSONObject inline = parts.getJSONObject(0).getJSONObject("inlineData");
                    check(inline.getString("mimeType").equals("audio/mp4"), "Wrong M4A MIME");
                    check(new String(Base64.getDecoder().decode(inline.getString("data")), StandardCharsets.UTF_8).equals("local recording test bytes"), "Wrong audio");
                    JSONObject source = new JSONObject(parts.getJSONObject(1).getString("text"));
                    check(source.length() == 1 && source.getDouble("actual_duration_seconds") == 10, "Unexpected transcription metadata");
                    respond(exchange, 200, envelope(transcript("voice-A", "voice-B", "voice-A")).toString());
                } else {
                    check(parts.length() == 1 && !body.contains("inlineData"), "Audio resent for summary");
                    JSONObject source = new JSONObject(parts.getJSONObject(0).getString("text"));
                    check(source.getString("title").equals("اجتماع"), "Meeting title missing");
                    check(!source.getJSONArray("speakers").getJSONObject(0).getBoolean("matched_reference"), "Stored reference matched");
                    respond(exchange, 200, envelope(minutes(3)).toString());
                }
            })) {
                JSONObject result = client(() -> "limited-token-" + tokens.incrementAndGet()).analyze(audio, "اجتماع", profiles, 10);
                check(result.getJSONArray("speakers").length() == 2 && calls.get() == 2 && tokens.get() == 2, "Wrong request/speaker count");
                check(result.getJSONArray("segments").getJSONObject(2).getString("speaker_id").equals("speaker_1"), "Returning voice changed id");
                check(!result.toString().contains("voice-A") && !result.toString().contains("PRIVATE_"), "Provider names or private profile leaked");
                server.check();
            }
        });
        test("silence produces empty minutes without an extra generation", () -> {
            AtomicInteger calls = new AtomicInteger();
            try (Server server = new Server(exchange -> { calls.incrementAndGet(); read(exchange.getRequestBody()); respond(exchange, 200, envelope(transcript()).toString()); })) {
                JSONObject result = client().analyze(audio, "اجتماع", null, 10);
                check(result.getJSONArray("segments").length() == 0 && result.getJSONObject("minutes").getJSONArray("decisions").length() == 0
                        && result.getString("language").equals("unknown"), "False speech/decision/language");
                check(calls.get() == 1, "Silence incurred another generation"); server.check();
            }
        });
        test("summary uploads a whitelist and preserves edited multilingual transcript", () -> {
            JSONObject original = meeting().put("language", "mul").put("_audio_path", "PRIVATE_PATH").put("api_key", "PRIVATE_KEY")
                    .put("_code_notes", "PRIVATE_CODE").put("translations", "PRIVATE_TRANSLATION");
            original.getJSONArray("segments").getJSONObject(0).put("_local", "PRIVATE_SEGMENT").put("text", "corrected English words");
            original.getJSONObject("minutes").put("_local", "PRIVATE_MINUTES");
            try (Server server = new Server(exchange -> {
                JSONObject request = new JSONObject(read(exchange.getRequestBody()));
                String sourceText = textPayload(request);
                check(!sourceText.contains("PRIVATE_") && !sourceText.contains("minutes"), "Local metadata or previous minutes uploaded");
                JSONObject source = new JSONObject(sourceText);
                check(source.getString("language").equals("mul") && source.getJSONArray("segments").getJSONObject(0).getString("text").equals("corrected English words"), "Edits/language lost");
                JSONObject mixed = minutes(2); mixed.getJSONArray("speech_annotations").getJSONObject(0).put("language", "en");
                respond(exchange, 200, envelope(mixed).toString());
            })) {
                JSONObject result = client().summarize(original);
                check(result.getString("language").equals("mul") && original.has("api_key"), "Original mutated"); server.check();
            }
        });
        test("meeting language reflects estimated transcript language and Arabic minutes stay separate", () -> {
            for (String[] languages : new String[][]{{"zh"}, {"ar", "he"}, {"unknown"}, {"he", "unknown"}, {"mul"}}) {
                AtomicInteger calls = new AtomicInteger();
                String expected = languages[0].equals("ar") || languages[0].equals("mul") ? "mul" : languages[0];
                try (Server server = new Server(exchange -> {
                    read(exchange.getRequestBody());
                    if (calls.incrementAndGet() == 1) {
                        String[] voices = new String[languages.length]; for (int i = 0; i < voices.length; i++) voices[i] = "A";
                        respond(exchange, 200, envelope(transcript(voices)).toString());
                    } else {
                        JSONObject value = minutes(languages.length);
                        for (int i = 0; i < languages.length; i++) value.getJSONArray("speech_annotations").getJSONObject(i).put("language", languages[i]);
                        respond(exchange, 200, envelope(value).toString());
                    }
                })) {
                    JSONObject result = client().analyze(audio, "اجتماع", null, 10);
                    check(result.getString("language").equals(expected), "Wrong aggregate language");
                    check(result.getJSONObject("minutes").getString("summary").equals("راجع المشاركون التقرير."), "Minutes changed language"); server.check();
                }
            }
        });
        test("translation sends whitelisted minutes and preserves exact ordered segment ids", () -> {
            JSONObject original = meeting().put("_audio_path", "PRIVATE_PATH").put("token", "PRIVATE_TOKEN");
            original.getJSONObject("minutes").getJSONArray("decisions").getJSONObject(0).put("_local", "PRIVATE_DECISION");
            original.getJSONArray("speakers").getJSONObject(0).put("path", "PRIVATE_REFERENCE");
            try (Server server = new Server(exchange -> {
                JSONObject request = new JSONObject(read(exchange.getRequestBody())); String sourceText = textPayload(request);
                check(!sourceText.contains("PRIVATE_") && !sourceText.contains("inlineData"), "Local data uploaded");
                JSONObject source = new JSONObject(sourceText);
                check(source.getJSONObject("meeting").getJSONObject("minutes").getJSONArray("decisions").length() == 1, "Minutes lost");
                check(source.getString("target_language").equals("he"), "Wrong target");
                respond(exchange, 200, envelope(translation("he", "s1", "s2")).toString());
            })) {
                JSONObject result = client().translate(original, "he");
                check(result.getJSONArray("segments").getJSONObject(1).getString("id").equals("s2"), "ID changed"); server.check();
            }
        });
        test("all twelve translation targets are supported", () -> {
            for (String target : new String[]{"en", "fr", "de", "es", "tr", "he", "ru", "el", "uk", "zh", "fa", "ur"})
                try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, envelope(translation(target, "s1", "s2")).toString()); })) {
                    check(client().translate(meeting(), target).getString("target_language").equals(target), "Unsupported target"); server.check();
                }
        });
        test("local status checks attestation without generation or uploading", () -> {
            AtomicInteger tokens = new AtomicInteger();
            expectNoRequest(() -> {
                JSONObject status = client(() -> { tokens.incrementAndGet(); return "limited-token"; }).status();
                check(status.getString("availability").equals("attestation_only") && status.getInt("reference_speakers") == 0, "Claimed remote availability or voice matching");
                check(status.getLong("max_audio_bytes") == 13_000_000 && status.getInt("max_duration_seconds") == 3600 && tokens.get() == 1, "Wrong limits");
            }, null);
        });
        test("size duration format title rejected before acquiring token or network", () -> {
            expectNoRequest(() -> validationClient().analyze(audio, "اجتماع", null, 0), "مدته");
            expectNoRequest(() -> validationClient().analyze(audio, "اجتماع", null, 3601), "مدته");
            expectNoRequest(() -> validationClient().analyze(audio, "اجتماع", null, Double.NaN), "مدته");
            expectNoRequest(() -> validationClient().analyze(audio, " ", null, 10), "عنوان");
            File large = new File(directory, "large.m4a");
            try (RandomAccessFile file = new RandomAccessFile(large, "rw")) { file.setLength(13_000_001); }
            try { expectNoRequest(() -> validationClient().analyze(large, "اجتماع", null, 10), "13"); } finally { large.delete(); }
            File wrong = new File(directory, "unsafe.txt"); Files.write(wrong.toPath(), new byte[]{1});
            try { expectNoRequest(() -> validationClient().analyze(wrong, "اجتماع", null, 10), "M4A"); } finally { wrong.delete(); }
            expectNoRequest(() -> validationClient().translate(meeting(), "xx"), "لغة");
        });
        test("MP3 WAV and compressed AAC input MIME contracts", () -> {
            for (String extension : new String[]{"mp3", "wav", "aac"}) {
                File input = new File(directory, "meeting." + extension); Files.write(input.toPath(), new byte[]{1, 2, 3});
                try (Server server = new Server(exchange -> {
                    JSONObject request = new JSONObject(read(exchange.getRequestBody()));
                    String actual = request.getJSONArray("contents").getJSONObject(0).getJSONArray("parts").getJSONObject(0).getJSONObject("inlineData").getString("mimeType");
                    check(actual.equals(extension.equals("mp3") ? "audio/mpeg" : extension.equals("wav") ? "audio/wav" : "audio/aac"), "Wrong MIME");
                    respond(exchange, 200, envelope(transcript()).toString());
                })) { client().analyze(input, "اجتماع", null, 10); server.check(); } finally { input.delete(); }
            }
        });
        test("duplicate transcript ids are rejected", () -> {
            JSONObject duplicate = transcript("A", "A"); duplicate.getJSONArray("segments").getJSONObject(1).put("id", "s1");
            expectAnalyzeFailure(duplicate);
        });
        test("timestamps beyond actual recording duration are rejected", () -> {
            JSONObject beyond = transcript("A"); beyond.getJSONArray("segments").getJSONObject(0).put("end", 12);
            expectAnalyzeFailure(beyond);
        });
        test("negative reversed and string timestamps are rejected", () -> {
            for (Object start : new Object[]{-1, 3, "0"}) {
                JSONObject malformed = transcript("A"); malformed.getJSONArray("segments").getJSONObject(0).put("start", start);
                expectAnalyzeFailure(malformed);
            }
        });
        test("summary invalid citations are rejected", () -> {
            JSONObject invalid = minutes(2); invalid.getJSONArray("decisions").getJSONObject(0).put("segment_ids", new JSONArray().put("invented"));
            expectSummaryFailure(envelope(invalid));
        });
        test("summary unknown annotations are rejected", () -> {
            JSONObject invalid = minutes(2); invalid.getJSONArray("speech_annotations").getJSONObject(1).put("segment_id", "s1");
            expectSummaryFailure(envelope(invalid));
        });
        test("summary empty annotations for speech are rejected", () -> { expectSummaryFailure(envelope(minutes(0))); });
        test("strict schema rejects injected extra fields and invalid nullable owner", () -> {
            expectSummaryFailure(envelope(minutes(2).put("api_key", "PRIVATE_RAW")));
            JSONObject invalid = minutes(2); invalid.getJSONArray("action_items").getJSONObject(0).put("owner", 3); expectSummaryFailure(envelope(invalid));
        });
        test("translation rejects changed omitted duplicate or reordered ids and wrong target", () -> {
            for (JSONObject value : new JSONObject[]{translation("he", "s2", "s1"), translation("he", "s1"),
                    translation("he", "s1", "s1"), translation("en", "s1", "s2")}) {
                try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, envelope(value).toString()); })) {
                    expectFailure(() -> client().translate(meeting(), "he"), "غير صالح"); server.check();
                }
            }
        });
        test("candidate must finish STOP and safety blocks are rejected", () -> {
            for (String reason : new String[]{"MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", ""}) {
                JSONObject blocked = envelope(minutes(2)); blocked.getJSONArray("candidates").getJSONObject(0).put("finishReason", reason);
                expectSummaryFailure(blocked);
            }
            expectSummaryFailure(envelope(minutes(2)).put("promptFeedback", new JSONObject().put("blockReason", "SAFETY")));
            JSONObject blocked = envelope(minutes(2)); blocked.getJSONArray("candidates").getJSONObject(0)
                    .put("safetyRatings", new JSONArray().put(new JSONObject().put("blocked", true))); expectSummaryFailure(blocked);
        });
        test("multiple candidates and function calls are rejected", () -> {
            JSONObject multiple = envelope(minutes(2)); multiple.getJSONArray("candidates").put(multiple.getJSONArray("candidates").getJSONObject(0)); expectSummaryFailure(multiple);
            JSONObject function = envelope(minutes(2)); function.getJSONArray("candidates").getJSONObject(0).getJSONObject("content").getJSONArray("parts")
                    .put(new JSONObject().put("functionCall", new JSONObject().put("name", "upload"))); expectSummaryFailure(function);
            JSONObject executable = envelope(minutes(2)); executable.getJSONArray("candidates").getJSONObject(0).getJSONObject("content").getJSONArray("parts")
                    .getJSONObject(0).put("executableCode", new JSONObject().put("code", "PRIVATE_EXECUTABLE")); expectSummaryFailure(executable);
        });
        test("text parts concatenate while hidden thought text is excluded", () -> {
            String json = minutes(2).toString(); JSONObject response = envelope(minutes(2));
            response.getJSONArray("candidates").getJSONObject(0).getJSONObject("content").put("parts", new JSONArray()
                    .put(new JSONObject().put("text", "PRIVATE_THOUGHT").put("thought", true))
                    .put(new JSONObject().put("text", json.substring(0, json.length() / 2)))
                    .put(new JSONObject().put("text", json.substring(json.length() / 2))));
            try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, response.toString()); })) {
                check(!client().summarize(meeting()).toString().contains("PRIVATE_THOUGHT"), "Thought retained"); server.check();
            }
        });
        test("raw HTTP errors and credentials are never echoed or retried", () -> {
            for (int code : new int[]{401, 403, 404, 429, 500}) {
                AtomicInteger calls = new AtomicInteger();
                try (Server server = new Server(exchange -> { calls.incrementAndGet(); read(exchange.getRequestBody()); respond(exchange, code, PUBLIC_KEY + " limited-token PRIVATE_RAW"); })) {
                    String error = expectFailure(() -> client().summarize(meeting()), code == 429 ? "الاستخدام" : code == 401 || code == 403 ? "التحقق" : "متاحة");
                    check(!error.contains("PRIVATE_") && !error.contains(PUBLIC_KEY) && !error.contains("limited-token"), "Raw error leaked");
                    check(calls.get() == 1, "Request retried and could incur duplicate charge"); server.check();
                }
            }
        });
        test("redirects are refused before forwarding any headers", () -> {
            AtomicInteger calls = new AtomicInteger();
            try (Server server = new Server(exchange -> { calls.incrementAndGet(); read(exchange.getRequestBody()); exchange.getResponseHeaders().set("Location", "https://evil.example/private"); respond(exchange, 307, ""); })) {
                expectFailure(() -> client().summarize(meeting()), "تحويل"); check(calls.get() == 1, "Redirect followed"); server.check();
            }
        });
        test("oversized streamed responses are bounded", () -> {
            try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); exchange.sendResponseHeaders(200, 0);
                try (OutputStream output = exchange.getResponseBody()) { byte[] block = new byte[8192]; for (int i = 0; i < 520; i++) {
                    try { output.write(block); } catch (Exception ignored) { break; }
                } }
            })) { expectFailure(() -> client().summarize(meeting()), "غير صالح"); server.check(); }
        });
        test("oversized declared response is rejected before reading its body", () -> {
            try (Server server = new Server(exchange -> {
                read(exchange.getRequestBody()); exchange.sendResponseHeaders(200, 4 * 1024 * 1024 + 1);
            })) { expectFailure(() -> client().summarize(meeting()), "غير صالح"); server.check(); }
        });
        test("malformed JSON never creates an empty successful report", () -> {
            try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, "PRIVATE_RAW not JSON"); })) {
                String error = expectFailure(() -> client().summarize(meeting()), "غير صالح"); check(!error.contains("PRIVATE_RAW"), "Raw reply echoed"); server.check();
            }
        });
        test("token failures and header injection are sanitized before network", () -> {
            expectNoRequest(() -> client(() -> { throw new Exception("PRIVATE_KEY PRIVATE_TOKEN"); }).status(), "التحقق");
            expectNoRequest(() -> client(() -> "token\r\nAuthorization: PRIVATE_TOKEN").summarize(meeting()), "التحقق");
            expectNoRequest(() -> client(() -> "").status(), "التحقق");
        });
        test("interrupt cancels before token or upload", () -> {
            Thread.currentThread().interrupt();
            try { expectNoRequest(() -> validationClient().analyze(audio, "اجتماع", null, 10), "أُلغي"); } finally { Thread.interrupted(); }
        });
        test("network exceptions never expose provider internals", () -> {
            ManagedAiClient.setConnectionsForTests(uri -> { throw new java.io.IOException("PRIVATE_KEY PRIVATE_TOKEN"); });
            try { String error = expectFailure(() -> client().summarize(meeting()), "الاتصال"); check(!error.contains("PRIVATE_"), "Network error leaked"); }
            finally { ManagedAiClient.setConnectionsForTests(null); }
        });
    }

    private static JSONObject config() throws Exception { return new JSONObject().put("enabled", true).put("project_id", "majlis-test")
            .put("api_key", PUBLIC_KEY).put("app_id", "1:123456:android:abcdef123456"); }
    private static ManagedAiClient client() throws Exception { return client(() -> "limited-token"); }
    private static ManagedAiClient validationClient() throws Exception { return client(() -> { throw new AssertionError("Invalid input acquired a token"); }); }
    private static ManagedAiClient client(ManagedAiClient.TokenProvider provider) throws Exception {
        return new ManagedAiClient(ManagedAiClient.configuration(config()), "com.majlis.app", CERT, provider);
    }
    private static JSONObject minutes(int count) throws Exception {
        JSONArray annotations = new JSONArray(); for (int i = 1; i <= count; i++) annotations.put(new JSONObject().put("segment_id", "s" + i)
                .put("language", "ar").put("status", "clear").put("reason", "نص واضح"));
        return new JSONObject().put("summary", "راجع المشاركون التقرير.").put("discussion_points", new JSONArray().put("مراجعة التقرير"))
                .put("decisions", new JSONArray().put(new JSONObject().put("text", "اعتماد التقرير").put("segment_ids", new JSONArray().put("s1"))))
                .put("action_items", new JSONArray().put(new JSONObject().put("task", "راجع التقرير").put("owner", JSONObject.NULL)
                        .put("due_date", JSONObject.NULL).put("segment_ids", new JSONArray().put("s1"))))
                .put("open_questions", new JSONArray()).put("speech_annotations", annotations);
    }
    private static JSONObject meeting() throws Exception { return new JSONObject().put("title", "اجتماع").put("language", "ar").put("duration_seconds", 10)
            .put("speakers", new JSONArray().put(new JSONObject().put("id", "person1").put("name", "أحمد").put("matched_reference", false)))
            .put("segments", new JSONArray().put(new JSONObject().put("id", "s1").put("speaker_id", "person1").put("start", 0).put("end", 2).put("text", "راجع التقرير"))
                    .put(new JSONObject().put("id", "s2").put("speaker_id", "person1").put("start", 3).put("end", 5).put("text", "نعود غدًا")))
            .put("minutes", minutes(2)); }
    private static JSONObject transcript(String... tags) throws Exception {
        JSONArray segments = new JSONArray(); for (int i = 0; i < tags.length; i++) segments.put(new JSONObject().put("id", "s" + (i + 1))
                .put("speaker", tags[i]).put("start", i * 2).put("end", i * 2 + 1).put("text", "راجع التقرير"));
        return new JSONObject().put("segments", segments);
    }
    private static JSONObject envelope(JSONObject body) throws Exception { return new JSONObject().put("candidates", new JSONArray().put(new JSONObject()
            .put("finishReason", "STOP").put("content", new JSONObject().put("parts", new JSONArray().put(new JSONObject().put("text", body.toString())))))); }
    private static JSONObject translation(String language, String... ids) throws Exception {
        JSONArray segments = new JSONArray(); for (String id : ids) segments.put(new JSONObject().put("id", id).put("text", "translated " + id));
        return new JSONObject().put("target_language", language).put("translated_report", "Complete translated report").put("segments", segments);
    }
    private static String textPayload(JSONObject request) throws Exception { return request.getJSONArray("contents").getJSONObject(0).getJSONArray("parts").getJSONObject(0).getString("text"); }
    private static void expectAnalyzeFailure(JSONObject body) throws Exception {
        try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, envelope(body).toString()); })) {
            expectFailure(() -> client().analyze(audio, "اجتماع", null, 10), "غير صالح"); server.check();
        }
    }
    private static void expectSummaryFailure(JSONObject body) throws Exception {
        try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, body.toString()); })) {
            expectFailure(() -> client().summarize(meeting()), "غير صالح"); server.check();
        }
    }
    private static void expectNoRequest(Action action, String contains) throws Exception {
        AtomicInteger requests = new AtomicInteger(); ManagedAiClient.setConnectionsForTests(uri -> { requests.incrementAndGet(); throw new AssertionError("Unexpected network"); });
        try { if (contains == null) action.run(); else expectFailure(action, contains); check(requests.get() == 0, "Input reached network"); }
        finally { ManagedAiClient.setConnectionsForTests(null); }
    }
    private static String expectFailure(Action action, String contains) throws Exception {
        try { action.run(); } catch (Exception e) { check(e.getMessage() != null && e.getMessage().contains(contains), "Wrong failure: " + e.getMessage()); return e.getMessage(); }
        throw new AssertionError("Expected failure");
    }
    private static String read(InputStream stream) throws Exception {
        try (InputStream input = stream; ByteArrayOutputStream bytes = new ByteArrayOutputStream()) { byte[] buffer = new byte[8192]; int count;
            while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count); return new String(bytes.toByteArray(), StandardCharsets.UTF_8); }
    }
    private static void respond(HttpExchange exchange, int status, String body) throws Exception {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8); exchange.getResponseHeaders().set("Content-Type", "application/json; charset=UTF-8");
        exchange.sendResponseHeaders(status, bytes.length); try (OutputStream output = exchange.getResponseBody()) { output.write(bytes); }
    }
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    private static void test(String name, Action action) throws Exception { action.run(); passed++; System.out.println("ok " + passed + " - " + name); }
    interface Action { void run() throws Exception; }
    interface Handler { void handle(HttpExchange exchange) throws Exception; }
    private static final class Server implements Closeable {
        final HttpServer server; final AtomicReference<Throwable> problem = new AtomicReference<>();
        Server(Handler handler) throws Exception {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> { try { handler.handle(exchange); } catch (Throwable failure) {
                problem.compareAndSet(null, failure); try { respond(exchange, 500, "test server failure"); } catch (Exception ignored) { }
            } finally { exchange.close(); } }); server.start();
            ManagedAiClient.setConnectionsForTests(uri -> {
                ManagedAiClientTest.check(uri.getScheme().equals("https") && uri.getHost().equals("firebasevertexai.googleapis.com")
                        && uri.getPort() == -1 && uri.getRawUserInfo() == null && uri.getRawFragment() == null, "Production endpoint altered");
                HttpURLConnection connection = (HttpURLConnection) new java.net.URL("http://127.0.0.1:" + server.getAddress().getPort() + uri.getRawPath()).openConnection();
                return connection;
            });
        }
        void check() { if (problem.get() != null) throw new AssertionError("Server contract failed", problem.get()); }
        public void close() { ManagedAiClient.setConnectionsForTests(null); server.stop(0); }
    }
}
