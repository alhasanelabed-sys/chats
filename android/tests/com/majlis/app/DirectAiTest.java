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
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Actual local HTTP/file/JSON contract checks. Never calls a provider or uses real credentials. */
public final class DirectAiTest {
    private static int passed;
    private static File directory, audio, reference;
    public static void main(String[] args) throws Exception {
        directory = Files.createTempDirectory("majlis-direct-ai-").toFile();
        audio = new File(directory, "meeting.m4a"); reference = new File(directory, "voice.m4a");
        Files.write(audio.toPath(), "local recording test bytes".getBytes(StandardCharsets.UTF_8));
        Files.write(reference.toPath(), "reference sample".getBytes(StandardCharsets.UTF_8));
        try { run(); } finally {
            DirectAi.setConnectionsForTests(null);
            for (File file : directory.listFiles()) file.delete(); directory.delete();
        }
        System.out.println("PASS: " + passed + " direct-provider contract checks");
    }
    private static void run() throws Exception {
        test("OpenAI multipart authenticated diarization and opaque named reference", () -> {
            AtomicInteger requests = new AtomicInteger();
            try (Server server = new Server(exchange -> {
                int call = requests.incrementAndGet(); String body = read(exchange.getRequestBody());
                check("POST".equals(exchange.getRequestMethod()), "Expected POST");
                check("Bearer test-key".equals(exchange.getRequestHeaders().getFirst("Authorization")), "Missing bearer");
                if (call == 1) {
                    check(exchange.getRequestURI().getPath().equals("/v1/audio/transcriptions"), "Wrong endpoint");
                    check(body.contains("gpt-4o-transcribe-diarize") && body.contains("diarized_json")
                            && body.contains("chunking_strategy") && body.contains("auto"), "Wrong diarization fields");
                    check(body.contains("known_speaker_names[]") && body.contains("known_0")
                            && body.contains("data:audio/mp4;base64,cmVmZXJlbmNlIHNhbXBsZQ=="), "Reference missing");
                    check(!body.contains("أحمد") && !body.contains(reference.getAbsolutePath()), "Reference name/path leaked");
                    check(body.contains("name=\"file\"") && body.contains("local recording test bytes"), "Audio missing");
                    respond(exchange, 200, openTranscript("known_0", "B", "known_0").toString());
                } else {
                    check(exchange.getRequestURI().getPath().equals("/v1/responses"), "Wrong summary endpoint");
                    JSONObject request = new JSONObject(body);
                    check(!request.getBoolean("store") && request.getString("model").equals("gpt-4.1-mini"), "Wrong settings");
                    check(request.getJSONObject("text").getJSONObject("format").getBoolean("strict"), "Missing strict schema");
                    JSONObject source = new JSONObject(request.getJSONArray("input").getJSONObject(1).getString("content"));
                    check(source.getJSONArray("speakers").getJSONObject(0).getString("name").equals("أحمد"), "Known display name lost");
                    respond(exchange, 200, openEnvelope(minutes(3)).toString());
                }
            })) {
                JSONObject result = DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", profiles(), 10);
                check(result.getJSONArray("speakers").length() == 2, "Returning speaker not reused");
                check(result.getJSONArray("speakers").getJSONObject(0).getBoolean("matched_reference"), "Known match lost");
                check(!result.getJSONArray("speakers").getJSONObject(1).getBoolean("matched_reference"), "Unknown matched");
                check(result.getJSONArray("segments").getJSONObject(2).getString("speaker_id").equals("known_0"), "Return id changed");
                check(requests.get() == 2, "Missing vertical slice"); server.check();
            }
        });
        test("summary uploads only whitelisted text and preserves user corrections", () -> {
            JSONObject original = meeting().put("language", "mul"); original.put("_audio_path", "PRIVATE_PATH").put("api_key", "PRIVATE_KEY")
                    .put("_code_notes", "PRIVATE_CODE").put("_provider_cleanup_pending", true);
            original.getJSONArray("segments").getJSONObject(0).put("_local", "PRIVATE_SEGMENT");
            try (Server server = new Server(exchange -> {
                JSONObject body = new JSONObject(read(exchange.getRequestBody()));
                String payload = body.getJSONArray("input").getJSONObject(1).getString("content");
                check(!payload.contains("PRIVATE_") && !payload.contains("minutes"), "Local/old minutes uploaded");
                JSONObject source = new JSONObject(payload);
                check(source.getJSONArray("speakers").getJSONObject(0).getString("name").equals("أحمد"), "Name changed");
                respond(exchange, 200, openEnvelope(minutes(2)).toString());
            })) {
                JSONObject result = DirectAi.summarize("openai", "test-key", "", original);
                check(MeetingApi.transcriptPayload(result).toString().equals(MeetingApi.transcriptPayload(original).toString()), "Transcript changed");
                check(!result.has("_audio_path") && !result.has("api_key"), "Private fields returned"); server.check();
            }
        });
        test("summary rejects unsupported citations", () -> {
            JSONObject invalid = minutes(2); invalid.getJSONArray("decisions").getJSONObject(0).put("segment_ids", new JSONArray().put("missing"));
            expectSummaryFailure(openEnvelope(invalid), "غير صالح");
        });
        test("summary requires annotations for each source segment", () -> {
            JSONObject invalid = minutes(2).put("speech_annotations", new JSONArray());
            expectSummaryFailure(openEnvelope(invalid), "غير صالح");
        });
        test("summary rejects duplicate annotation ids", () -> {
            JSONObject invalid = minutes(2); invalid.getJSONArray("speech_annotations").getJSONObject(1).put("segment_id", "s1");
            expectSummaryFailure(openEnvelope(invalid), "غير صالح");
        });
        test("summary rejects invented fields", () -> expectSummaryFailure(openEnvelope(minutes(2).put("secret", "data")), "غير صالح"));
        test("summary rejects overlong source segment before network", () -> {
            JSONObject source = meeting(); source.getJSONArray("segments").getJSONObject(0).put("text", repeat("x", 20001));
            expectNoRequest(() -> DirectAi.summarize("openai", "test-key", "", source), "غير صالح");
        });
        test("summary validates real numeric timestamp types", () -> {
            JSONObject source = meeting(); source.getJSONArray("segments").getJSONObject(0).put("end", "2");
            expectNoRequest(() -> DirectAi.summarize("openai", "test-key", "", source), "غير صالح");
        });
        test("summary rejects text outside the recording duration", () -> {
            JSONObject source = meeting(); source.getJSONArray("segments").getJSONObject(0).put("end", 12);
            expectNoRequest(() -> DirectAi.summarize("openai", "test-key", "", source), "غير صالح");
        });
        test("unknown language and uninterpretable text remain observations", () -> {
            JSONObject result = minutes(2); result.getJSONArray("speech_annotations").getJSONObject(0)
                    .put("language", "unknown").put("status", "uninterpretable").put("reason", "المعنى غير معروف");
            try (Server server = new Server(exchange -> respond(exchange, 200, openEnvelope(result).toString()))) {
                JSONObject analyzed = DirectAi.summarize("openai", "test-key", "", meeting());
                check(analyzed.getJSONObject("minutes").getJSONArray("speech_annotations").getJSONObject(0)
                        .getString("language").equals("unknown"), "Unknown language lost"); server.check();
            }
        });
        test("translation ordered exact ids and complete report, local data stripped", () -> {
            JSONObject source = meeting().put("_audio_path", "PRIVATE_PATH").put("_translations", "PRIVATE_OLD");
            source.getJSONObject("minutes").put("_code_notes", "PRIVATE_NOTE");
            try (Server server = new Server(exchange -> {
                JSONObject body = new JSONObject(read(exchange.getRequestBody()));
                String payload = body.getJSONArray("input").getJSONObject(1).getString("content");
                check(!payload.contains("PRIVATE_"), "Local data uploaded during translation");
                check(new JSONObject(payload).getString("target_language").equals("he"), "Wrong translation target");
                respond(exchange, 200, openEnvelope(translation("he", "s1", "s2")).toString());
            })) {
                JSONObject result = DirectAi.translate("openai", "test-key", "", source, "he");
                check(result.getString("target_language").equals("he") && result.getJSONArray("segments").length() == 2, "Translation missing");
                server.check();
            }
        });
        test("translation rejects reordered segment ids", () -> expectTranslationFailure(translation("he", "s2", "s1")));
        test("translation rejects omitted segments", () -> expectTranslationFailure(translation("he", "s1")));
        test("translation rejects wrong requested language", () -> expectTranslationFailure(translation("ru", "s1", "s2")));
        test("translation rejects unsupported target before network", () -> expectNoRequest(
                () -> DirectAi.translate("openai", "test-key", "", meeting(), "unknown"), "مدعومة"));
        test("OpenAI rejects incomplete and refusal envelopes", () -> {
            expectSummaryFailure(openEnvelope(minutes(2)).put("status", "incomplete"), "غير صالح");
            JSONObject refusal = new JSONObject().put("status", "completed").put("output", new JSONArray()
                    .put(new JSONObject().put("type", "message").put("content", new JSONArray()
                            .put(new JSONObject().put("type", "refusal").put("refusal", "PRIVATE_KEY")))));
            expectSummaryFailure(refusal, "غير صالح");
        });
        test("OpenAI rejects string durations and nonsegment events", () -> {
            JSONObject raw = openTranscript("A"); raw.put("duration", "10"); expectAnalyzeFailure(raw);
            raw = openTranscript("A"); raw.getJSONArray("segments").getJSONObject(0).put("type", "other"); expectAnalyzeFailure(raw);
        });
        test("OpenAI rejects duplicate transcript ids and timestamps beyond duration", () -> {
            JSONObject raw = openTranscript("A", "B"); raw.getJSONArray("segments").getJSONObject(1).put("id", "s1"); expectAnalyzeFailure(raw);
            raw = openTranscript("A"); raw.getJSONArray("segments").getJSONObject(0).put("end", 12); expectAnalyzeFailure(raw);
        });
        test("silence produces grounded empty minutes without a second generation", () -> {
            AtomicInteger calls = new AtomicInteger();
            try (Server server = new Server(exchange -> { calls.incrementAndGet(); read(exchange.getRequestBody());
                respond(exchange, 200, openTranscript().toString()); })) {
                JSONObject result = DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", new JSONArray(), 10);
                check(result.getJSONArray("speakers").length() == 0 && result.getJSONObject("minutes").getJSONArray("decisions").length() == 0, "Silent decision invented");
                check(calls.get() == 1, "Unneeded silent summary call"); server.check();
            }
        });
        test("Gemini full resumable upload, transcript, minutes, cleanup without references", () -> {
            AtomicInteger generated = new AtomicInteger(), deletes = new AtomicInteger();
            try (Server server = new Server(exchange -> {
                String path = exchange.getRequestURI().getPath(), method = exchange.getRequestMethod();
                String body = read(exchange.getRequestBody());
                if (path.equals("/upload/v1beta/files") && exchange.getRequestURI().getQuery() == null) {
                    check("test-key".equals(exchange.getRequestHeaders().getFirst("x-goog-api-key")), "Init key missing");
                    check("audio/m4a".equals(exchange.getRequestHeaders().getFirst("X-Goog-Upload-Header-Content-Type")), "Gemini M4A MIME wrong");
                    check("start".equals(exchange.getRequestHeaders().getFirst("X-Goog-Upload-Command")), "Missing upload start");
                    check("resumable".equals(exchange.getRequestHeaders().getFirst("X-Goog-Upload-Protocol")), "Wrong protocol");
                    check(Long.toString(audio.length()).equals(exchange.getRequestHeaders().getFirst("X-Goog-Upload-Header-Content-Length")), "Wrong byte size");
                    check(!body.contains("أحمد") && !body.contains("reference"), "Named references uploaded");
                    exchange.getResponseHeaders().set("X-Goog-Upload-URL", "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=test");
                    respond(exchange, 200, "{}");
                } else if (path.equals("/upload/v1beta/files")) {
                    check(exchange.getRequestHeaders().getFirst("x-goog-api-key") == null, "API key forwarded to upload URL");
                    check("upload, finalize".equals(exchange.getRequestHeaders().getFirst("X-Goog-Upload-Command")), "Missing finalization");
                    check(body.equals("local recording test bytes"), "Wrong uploaded recording");
                    respond(exchange, 200, new JSONObject().put("file", geminiFile("ACTIVE")).toString());
                } else if (method.equals("DELETE")) { deletes.incrementAndGet(); respond(exchange, 200, "{}"); }
                else {
                    check(path.equals("/v1beta/models/gemini-3.8-flash:generateContent"), "Wrong generate endpoint");
                    JSONObject request = new JSONObject(body);
                    check(request.getJSONObject("generationConfig").getString("responseMimeType").equals("application/json")
                            && request.getJSONObject("generationConfig").has("responseJsonSchema"), "Structured output missing");
                    if (generated.incrementAndGet() == 1) {
                        check(body.contains("fileData") && body.contains("actual_duration_seconds") && body.contains("audio/m4a"), "Audio/duration/MIME missing");
                        check(!body.contains("أحمد") && !body.contains("reference sample"), "Profiles sent to Gemini");
                        respond(exchange, 200, geminiEnvelope(geminiTranscript("voice_a", "voice_b", "voice_a")).toString());
                    } else respond(exchange, 200, geminiEnvelope(minutes(3)).toString());
                }
            })) {
                JSONObject result = DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", profiles(), 10);
                check(result.getJSONArray("speakers").length() == 2 && !result.getJSONArray("speakers").getJSONObject(0).getBoolean("matched_reference"), "Gemini claimed reference identity");
                check(result.getJSONArray("segments").getJSONObject(0).getString("speaker_id").equals(result.getJSONArray("segments").getJSONObject(2).getString("speaker_id")), "Voice return lost");
                check(generated.get() == 2 && deletes.get() == 1, "Missing generation/cleanup"); server.check();
            }
        });
        test("Gemini cleanup runs when transcription is malformed", () -> {
            AtomicInteger deletes = new AtomicInteger();
            try (Server server = geminiServer(geminiEnvelope(new JSONObject().put("segments", "bad")), false, deletes, "ACTIVE")) {
                expectFailure(() -> DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", new JSONArray(), 10), "غير صالح");
                check(deletes.get() == 1, "Uploaded file retained after failed transcription"); server.check();
            }
        });
        test("Gemini cleanup runs after blocked/truncated response", () -> {
            AtomicInteger deletes = new AtomicInteger();
            JSONObject blocked = geminiEnvelope(geminiTranscript("A")); blocked.getJSONArray("candidates").getJSONObject(0).put("finishReason", "MAX_TOKENS");
            try (Server server = geminiServer(blocked, false, deletes, "ACTIVE")) {
                expectFailure(() -> DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", new JSONArray(), 10), "غير صالح");
                check(deletes.get() == 1, "No cleanup after truncated response"); server.check();
            }
        });
        test("Gemini cleanup failure preserves useful results with explicit retention flag", () -> {
            AtomicInteger deletes = new AtomicInteger();
            try (Server server = geminiServer(geminiEnvelope(geminiTranscript()), true, deletes, "ACTIVE")) {
                JSONObject result = DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", new JSONArray(), 10);
                check(result.getBoolean("_provider_cleanup_pending") && deletes.get() == 1, "Retention failure hidden"); server.check();
            }
        });
        test("Gemini processing polls canonical metadata then cleans up", () -> {
            AtomicInteger deletes = new AtomicInteger();
            try (Server server = geminiServer(geminiEnvelope(geminiTranscript()), false, deletes, "PROCESSING")) {
                DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", new JSONArray(), 10);
                check(deletes.get() == 1, "No cleanup after processing"); server.check();
            }
        });
        test("Gemini rejects foreign upload host before sending recording", () -> {
            AtomicInteger calls = new AtomicInteger();
            try (Server server = new Server(exchange -> { calls.incrementAndGet(); read(exchange.getRequestBody());
                exchange.getResponseHeaders().set("X-Goog-Upload-URL", "https://evil.example/upload/v1beta/files"); respond(exchange, 200, "{}"); })) {
                expectFailure(() -> DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", new JSONArray(), 10), "غير موثوق");
                check(calls.get() == 1, "Audio sent to untrusted host"); server.check();
            }
        });
        test("Gemini rejects unsafe file URI and still deletes canonical file", () -> {
            AtomicInteger calls = new AtomicInteger(), deletes = new AtomicInteger();
            try (Server server = new Server(exchange -> {
                String path = exchange.getRequestURI().getPath(); read(exchange.getRequestBody()); calls.incrementAndGet();
                if (exchange.getRequestMethod().equals("DELETE")) { deletes.incrementAndGet(); respond(exchange, 200, "{}"); }
                else if (exchange.getRequestURI().getQuery() == null) {
                    exchange.getResponseHeaders().set("X-Goog-Upload-URL", "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=test"); respond(exchange, 200, "{}");
                } else respond(exchange, 200, new JSONObject().put("file", geminiFile("ACTIVE").put("uri", "https://evil.example/file")).toString());
            })) {
                expectFailure(() -> DirectAi.analyze("gemini", "test-key", "", audio, "اجتماع", new JSONArray(), 10), "غير موثوق");
                check(deletes.get() == 1 && calls.get() == 3, "Unsafe file URI used or cleanup skipped"); server.check();
            }
        });
        test("OpenAI status checks model capabilities with no meeting upload", () -> {
            AtomicInteger calls = new AtomicInteger();
            try (Server server = new Server(exchange -> {
                check(exchange.getRequestMethod().equals("GET") && read(exchange.getRequestBody()).isEmpty(), "Status uploaded data"); calls.incrementAndGet();
                String path = exchange.getRequestURI().getPath(); String id = path.substring(path.lastIndexOf('/') + 1);
                respond(exchange, 200, new JSONObject().put("id", id).toString());
            })) {
                JSONObject result = DirectAi.status("openai", "test-key", "");
                check(result.getInt("reference_speakers") == 4 && calls.get() == 2, "Diarize availability not checked"); server.check();
            }
        });
        test("Gemini status sends key only in headers and verifies method availability", () -> {
            try (Server server = new Server(exchange -> {
                check(!exchange.getRequestURI().toString().contains("test-key") && read(exchange.getRequestBody()).isEmpty(), "Key/body leaked");
                check("test-key".equals(exchange.getRequestHeaders().getFirst("x-goog-api-key")), "Missing Gemini key");
                respond(exchange, 200, new JSONObject().put("name", "models/gemini-3.8-flash")
                        .put("supportedGenerationMethods", new JSONArray().put("generateContent")).toString());
            })) {
                check(DirectAi.status("gemini", "test-key", "").getInt("reference_speakers") == 0, "Gemini reference claim"); server.check();
            }
        });
        test("provider errors sanitize raw echoed secrets", () -> {
            try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 401, "test-key PRIVATE_KEY raw prompt"); })) {
                String error = expectFailure(() -> DirectAi.status("openai", "test-key", ""), "401");
                check(!error.contains("test-key") && !error.contains("PRIVATE_KEY") && !error.contains("raw prompt"), "Raw error leaked"); server.check();
            }
        });
        test("redirects are rejected and not followed", () -> {
            AtomicInteger requests = new AtomicInteger();
            try (Server server = new Server(exchange -> { requests.incrementAndGet();
                exchange.getResponseHeaders().set("Location", "https://evil.example/private"); respond(exchange, 307, ""); })) {
                expectFailure(() -> DirectAi.status("openai", "test-key", ""), "تحويل"); check(requests.get() == 1, "Followed redirect"); server.check();
            }
        });
        test("bounded streamed provider responses reject oversized envelopes", () -> {
            try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); exchange.sendResponseHeaders(200, 0);
                try (OutputStream output = exchange.getResponseBody()) { byte[] block = new byte[8192];
                    for (int i = 0; i < 520; i++) { try { output.write(block); } catch (Exception ignored) { break; } } }
            })) { expectFailure(() -> DirectAi.status("openai", "test-key", ""), "غير صالح"); server.check(); }
        });
        test("official HTTPS host userinfo port and fragment limits", () -> {
            for (String url : new String[]{"http://api.openai.com/v1/responses", "https://api.openai.com.evil.test/v1/responses",
                    "https://secret@api.openai.com/v1/responses", "https://api.openai.com:8443/v1/responses", "https://api.openai.com/v1/responses#secret"})
                expectFailure(() -> DirectAi.validateOfficialUrl(url, "openai"), "غير موثوق");
            check(DirectAi.validateOfficialUrl("https://api.openai.com:443/v1/responses", "openai").getHost().equals("api.openai.com"), "Official HTTPS rejected");
        });
        test("invalid key provider and model rejected before network", () -> {
            expectNoRequest(() -> DirectAi.status("other", "test-key", ""), "اختر");
            expectNoRequest(() -> DirectAi.status("openai", "test-key\n", ""), "مفتاح");
            expectNoRequest(() -> DirectAi.status("gemini", "", ""), "مفتاح");
            expectNoRequest(() -> DirectAi.status("openai", "test-key", "../anything"), "نموذج");
        });
        test("recording size format duration title validated before upload", () -> {
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", null, 0), "مدته");
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", null, 3601), "مدته");
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", null, Double.NaN), "مدته");
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, " ", null, 10), "عنوان");
            File oversize = new File(directory, "large.m4a"); try (RandomAccessFile file = new RandomAccessFile(oversize, "rw")) { file.setLength(24_000_001); }
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", oversize, "اجتماع", null, 10), "24"); oversize.delete();
            File unsupported = new File(directory, "unsafe.txt"); Files.write(unsupported.toPath(), new byte[]{1});
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", unsupported, "اجتماع", null, 10), "M4A"); unsupported.delete();
        });
        test("references constrained to four and inside canonical recording directory", () -> {
            JSONArray excess = new JSONArray(); for (int i = 0; i < 5; i++) excess.put(profiles().getJSONObject(0));
            expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", excess, 10), "أربعة");
            File outside = File.createTempFile("majlis-outside-", ".m4a"); Files.write(outside.toPath(), new byte[]{1});
            try {
                JSONArray profiles = profiles(); profiles.getJSONObject(0).put("path", outside.getPath());
                expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", profiles, 10), "مجلد");
                File link = new File(directory, "symlink.m4a"); Files.createSymbolicLink(link.toPath(), outside.toPath());
                profiles.getJSONObject(0).put("path", link.getPath());
                expectNoRequest(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", profiles, 10), "مجلد"); link.delete();
            } finally { outside.delete(); }
        });
        test("interrupted request cancels before upload", () -> {
            Thread.currentThread().interrupt();
            try { expectNoRequest(() -> DirectAi.status("openai", "test-key", ""), "أُلغي"); }
            finally { Thread.interrupted(); }
        });
    }

    private static JSONObject minutes(int count) throws Exception {
        JSONArray annotations = new JSONArray(); for (int i = 1; i <= count; i++) annotations.put(new JSONObject()
                .put("segment_id", "s" + i).put("language", "ar").put("status", "clear").put("reason", "نص واضح"));
        return new JSONObject().put("summary", "راجع المشاركون التقرير.").put("discussion_points", new JSONArray().put("مراجعة التقرير"))
                .put("decisions", new JSONArray().put(new JSONObject().put("text", "اعتماد التقرير").put("segment_ids", new JSONArray().put("s1"))))
                .put("action_items", new JSONArray().put(new JSONObject().put("task", "راجع التقرير").put("owner", JSONObject.NULL)
                        .put("due_date", JSONObject.NULL).put("segment_ids", new JSONArray().put("s1"))))
                .put("open_questions", new JSONArray()).put("speech_annotations", annotations);
    }
    private static JSONObject meeting() throws Exception {
        return new JSONObject().put("title", "اجتماع").put("language", "ar").put("duration_seconds", 10)
                .put("speakers", new JSONArray().put(new JSONObject().put("id", "person1").put("name", "أحمد").put("matched_reference", true)))
                .put("segments", new JSONArray().put(new JSONObject().put("id", "s1").put("speaker_id", "person1").put("start", 0).put("end", 2).put("text", "راجع التقرير"))
                        .put(new JSONObject().put("id", "s2").put("speaker_id", "person1").put("start", 3).put("end", 5).put("text", "نعود غدًا")))
                .put("minutes", minutes(2));
    }
    private static JSONArray profiles() throws Exception { return new JSONArray().put(new JSONObject().put("id", "p1").put("name", "أحمد").put("path", reference.getPath())); }
    private static JSONObject openTranscript(String... tags) throws Exception {
        JSONArray segments = new JSONArray(); for (int i = 0; i < tags.length; i++) segments.put(new JSONObject().put("type", "transcript.text.segment")
                .put("id", "s" + (i + 1)).put("speaker", tags[i]).put("start", i * 2).put("end", i * 2 + 1).put("text", "راجع التقرير"));
        return new JSONObject().put("task", "transcribe").put("duration", 10).put("segments", segments);
    }
    private static JSONObject geminiTranscript(String... tags) throws Exception {
        JSONArray segments = openTranscript(tags).getJSONArray("segments"); for (int i = 0; i < segments.length(); i++) segments.getJSONObject(i).remove("type");
        return new JSONObject().put("segments", segments);
    }
    private static JSONObject openEnvelope(JSONObject data) throws Exception { return new JSONObject().put("status", "completed").put("output", new JSONArray()
            .put(new JSONObject().put("type", "message").put("content", new JSONArray().put(new JSONObject().put("type", "output_text").put("text", data.toString()))))); }
    private static JSONObject geminiEnvelope(JSONObject data) throws Exception { return new JSONObject().put("candidates", new JSONArray().put(new JSONObject().put("finishReason", "STOP")
            .put("content", new JSONObject().put("parts", new JSONArray().put(new JSONObject().put("text", data.toString())))))); }
    private static JSONObject geminiFile(String state) throws Exception { return new JSONObject().put("name", "files/localtest").put("state", state)
            .put("uri", "https://generativelanguage.googleapis.com/v1beta/files/localtest"); }
    private static JSONObject translation(String language, String... ids) throws Exception {
        JSONArray segments = new JSONArray(); for (String id : ids) segments.put(new JSONObject().put("id", id).put("text", "translated " + id));
        return new JSONObject().put("target_language", language).put("translated_report", "Complete translated report").put("segments", segments);
    }
    private static Server geminiServer(JSONObject transcriptEnvelope, boolean cleanupFailure, AtomicInteger deletes, String state) throws Exception {
        return new Server(exchange -> {
            String path = exchange.getRequestURI().getPath(); String method = exchange.getRequestMethod(); read(exchange.getRequestBody());
            if (method.equals("DELETE")) { deletes.incrementAndGet(); respond(exchange, cleanupFailure ? 500 : 200, "{}"); }
            else if (method.equals("GET")) respond(exchange, 200, geminiFile("ACTIVE").toString());
            else if (path.equals("/upload/v1beta/files") && exchange.getRequestURI().getQuery() == null) {
                exchange.getResponseHeaders().set("X-Goog-Upload-URL", "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=test"); respond(exchange, 200, "{}");
            } else if (path.equals("/upload/v1beta/files")) respond(exchange, 200, new JSONObject().put("file", geminiFile(state)).toString());
            else respond(exchange, 200, transcriptEnvelope.toString());
        });
    }
    private static void expectSummaryFailure(JSONObject envelope, String text) throws Exception {
        try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, envelope.toString()); })) {
            expectFailure(() -> DirectAi.summarize("openai", "test-key", "", meeting()), text); server.check();
        }
    }
    private static void expectTranslationFailure(JSONObject translation) throws Exception {
        try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, openEnvelope(translation).toString()); })) {
            expectFailure(() -> DirectAi.translate("openai", "test-key", "", meeting(), "he"), "غير صالح"); server.check();
        }
    }
    private static void expectAnalyzeFailure(JSONObject transcript) throws Exception {
        try (Server server = new Server(exchange -> { read(exchange.getRequestBody()); respond(exchange, 200, transcript.toString()); })) {
            expectFailure(() -> DirectAi.analyze("openai", "test-key", "", audio, "اجتماع", null, 10), "غير صالح"); server.check();
        }
    }
    private static void expectNoRequest(Action action, String text) throws Exception {
        AtomicInteger requests = new AtomicInteger();
        DirectAi.setConnectionsForTests(uri -> { requests.incrementAndGet(); throw new AssertionError("Unexpected network"); });
        try { expectFailure(action, text); check(requests.get() == 0, "Invalid input reached network"); }
        finally { DirectAi.setConnectionsForTests(null); }
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
    private static String repeat(String text, int count) { StringBuilder out = new StringBuilder(); for (int i = 0; i < count; i++) out.append(text); return out.toString(); }
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    private static void test(String name, Action action) throws Exception { action.run(); passed++; System.out.println("ok " + passed + " - " + name); }
    interface Action { void run() throws Exception; }
    interface Handler { void handle(HttpExchange exchange) throws Exception; }
    private static final class Server implements Closeable {
        final HttpServer server; final AtomicReference<Throwable> problem = new AtomicReference<>();
        Server(Handler handler) throws Exception {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                try { handler.handle(exchange); } catch (Throwable failure) { problem.compareAndSet(null, failure);
                    try { respond(exchange, 500, "test server failure"); } catch (Exception ignored) { } }
                finally { exchange.close(); }
            }); server.start();
            DirectAi.setConnectionsForTests(uri -> {
                DirectAiTest.check(uri.getScheme().equals("https") && (uri.getHost().equals("api.openai.com") || uri.getHost().equals("generativelanguage.googleapis.com")), "Production endpoint altered");
                return (HttpURLConnection) new java.net.URL("http://127.0.0.1:" + server.getAddress().getPort() + uri.getRawPath()
                        + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery())).openConnection();
            });
        }
        void check() { if (problem.get() != null) throw new AssertionError("Server contract failed", problem.get()); }
        public void close() { DirectAi.setConnectionsForTests(null); server.stop(0); }
    }
}
