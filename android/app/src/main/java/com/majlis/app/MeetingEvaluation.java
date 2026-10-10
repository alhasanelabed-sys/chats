package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Compares an original transcript with a listener-reviewed reference, without uploading either. */
public final class MeetingEvaluation {
    private static final int MAX_WORDS = 6_000;
    private static final int MAX_SEGMENTS = 20_000;
    private static final int MAX_TEXT_CHARACTERS = 1_000_000;
    private static final Pattern ARABIC_MARKS = Pattern.compile(
            "[\\u0610-\\u061A\\u064B-\\u065F\\u0670\\u06D6-\\u06ED\\u0640]");
    private static final Pattern ALEF_VARIANTS = Pattern.compile("[\\u0622\\u0623\\u0625\\u0671]");
    private static final Pattern WORD = Pattern.compile("[\\p{L}\\p{N}]+");

    private MeetingEvaluation() { }

    /**
     * WER = word edit distance / words in the human-reviewed reference; it can exceed 100%.
     * Speaker correction measures the sum of durations of relabelled, fixed segments.
     * It is not DER: timing changes, missing speech and overlap are not independently scored;
     * overlapping segment durations contribute separately to both duration totals.
     */
    public static JSONObject compare(JSONArray baseline, JSONArray reviewed) throws JSONException {
        if (baseline == null || reviewed == null || baseline.length() != reviewed.length()
                || baseline.length() > MAX_SEGMENTS) {
            throw invalid("يجب تقييم نسختين لهما المقاطع نفسها بالترتيب نفسه.");
        }
        Set<String> ids = new HashSet<>();
        List<String> heardWords = new ArrayList<>();
        List<String> referenceWords = new ArrayList<>();
        long textCharacters = 0L;
        double speechSeconds = 0.0;
        double changedSpeakerSeconds = 0.0;
        int changedSegments = 0;
        boolean hasCjk=false;

        for (int i = 0; i < baseline.length(); i++) {
            JSONObject original = baseline.optJSONObject(i);
            JSONObject reference = reviewed.optJSONObject(i);
            if (original == null || reference == null) {
                throw invalid("بيانات أحد مقاطع التقييم غير صالحة.");
            }
            String id = requiredString(original, "id", false);
            if (!ids.add(id) || !id.equals(requiredString(reference, "id", false))) {
                throw invalid("معرّفات المقاطع يجب أن تكون فريدة ومتطابقة بالترتيب نفسه.");
            }
            double start = seconds(original, "start");
            double end = seconds(original, "end");
            double reviewedStart = seconds(reference, "start");
            double reviewedEnd = seconds(reference, "end");
            if (end < start || reviewedEnd < reviewedStart
                    || start != reviewedStart || end != reviewedEnd) {
                throw invalid("يجب أن تبقى أوقات المقاطع ثابتة بين النسختين للتقييم.");
            }
            String originalSpeaker = requiredString(original, "speaker_id", false);
            String reviewedSpeaker = requiredString(reference, "speaker_id", false);
            String originalText = requiredString(original, "text", true);
            String reviewedText = requiredString(reference, "text", true);
            hasCjk=hasCjk||Pattern.compile("[\\u3400-\\u9FFF]").matcher(reviewedText).find();
            textCharacters += originalText.length() + (long) reviewedText.length();
            if (textCharacters > MAX_TEXT_CHARACTERS) {
                throw invalid("نص التقييم كبير جدًا؛ قيّم عينة أقصر.");
            }
            appendWords(heardWords, originalText);
            appendWords(referenceWords, reviewedText);
            double duration = end - start;
            speechSeconds += duration;
            boolean speakerChanged = !originalSpeaker.equals(reviewedSpeaker);
            if (speakerChanged) changedSpeakerSeconds += duration;
            if (speakerChanged || !originalText.equals(reviewedText)) changedSegments++;
        }
        if (referenceWords.isEmpty()) {
            throw invalid("لا توجد كلمات مرجعية بعد التطبيع لحساب معدل خطأ الكلمات؛ راجع عينة تحتوي على كلام.");
        }
        if (Double.isInfinite(speechSeconds) || Double.isInfinite(changedSpeakerSeconds)) {
            throw invalid("مجموع مدد المقاطع غير صالح للتقييم.");
        }
        int errors = wordDistance(referenceWords, heardWords);
        JSONObject score=new JSONObject()
                .put("reference_words", referenceWords.size())
                .put("heard_words", heardWords.size())
                .put("word_errors", errors)
                .put("word_error_rate_percent", errors * 100.0 / referenceWords.size())
                .put("speaker_changed_seconds", changedSpeakerSeconds)
                .put("speech_seconds", speechSeconds)
                .put("speaker_correction_percent", speechSeconds > 0.0
                        ? changedSpeakerSeconds / speechSeconds * 100.0 : 0.0)
                .put("changed_segments", changedSegments);
        List<String> referenceCharacters=characters(referenceWords),heardCharacters=characters(heardWords);
        score.put("has_cjk",hasCjk);
        if(referenceCharacters!=null&&heardCharacters!=null&&!referenceCharacters.isEmpty()){
            int characterErrors=wordDistance(referenceCharacters,heardCharacters);
            score.put("reference_characters",referenceCharacters.size()).put("character_errors",characterErrors)
                    .put("character_error_rate_percent",100.0*characterErrors/referenceCharacters.size());
        }else score.put("character_error_rate_percent",JSONObject.NULL);
        return score;
    }

    private static void appendWords(List<String> target, String text) {
        String normalized = Normalizer.normalize(text, Normalizer.Form.NFKC)
                .toLowerCase(Locale.ROOT);
        normalized = ARABIC_MARKS.matcher(normalized).replaceAll("");
        normalized = ALEF_VARIANTS.matcher(normalized).replaceAll("ا").replace('\u0649', '\u064a');
        Matcher words = WORD.matcher(normalized);
        while (words.find()) {
            if (target.size() >= MAX_WORDS) {
                throw invalid("يدعم التقييم حتى ٦٠٠٠ كلمة في كل نسخة؛ قيّم عينة أقصر.");
            }
            target.add(words.group());
        }
    }

    /** CER uses the same normalized letters/digits, excluding separators; bounded to 6000 code points. */
    private static List<String> characters(List<String> words){
        List<String> result=new ArrayList<>();
        for(String word:words)for(int offset=0;offset<word.length();){
            int code=word.codePointAt(offset);offset+=Character.charCount(code);
            if(result.size()>=MAX_WORDS)return null;result.add(new String(Character.toChars(code)));
        }
        return result;
    }

    /** Two rows and the shorter transcript as columns: O(min(words)) memory, <=36M cells. */
    private static int wordDistance(List<String> reference, List<String> heard) {
        List<String> rows = reference.size() >= heard.size() ? reference : heard;
        List<String> columns = reference.size() >= heard.size() ? heard : reference;
        int[] previous = new int[columns.size() + 1];
        int[] current = new int[columns.size() + 1];
        for (int column = 0; column <= columns.size(); column++) previous[column] = column;
        for (int row = 1; row <= rows.size(); row++) {
            current[0] = row;
            for (int column = 1; column <= columns.size(); column++) {
                int replacement = previous[column - 1]
                        + (rows.get(row - 1).equals(columns.get(column - 1)) ? 0 : 1);
                current[column] = Math.min(replacement,
                        Math.min(previous[column] + 1, current[column - 1] + 1));
            }
            int[] swap = previous;
            previous = current;
            current = swap;
        }
        return previous[columns.size()];
    }

    private static String requiredString(JSONObject object, String key, boolean emptyAllowed) {
        Object value = object.opt(key);
        if (!(value instanceof String) || (!emptyAllowed && ((String) value).trim().isEmpty())) {
            throw invalid("بيانات المقاطع غير صالحة؛ يلزم النص ومعرّف المقطع والمتحدث.");
        }
        return (String) value;
    }

    private static double seconds(JSONObject object, String key) {
        Object value = object.opt(key);
        if (!(value instanceof Number)) throw invalid("أوقات المقاطع يجب أن تكون أرقامًا صالحة.");
        double number = ((Number) value).doubleValue();
        if (Double.isNaN(number) || Double.isInfinite(number) || number < 0.0) {
            throw invalid("أوقات المقاطع يجب أن تكون أرقامًا صالحة.");
        }
        return number;
    }

    private static IllegalArgumentException invalid(String message) {
        return new IllegalArgumentException(message);
    }
}
