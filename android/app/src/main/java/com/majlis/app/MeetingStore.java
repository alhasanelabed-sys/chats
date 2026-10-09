package com.majlis.app;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** App-private local records. Service credentials are never written here. */
public final class MeetingStore {
    private static final int MAX_JSON_BYTES = 4 * 1024 * 1024;
    private final File root;

    public MeetingStore(Context context) {
        root = context.getFilesDir();
    }

    public synchronized JSONArray profiles() {
        File file = new File(root, "profiles.json");
        if (!file.exists()) return new JSONArray();
        try {
            return checkedProfiles(new JSONArray(read(file)));
        } catch (Exception e) {
            throw storageError("تعذر قراءة أسماء المتحدثين المحفوظة.", e);
        }
    }

    public synchronized void saveProfiles(JSONArray profiles) {
        try {
            JSONArray clean = checkedProfiles(profiles);
            atomicWrite(new File(root, "profiles.json"), clean.toString());
        } catch (Exception e) {
            throw storageError("تعذر حفظ أسماء المتحدثين. تحقق من مساحة الجهاز.", e);
        }
    }

    public synchronized File recordingsDir() {
        return directory(new File(root, "recordings"));
    }

    public synchronized String saveMeeting(JSONObject meeting) {
        try {
            if (meeting == null) throw new IOException("Missing meeting");
            JSONObject copy = new JSONObject(meeting.toString());
            MeetingApi.validateResult(copy);
            String id = copy.optString("_id", "");
            if (!safeId(id)) id = System.currentTimeMillis() + "_" + UUID.randomUUID();
            long savedAt = copy.optLong("_saved_at", 0);
            if (savedAt <= 0) savedAt = System.currentTimeMillis();
            copy.put("_id", id);
            copy.put("_saved_at", savedAt);
            if (copy.has("_audio_path") && !copy.isNull("_audio_path")) {
                File audio = new File(copy.getString("_audio_path"));
                if (!isRecording(audio)) throw new IOException("Audio path outside app recordings");
                copy.put("_audio_path", audio.getCanonicalPath());
            }
            atomicWrite(new File(meetingsDir(), id + ".json"), copy.toString());
            meeting.put("_id", id);
            meeting.put("_saved_at", savedAt);
            return id;
        } catch (Exception e) {
            throw storageError("تعذر حفظ محضر الاجتماع. تحقق من مساحة الجهاز.", e);
        }
    }

    public synchronized JSONArray meetings() {
        List<JSONObject> values = new ArrayList<>();
        File[] files = meetingsDir().listFiles();
        if (files == null) throw storageError("تعذر قراءة الاجتماعات المحفوظة.", null);
        for (File file : files) {
            if (!file.isFile() || !file.getName().endsWith(".json")) continue;
            String id = file.getName().substring(0, file.getName().length() - 5);
            if (!safeId(id)) continue;
            try {
                JSONObject meeting = new JSONObject(read(file));
                if (!id.equals(meeting.optString("_id")) || meeting.optJSONObject("minutes") == null
                        || meeting.optJSONArray("segments") == null || meeting.optJSONArray("speakers") == null
                        || meeting.optLong("_saved_at", 0) <= 0) continue;
                MeetingApi.validateResult(meeting);
                values.add(meeting);
            } catch (Exception ignored) {
                // Leave malformed files untouched; they must never replace a valid record.
            }
        }
        values.sort(Comparator.comparingLong((JSONObject item) -> item.optLong("_saved_at", 0))
                .reversed().thenComparing(item -> item.optString("_id")));
        JSONArray result = new JSONArray();
        for (JSONObject value : values) result.put(value);
        return result;
    }

    public synchronized void deleteMeeting(JSONObject meeting) {
        try {
            String id = meeting == null ? "" : meeting.optString("_id", "");
            if (!safeId(id)) throw new IOException("Invalid meeting id");
            if (meeting.has("_audio_path") && !meeting.isNull("_audio_path")) {
                File audio = new File(meeting.getString("_audio_path"));
                if (isRecording(audio) && audio.exists() && !audio.delete()) {
                    throw new IOException("Could not remove recording");
                }
            }
            File file = new File(meetingsDir(), id + ".json");
            if (file.exists() && !file.delete()) throw new IOException("Could not remove meeting");
        } catch (Exception e) {
            throw storageError("تعذر حذف الاجتماع أو تسجيله من الجهاز.", e);
        }
    }

    public String report(JSONObject meeting) {
        if (meeting == null) return "";
        Map<String, String> names = new HashMap<>();
        JSONArray speakers = meeting.optJSONArray("speakers");
        if (speakers != null) {
            for (int i = 0; i < speakers.length(); i++) {
                JSONObject speaker = speakers.optJSONObject(i);
                if (speaker != null) names.put(speaker.optString("id"), speaker.optString("name", "متحدث"));
            }
        }
        StringBuilder text = new StringBuilder();
        if (meeting.optBoolean("_demo", false)) text.append("مثال تجريبي — هذا المحضر ليس ناتجًا عن تسجيل حقيقي.\n\n");
        text.append("محضر اجتماع: ").append(meeting.optString("title", "اجتماع")).append('\n');
        long savedAt = meeting.optLong("_saved_at", 0);
        if (savedAt > 0) {
            text.append("تاريخ الحفظ: ").append(DateFormat.getDateTimeInstance(DateFormat.MEDIUM,
                    DateFormat.SHORT, new Locale("ar")).format(new Date(savedAt))).append('\n');
        }
        text.append("مدة التسجيل: ").append(time(meeting.optDouble("duration_seconds", 0))).append('\n');
        if (speakers != null && speakers.length() > 0) {
            text.append("المتحدثون: ");
            for (int i = 0; i < speakers.length(); i++) {
                JSONObject speaker = speakers.optJSONObject(i);
                if (i > 0) text.append("، ");
                if (speaker != null) text.append(names.get(speaker.optString("id")));
            }
            text.append('\n');
        }
        JSONObject minutes = meeting.optJSONObject("minutes");
        if (minutes != null) {
            text.append("\nالملخص\n").append(minutes.optString("summary", "لم يُقدّم ملخص.")).append('\n');
            appendList(text, "نقاط النقاش", minutes.optJSONArray("discussion_points"));
            text.append("\nالقرارات\n");
            JSONArray decisions = minutes.optJSONArray("decisions");
            if (decisions == null || decisions.length() == 0) text.append("لم تُسجّل قرارات صريحة.\n");
            else {
                for (int i = 0; i < decisions.length(); i++) {
                    JSONObject decision = decisions.optJSONObject(i);
                    if (decision == null) continue;
                    text.append("• ").append(decision.optString("text"));
                    appendCitations(text, decision.optJSONArray("segment_ids"));
                    text.append('\n');
                }
            }
            text.append("\nالمهام والمتابعة\n");
            JSONArray actions = minutes.optJSONArray("action_items");
            if (actions == null || actions.length() == 0) text.append("لم تُسجّل مهام صريحة.\n");
            else {
                for (int i = 0; i < actions.length(); i++) {
                    JSONObject action = actions.optJSONObject(i);
                    if (action == null) continue;
                    String owner = nullableText(action, "owner", "غير محدد");
                    text.append("• ").append(action.optString("task"))
                            .append(" — المسؤول: ").append(names.containsKey(owner) ? names.get(owner) : owner)
                            .append("؛ الموعد: ").append(nullableText(action, "due_date", "غير محدد"));
                    appendCitations(text, action.optJSONArray("segment_ids"));
                    text.append('\n');
                }
            }
            appendList(text, "الأسئلة المفتوحة", minutes.optJSONArray("open_questions"));
        }
        text.append("\nالتفريغ حسب المتحدث\n");
        JSONArray segments = meeting.optJSONArray("segments");
        if (segments != null) {
            for (int i = 0; i < segments.length(); i++) {
                JSONObject segment = segments.optJSONObject(i);
                if (segment == null) continue;
                String speakerId = segment.optString("speaker_id");
                text.append('[').append(time(segment.optDouble("start", 0))).append(" – ")
                        .append(time(segment.optDouble("end", 0))).append("] ")
                        .append(names.containsKey(speakerId) ? names.get(speakerId) : "متحدث غير محدد")
                        .append(" (").append(segment.optString("id")).append("): ")
                        .append(segment.optString("text")).append('\n');
            }
        }
        text.append("\nمحضر مولّد آليًا؛ راجع الأسماء والقرارات والمهام قبل الاعتماد.\n");
        return text.toString();
    }

    private static void appendList(StringBuilder text, String heading, JSONArray values) {
        text.append('\n').append(heading).append('\n');
        if (values == null || values.length() == 0) text.append("لا توجد بنود مسجلة.\n");
        else for (int i = 0; i < values.length(); i++) text.append("• ").append(values.optString(i)).append('\n');
    }

    private static void appendCitations(StringBuilder text, JSONArray ids) {
        if (ids == null || ids.length() == 0) return;
        text.append(" [المقاطع: ");
        for (int i = 0; i < ids.length(); i++) {
            if (i > 0) text.append("، ");
            text.append(ids.optString(i));
        }
        text.append(']');
    }

    private static String nullableText(JSONObject value, String key, String fallback) {
        if (value.isNull(key)) return fallback;
        String text = value.optString(key, "").trim();
        return text.isEmpty() ? fallback : text;
    }

    private static String time(double seconds) {
        long value = Double.isNaN(seconds) || Double.isInfinite(seconds) ? 0 : Math.max(0, (long) seconds);
        return value >= 3600 ? String.format(Locale.ROOT, "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
                : String.format(Locale.ROOT, "%02d:%02d", value / 60, value % 60);
    }

    private File meetingsDir() {
        return directory(new File(root, "meetings"));
    }

    private JSONArray checkedProfiles(JSONArray profiles) throws Exception {
        if (profiles == null || profiles.length() > 4) {
            throw new IOException("At most four speaker references are supported");
        }
        JSONArray clean = new JSONArray();
        Set<String> ids = new HashSet<>();
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject profile = profiles.getJSONObject(i);
            String id = profile.getString("id");
            String name = profile.getString("name").trim();
            File path = new File(profile.getString("path"));
            if (!id.matches("[A-Za-z0-9_-]{1,64}") || !ids.add(id)
                    || name.isEmpty() || name.length() > 80 || !isRecording(path)
                    || !path.getName().toLowerCase(Locale.ROOT).endsWith(".m4a")) {
                throw new IOException("Invalid speaker reference");
            }
            JSONObject value = new JSONObject();
            value.put("id", id);
            value.put("name", name);
            value.put("path", path.getCanonicalPath());
            clean.put(value);
        }
        return clean;
    }

    private boolean isRecording(File file) throws IOException {
        String parent = recordingsDir().getCanonicalPath() + File.separator;
        return file.getCanonicalPath().startsWith(parent) && !file.isDirectory();
    }

    private static boolean safeId(String id) {
        return id != null && id.matches("[A-Za-z0-9_-]{1,100}");
    }

    private static File directory(File directory) {
        if (!directory.isDirectory() && !directory.mkdirs() && !directory.isDirectory()) {
            throw storageError("تعذر إنشاء مجلد التخزين الخاص بالتطبيق.", null);
        }
        return directory;
    }

    private static void atomicWrite(File target, String value) throws IOException {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_JSON_BYTES) throw new IOException("Record exceeded limit");
        File temp = File.createTempFile(".pending-", ".tmp", target.getParentFile());
        try {
            try (FileOutputStream output = new FileOutputStream(temp)) {
                output.write(bytes);
                output.flush();
                output.getFD().sync();
            }
            Files.move(temp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING);
        } finally {
            if (temp.exists()) temp.delete();
        }
    }

    private static String read(File file) throws IOException {
        try (InputStream input = new FileInputStream(file)) {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (bytes.size() + count > MAX_JSON_BYTES) throw new IOException("Record exceeded limit");
                bytes.write(buffer, 0, count);
            }
            return new String(bytes.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private static IllegalStateException storageError(String message, Exception cause) {
        return new IllegalStateException(message, cause);
    }
}
