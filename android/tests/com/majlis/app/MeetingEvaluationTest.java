package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONObject;

/** Uses the real org.json implementation; no Android classes or provider credentials. */
public final class MeetingEvaluationTest {
    private static int passed;

    public static void main(String[] args) throws Exception {
        test("identity and immutable inputs", () -> {
            JSONArray baseline = sample("راجعنا خطة المشروع");
            JSONArray reviewed = sample("راجعنا خطة المشروع");
            String before = baseline.toString() + reviewed.toString();
            JSONObject score = MeetingEvaluation.compare(baseline, reviewed);
            equal(score.getInt("reference_words"), 3, "reference words");
            equal(score.getInt("heard_words"), 3, "heard words");
            equal(score.getInt("word_errors"), 0, "identity errors");
            close(score.getDouble("word_error_rate_percent"), 0, "identity WER");
            equal(score.getInt("changed_segments"), 0, "identity segments");
            check(before.equals(baseline.toString() + reviewed.toString()), "Inputs mutated");
        });
        test("insertions can produce WER above 100 percent", () -> {
            JSONObject score = MeetingEvaluation.compare(sample("واحد اثنان ثلاثة أربعة"), sample("واحد"));
            equal(score.getInt("word_errors"), 3, "insertion errors");
            close(score.getDouble("word_error_rate_percent"), 300, "raw WER");
        });
        test("deletion and substitution", () -> {
            JSONObject deletion = MeetingEvaluation.compare(sample("راجعنا"), sample("راجعنا خطة المشروع"));
            equal(deletion.getInt("word_errors"), 2, "deletion errors");
            close(deletion.getDouble("word_error_rate_percent"), 200.0 / 3.0, "deletion WER");
            JSONObject substitution = MeetingEvaluation.compare(sample("راجعنا خطة المشروع"), sample("راجعنا ميزانية المشروع"));
            equal(substitution.getInt("word_errors"), 1, "substitution errors");
            close(substitution.getDouble("word_error_rate_percent"), 100.0 / 3.0, "substitution WER");
        });
        test("Arabic normalization punctuation and case", () -> {
            JSONObject score = MeetingEvaluation.compare(sample("أَحْمَدُ، إِلَى الـمَكْتَبِ! API"), sample("احمد الي المكتب api"));
            equal(score.getInt("word_errors"), 0, "Arabic normalization errors");
            equal(score.getInt("reference_words"), 4, "normalized word count");
            equal(score.getInt("changed_segments"), 1, "raw editing still counted");
            equal(MeetingEvaluation.compare(sample("ا\u0654حمد"), sample("احمد")).getInt("word_errors"), 0, "decomposed alef");
        });
        test("empty hypothesis counts deletions and empty reference is rejected", () -> {
            JSONObject score = MeetingEvaluation.compare(sample("..."), sample("كلمة واحدة"));
            equal(score.getInt("heard_words"), 0, "empty heard words");
            equal(score.getInt("word_errors"), 2, "empty hypothesis errors");
            close(score.getDouble("word_error_rate_percent"), 100, "empty hypothesis WER");
            reject(() -> MeetingEvaluation.compare(sample("نص"), sample("ـ َ ، !")), "مرجعية");
            reject(() -> MeetingEvaluation.compare(new JSONArray(), new JSONArray()), "مرجعية");
        });
        test("speaker corrections use durations rather than number of segments", () -> {
            JSONArray baseline = new JSONArray().put(segment("one", "a", 0, 2, "الأول"))
                    .put(segment("two", "a", 2, 10, "الثاني"));
            JSONArray reviewed = new JSONArray().put(segment("one", "b", 0, 2, "الأول"))
                    .put(segment("two", "a", 2, 10, "الثاني"));
            JSONObject score = MeetingEvaluation.compare(baseline, reviewed);
            close(score.getDouble("speaker_changed_seconds"), 2, "corrected speaker seconds");
            close(score.getDouble("speech_seconds"), 10, "speech seconds");
            close(score.getDouble("speaker_correction_percent"), 20, "speaker percentage");
            equal(score.getInt("changed_segments"), 1, "changed segments");
            close(score.getDouble("word_error_rate_percent"), 0, "speaker edits do not change WER");
        });
        test("overlap contributes separate segment durations", () -> {
            JSONArray baseline = new JSONArray().put(segment("one", "a", 0, 4, "الأول"))
                    .put(segment("two", "a", 2, 6, "الثاني"));
            JSONArray reviewed = new JSONArray().put(segment("one", "b", 0, 4, "الأول"))
                    .put(segment("two", "a", 2, 6, "الثاني"));
            JSONObject score = MeetingEvaluation.compare(baseline, reviewed);
            close(score.getDouble("speech_seconds"), 8, "overlap denominator");
            close(score.getDouble("speaker_correction_percent"), 50, "fixed-segment ratio");
        });
        test("mismatched count order identity and timing are rejected", () -> {
            reject(() -> MeetingEvaluation.compare(sample("نص"), new JSONArray()), "المقاطع");
            reject(() -> MeetingEvaluation.compare(sample("نص"), new JSONArray().put(segment("other", "a", 0, 8, "نص"))), "معرّفات");
            JSONArray ordered = new JSONArray().put(segment("one", "a", 0, 2, "نص"))
                    .put(segment("two", "a", 2, 4, "نص"));
            JSONArray reversed = new JSONArray().put(segment("two", "a", 2, 4, "نص"))
                    .put(segment("one", "a", 0, 2, "نص"));
            reject(() -> MeetingEvaluation.compare(ordered, reversed), "معرّفات");
            JSONArray duplicate = new JSONArray().put(segment("one", "a", 0, 2, "نص"))
                    .put(segment("one", "a", 2, 4, "نص"));
            reject(() -> MeetingEvaluation.compare(duplicate, duplicate), "فريدة");
            reject(() -> MeetingEvaluation.compare(sample("نص"), new JSONArray().put(segment("one", "a", 0, 9, "نص"))), "أوقات");
        });
        test("invalid speaker text and times are rejected", () -> {
            JSONArray missingSpeaker = sample("نص");
            missingSpeaker.getJSONObject(0).remove("speaker_id");
            reject(() -> MeetingEvaluation.compare(sample("نص"), missingSpeaker), "المتحدث");
            JSONArray wrongText = sample("نص");
            wrongText.getJSONObject(0).put("text", 7);
            reject(() -> MeetingEvaluation.compare(sample("نص"), wrongText), "النص");
            JSONArray wrongTime = sample("نص");
            wrongTime.getJSONObject(0).put("start", "0");
            reject(() -> MeetingEvaluation.compare(wrongTime, wrongTime), "أوقات");
            JSONArray backwards = new JSONArray().put(segment("one", "a", 9, 8, "نص"));
            reject(() -> MeetingEvaluation.compare(backwards, backwards), "أوقات");
        });
        test("Chinese CER counts code points instead of pretending spaces define words", () -> {
            JSONObject score=MeetingEvaluation.compare(sample("你好世"),sample("你好界"));
            check(score.getBoolean("has_cjk"),"Chinese marker absent");
            equal(score.getInt("reference_characters"),3,"Chinese reference characters");
            equal(score.getInt("character_errors"),1,"Chinese substitution");
            close(score.getDouble("character_error_rate_percent"),100.0/3.0,"Chinese CER");
        });
        test("word budget accepts 6000 and rejects 6001 in either transcript", () -> {
            String sixThousand = words(6000);
            JSONObject score = MeetingEvaluation.compare(sample(sixThousand), sample(sixThousand));
            equal(score.getInt("reference_words"), 6000, "word budget boundary");
            equal(score.getInt("word_errors"), 0, "long identity");
            check(score.isNull("character_error_rate_percent"),"CER budget should be explicit");
            reject(() -> MeetingEvaluation.compare(sample(words(6001)), sample("نص")), "٦٠٠٠");
            reject(() -> MeetingEvaluation.compare(sample("نص"), sample(words(6001))), "٦٠٠٠");
        });
        System.out.println("Passed " + passed + " meeting evaluation checks.");
    }

    private static JSONArray sample(String text) throws Exception {
        return new JSONArray().put(segment("one", "a", 0, 8, text));
    }

    private static JSONObject segment(String id, String speaker, double start, double end, String text) throws Exception {
        return new JSONObject().put("id", id).put("speaker_id", speaker)
                .put("start", start).put("end", end).put("text", text);
    }

    private static String words(int count) {
        StringBuilder value = new StringBuilder();
        for (int i = 0; i < count; i++) value.append("نص ");
        return value.toString();
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

    private static void reject(Checked action, String part) throws Exception {
        try { action.run(); }
        catch (IllegalArgumentException expected) {
            check(expected.getMessage().contains(part), "Unexpected rejection: " + expected.getMessage());
            return;
        }
        throw new AssertionError("Expected rejection containing " + part);
    }

    private static void equal(int actual, int expected, String name) {
        check(actual == expected, name + ": " + actual + " != " + expected);
    }

    private static void close(double actual, double expected, String name) {
        check(Math.abs(actual - expected) < 0.000001, name + ": " + actual + " != " + expected);
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private interface Checked { void run() throws Exception; }
}
