package com.majlis.app;

import android.content.Context;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Public-boundary regression tests using real JSON, files, and local HTTP. No provider credentials. */
public final class MeetingDataTest {
    private static int passed;

    public static void main(String[] args) throws Exception {
        test("authenticated status sends no meeting data", () -> {
            AtomicReference<String> problem = new AtomicReference<>();
            try (Server server = new Server(exchange -> {
                if (!"GET".equals(exchange.getRequestMethod())
                        || !"/v1/status".equals(exchange.getRequestURI().toString())
                        || !"Bearer status-secret".equals(exchange.getRequestHeaders().getFirst("Authorization"))
                        || read(exchange.getRequestBody()).length != 0) problem.set("Status request leaked data or missed auth");
                respond(exchange, 200, statusResult().toString());
            })) {
                JSONObject result = MeetingApi.status(server.base(), "status-secret");
                check(result.getInt("max_audio_bytes") == 24_000_000, "Status limit lost");
                check(problem.get() == null, problem.get());
            }
        });
        test("status rejects incompatible schema", () -> {
            try (Server server = new Server(exchange -> respond(exchange, 200,
                    statusResult().put("version", "0.1").toString()))) {
                expectFailure(() -> MeetingApi.status(server.base(), ""), "0.2");
            }
            try (Server server = new Server(exchange -> respond(exchange, 200,
                    statusResult().put("max_audio_bytes", "24000000").toString()))) {
                expectFailure(() -> MeetingApi.status(server.base(), ""), "0.2");
            }
        });
        test("redirects never receive credentials", () -> {
            AtomicInteger forwarded = new AtomicInteger();
            try (Server target = new Server(exchange -> {
                forwarded.incrementAndGet();
                respond(exchange, 200, statusResult().toString());
            }); Server source = new Server(exchange -> {
                exchange.getResponseHeaders().set("Location", target.base() + "/v1/status");
                respond(exchange, 302, "");
            })) {
                expectFailure(() -> MeetingApi.status(source.base(), "keep-private"), "توجيه");
                check(forwarded.get() == 0, "Followed redirect with bearer token");
            }
        });
        test("invalid token rejected before HTTP", () -> {
            AtomicInteger requests = new AtomicInteger();
            try (Server server = new Server(exchange -> {
                requests.incrementAndGet();
                respond(exchange, 200, statusResult().toString());
            })) {
                expectFailure(() -> MeetingApi.status(server.base(), "secret\nInjected: yes"), "رمز");
                check(requests.get() == 0, "Invalid header reached server");
            }
        });
        test("cancelled status performs no HTTP", () -> {
            AtomicInteger requests = new AtomicInteger();
            try (Server server = new Server(exchange -> {
                requests.incrementAndGet();
                respond(exchange, 200, statusResult().toString());
            })) {
                Thread.currentThread().interrupt();
                try {
                    expectFailure(() -> MeetingApi.status(server.base(), ""), "أُلغي");
                } finally {
                    Thread.interrupted();
                }
                check(requests.get() == 0, "Cancelled request sent");
            }
        });
        test("server errors redact bearer tokens", () -> {
            try (Server server = new Server(exchange -> respond(exchange, 401,
                    "{\"detail\":\"invalid keep-this-secret token\"}"))) {
                Exception failure = expectFailure(() -> MeetingApi.status(server.base(), "keep-this-secret"), "401");
                check(!failure.getMessage().contains("keep-this-secret"), "Error revealed access token");
            }
        });
        test("main M4A MP3 WAV use matching multipart MIME", () -> {
            try (Directory directory = new Directory()) {
                String[] formats = {"m4a", "MP3", "wav"};
                String[] mime = {"audio/mp4", "audio/mpeg", "audio/wav"};
                for (int i = 0; i < formats.length; i++) {
                    final String expectedExtension = formats[i].toLowerCase(java.util.Locale.ROOT);
                    final String expectedMime = mime[i];
                    AtomicReference<String> body = new AtomicReference<>();
                    File audio = directory.audio("recording." + formats[i], 30);
                    try (Server server = new Server(exchange -> {
                        body.set(new String(read(exchange.getRequestBody()), StandardCharsets.UTF_8));
                        respond(exchange, 200, fixture().toString());
                    })) {
                        JSONObject result = MeetingApi.analyze(server.base(), "", audio, "اجتماع", new JSONArray());
                        check(result.getJSONArray("segments").length() == 1, "Analysis response lost");
                        check(body.get().contains("filename=\"audio." + expectedExtension + "\""), "Wrong extension");
                        check(body.get().contains("Content-Type: " + expectedMime), "Wrong audio MIME");
                    }
                }
            }
        });
        test("reference format and 1 MB cap remain enforced", () -> {
            try (Directory directory = new Directory()) {
                File main = directory.audio("meeting.mp3", 30);
                File wrongFormat = directory.audio("speaker.mp3", 30);
                expectFailure(() -> MeetingApi.analyze("http://localhost:1", "", main, "اجتماع",
                        profile(wrongFormat)), "M4A");
                File tooLarge = directory.audio("speaker.m4a", 1_000_001);
                expectFailure(() -> MeetingApi.analyze("http://localhost:1", "", main, "اجتماع",
                        profile(tooLarge)), "الحد");
            }
        });
        test("24 MB main cap and canonical reference parent enforced", () -> {
            try (Directory directory = new Directory(); Directory other = new Directory()) {
                File tooLarge = directory.audio("meeting.wav", 24_000_001);
                expectFailure(() -> MeetingApi.analyze("http://localhost:1", "", tooLarge, "اجتماع", new JSONArray()), "الحد");
                File main = directory.audio("meeting.m4a", 30);
                File outside = other.audio("speaker.m4a", 30);
                expectFailure(() -> MeetingApi.analyze("http://localhost:1", "", main, "اجتماع", profile(outside)), "مجلد");
                File symbolic = new File(directory.root, "alias.mp3");
                Files.createSymbolicLink(symbolic.toPath(), outside.toPath());
                expectFailure(() -> MeetingApi.analyze("http://localhost:1", "", symbolic, "اجتماع", new JSONArray()), "مجلد");
            }
        });
        test("resummarization sends a deep transcript-only whitelist", () -> {
            JSONObject original = fixture();
            original.put("_id", "local-record").put("_audio_path", "/private/recording.m4a")
                    .put("token", "never-serialize").put("_minutes_stale", true);
            original.getJSONArray("segments").getJSONObject(0).put("local_path", "/private/segment");
            original.getJSONArray("speakers").getJSONObject(0).put("reference_path", "/private/reference");
            AtomicReference<JSONObject> sent = new AtomicReference<>();
            try (Server server = new Server(exchange -> {
                JSONObject request = new JSONObject(new String(read(exchange.getRequestBody()), StandardCharsets.UTF_8));
                sent.set(request);
                JSONObject result = fixture().put("_id", "server-record").put("_audio_path", "/server/file")
                        .put("_minutes_stale", true);
                result.getJSONArray("segments").getJSONObject(0).put("_server_path", "/server/segment");
                result.getJSONObject("minutes").getJSONArray("action_items").getJSONObject(0).put("completed", true);
                respond(exchange, 200, result.toString());
            })) {
                JSONObject result = MeetingApi.summarize(server.base(), "summarize-secret", original);
                check(keys(sent.get()).equals(new HashSet<>(Arrays.asList("title", "language", "duration_seconds", "segments", "speakers"))), "Unexpected payload fields");
                check(keys(sent.get().getJSONArray("segments").getJSONObject(0)).equals(new HashSet<>(Arrays.asList("id", "speaker_id", "start", "end", "text"))), "Segment metadata leaked");
                check(keys(sent.get().getJSONArray("speakers").getJSONObject(0)).equals(new HashSet<>(Arrays.asList("id", "name", "matched_reference"))), "Speaker metadata leaked");
                check(!sent.get().toString().contains("/private/") && !sent.get().toString().contains("never-serialize"), "Local secret or path leaked");
                check(!result.has("_id") && !result.has("_audio_path") && !result.has("_minutes_stale"), "Server metadata accepted");
                check(!result.getJSONArray("segments").getJSONObject(0).has("_server_path"), "Nested metadata accepted");
                check(!result.getJSONObject("minutes").getJSONArray("action_items").getJSONObject(0).has("completed"), "Remote task completion accepted");
                check(original.getString("_id").equals("local-record"), "Original meeting mutated");
            }
        });
        test("resummarization rejects transcript replacement", () -> {
            JSONObject original = fixture();
            String before = original.toString();
            try (Server server = new Server(exchange -> {
                read(exchange.getRequestBody());
                JSONObject altered = fixture();
                altered.getJSONArray("segments").getJSONObject(0).put("text", "نص غير الذي وافق عليه المستخدم");
                respond(exchange, 200, altered.toString());
            })) {
                expectFailure(() -> MeetingApi.summarize(server.base(), "", original), "غيّرت");
                check(before.equals(original.toString()), "Failed resummary mutated local data");
            }
        });
        test("invalid citations and oversized responses rejected", () -> {
            JSONObject invalid = fixture();
            invalid.getJSONObject("minutes").getJSONArray("decisions").getJSONObject(0)
                    .put("segment_ids", new JSONArray().put("nonexistent"));
            try (Directory directory = new Directory(); Server server = new Server(exchange -> {
                read(exchange.getRequestBody());
                respond(exchange, 200, invalid.toString());
            })) {
                expectFailure(() -> MeetingApi.analyze(server.base(), "", directory.audio("meeting.m4a", 30), "اجتماع", new JSONArray()), "غير صالح");
            }
            char[] huge = new char[4 * 1024 * 1024 + 1];
            Arrays.fill(huge, ' ');
            try (Server server = new Server(exchange -> respond(exchange, 200, new String(huge)))) {
                expectFailure(() -> MeetingApi.status(server.base(), ""), "تعذر");
            }
        });
        test("segment edits preserve identity times citations and original", () -> {
            JSONObject original = fixture().put("_id", "local-meeting").put("_saved_at", 12345);
            String before = original.toString();
            JSONObject edited = MeetingEdits.applySegmentEdit(original, "segment-1", "  سنراجع التقرير غدًا.  ", "speaker-2");
            JSONObject segment = edited.getJSONArray("segments").getJSONObject(0);
            check(before.equals(original.toString()), "Segment edit mutated original");
            check(edited.optBoolean("_minutes_stale"), "Edited minutes not marked stale");
            check(edited.getString("_id").equals("local-meeting") && edited.getLong("_saved_at") == 12345, "Local record identity lost");
            check(segment.getString("id").equals("segment-1") && segment.getDouble("start") == 0.0 && segment.getDouble("end") == 8.0, "Timestamps or id changed");
            check(segment.getString("text").equals("سنراجع التقرير غدًا.") && segment.getString("speaker_id").equals("speaker-2"), "Edit not applied");
            check(edited.getJSONObject("minutes").toString().equals(original.getJSONObject("minutes").toString()), "Existing citations or minutes rewritten");
            expectFailure(() -> MeetingEdits.applySegmentEdit(original, "segment-1", "نص", "unknown"), "متحدث");
            expectFailure(() -> MeetingEdits.applySegmentEdit(original, "absent", "نص", "speaker-1"), "المقطع");
            expectFailure(() -> MeetingEdits.applySegmentEdit(original, "segment-1", " ", "speaker-1"), "نص");
        });
        test("local search normalizes Arabic and searches names summary transcript", () -> {
            try (Directory directory = new Directory()) {
                MeetingStore store = new MeetingStore(directory.context());
                JSONObject first = fixture().put("title", "إِدارة الـمَشروع");
                first.getJSONObject("minutes").put("summary", "راجعنا الميزانية");
                first.getJSONArray("speakers").getJSONObject(0).put("name", "أحمد");
                first.getJSONArray("segments").getJSONObject(0).put("text", "ناقشنا تسليم تقرير API غدًا");
                store.saveMeeting(first);
                JSONObject second = fixture().put("title", "اجتماع آخر");
                store.saveMeeting(second);
                check(store.searchMeetings("ادارة المشروع").length() == 1, "Arabic normalization failed");
                check(store.searchMeetings("الميزانية احمد").length() == 1, "Summary/name cross-field search failed");
                check(store.searchMeetings("api تسليم").length() == 1, "Transcript/case-fold search failed");
                check(store.searchMeetings("كلمةغيرموجودة").length() == 0, "Search returned unrelated data");
                check(store.searchMeetings("  ").length() == 2, "Empty search lost meetings");
                check(store.searchMeetings("ادارة المشروع").getJSONObject(0).getString("title").equals("إِدارة الـمَشروع"), "Search modified stored Arabic text");
            }
        });
        test("storage round-trip preserves edited flags and completed tasks", () -> {
            try (Directory directory = new Directory()) {
                MeetingStore store = new MeetingStore(directory.context());
                JSONObject edited = MeetingEdits.applySegmentEdit(fixture(), "segment-1", "النسخة المصححة", "speaker-1");
                edited.getJSONObject("minutes").getJSONArray("action_items").getJSONObject(0).put("completed", true);
                store.saveMeeting(edited);
                JSONObject saved = store.meetings().getJSONObject(0);
                check(saved.optBoolean("_minutes_stale"), "Stale flag lost during save");
                check(saved.getJSONObject("minutes").getJSONArray("action_items").getJSONObject(0).getBoolean("completed"), "Task state lost during save");
                String report = store.report(saved);
                check(report.contains("لم يُحدَّث") && report.contains("☑") && report.contains("مكتملة"), "Export omitted stale or task state");
                store.deleteMeeting(saved);
                check(store.meetings().length() == 0, "Delete left saved record");
            }
        });
        System.out.println("Passed " + passed + " Android data/API regression checks.");
    }

    private static JSONObject fixture() throws Exception {
        JSONObject meeting = new JSONObject();
        meeting.put("title", "اجتماع الفريق").put("language", "ar").put("duration_seconds", 8.0);
        meeting.put("speakers", new JSONArray()
                .put(new JSONObject().put("id", "speaker-1").put("name", "ليلى").put("matched_reference", false))
                .put(new JSONObject().put("id", "speaker-2").put("name", "عمر").put("matched_reference", false)));
        meeting.put("segments", new JSONArray().put(new JSONObject().put("id", "segment-1")
                .put("speaker_id", "speaker-1").put("start", 0.0).put("end", 8.0).put("text", "سنراجع التقرير غدًا.")));
        meeting.put("minutes", new JSONObject().put("summary", "اتفق الفريق على مراجعة التقرير.")
                .put("discussion_points", new JSONArray().put("تقرير الفريق"))
                .put("decisions", new JSONArray().put(new JSONObject().put("text", "مراجعة التقرير")
                        .put("segment_ids", new JSONArray().put("segment-1"))))
                .put("action_items", new JSONArray().put(new JSONObject().put("task", "مراجعة التقرير")
                        .put("owner", "speaker-1").put("due_date", JSONObject.NULL)
                        .put("segment_ids", new JSONArray().put("segment-1"))))
                .put("open_questions", new JSONArray()));
        return meeting;
    }

    private static JSONObject statusResult() throws Exception {
        return new JSONObject().put("status", "ok").put("version", "0.2")
                .put("max_audio_bytes", 24_000_000).put("max_duration_seconds", 3600)
                .put("formats", new JSONArray().put("m4a").put("mp3").put("wav")).put("reference_speakers", 4);
    }

    private static JSONArray profile(File path) throws Exception {
        return new JSONArray().put(new JSONObject().put("id", "participant-1").put("name", "ليلى").put("path", path.getPath()));
    }

    private static Set<String> keys(JSONObject object) {
        Set<String> keys = new HashSet<>();
        Iterator<String> iterator = object.keys();
        while (iterator.hasNext()) keys.add(iterator.next());
        return keys;
    }

    private static void test(String name, Checked action) throws Exception {
        try {
            action.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable failure) {
            throw new AssertionError("FAIL " + name, failure);
        }
    }

    private static Exception expectFailure(Checked action, String messagePart) throws Exception {
        try {
            action.run();
        } catch (Exception e) {
            check(e.getMessage() != null && e.getMessage().contains(messagePart), "Unexpected failure: " + e.getMessage());
            return e;
        }
        throw new AssertionError("Expected rejection containing " + messagePart);
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static byte[] read(InputStream input) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count);
        return bytes.toByteArray();
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=UTF-8");
        exchange.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
        exchange.close();
    }

    private interface Checked { void run() throws Exception; }
    private interface Handler { void handle(HttpExchange exchange) throws Exception; }

    private static final class Server implements Closeable {
        private final HttpServer server;
        Server(Handler handler) throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                try { handler.handle(exchange); }
                catch (Exception e) {
                    try { respond(exchange, 500, "{\"detail\":\"test handler failed\"}"); }
                    catch (Exception ignored) { exchange.close(); }
                }
            });
            server.start();
        }
        String base() { return "http://127.0.0.1:" + server.getAddress().getPort(); }
        public void close() { server.stop(0); }
    }

    private static final class Directory implements Closeable {
        private final File root;
        Directory() throws IOException { root = Files.createTempDirectory("majlis-data-").toFile(); }
        File audio(String name, long size) throws IOException {
            File file = new File(root, name);
            try (RandomAccessFile output = new RandomAccessFile(file, "rw")) { output.setLength(size); }
            return file;
        }
        Context context() {
            return new Context() { public File getFilesDir() { return root; } };
        }
        public void close() throws IOException { remove(root); }
        private static void remove(File file) throws IOException {
            if (file.isDirectory() && !Files.isSymbolicLink(file.toPath())) {
                File[] children = file.listFiles();
                if (children != null) for (File child : children) remove(child);
            }
            Files.deleteIfExists(file.toPath());
        }
    }
}
