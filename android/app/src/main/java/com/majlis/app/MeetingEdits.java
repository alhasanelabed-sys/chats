package com.majlis.app;

import org.json.JSONArray;
import org.json.JSONObject;

/** Applies local corrections while preserving timestamps and evidence references. */
public final class MeetingEdits {
    private MeetingEdits() { }

    public static JSONObject applySegmentEdit(JSONObject meeting, String segmentId,
                                               String text, String speakerId) throws Exception {
        if (meeting == null) throw new Exception("محضر الاجتماع غير موجود.");
        String corrected = text == null ? "" : text.trim();
        if (corrected.isEmpty() || corrected.length() > 20_000) {
            throw new Exception("أدخل نصًا للمقطع بين حرف واحد و20 ألف حرف.");
        }
        JSONObject copy = new JSONObject(meeting.toString());
        MeetingApi.validateResult(copy);
        JSONArray speakers = copy.getJSONArray("speakers");
        boolean validSpeaker = false;
        for (int i = 0; i < speakers.length(); i++) {
            if (speakers.getJSONObject(i).getString("id").equals(speakerId)) validSpeaker = true;
        }
        if (!validSpeaker) throw new Exception("اختر متحدثًا موجودًا في هذا الاجتماع.");
        JSONArray segments = copy.getJSONArray("segments");
        for (int i = 0; i < segments.length(); i++) {
            JSONObject segment = segments.getJSONObject(i);
            if (segment.getString("id").equals(segmentId)) {
                segment.put("text", corrected);
                segment.put("speaker_id", speakerId);
                copy.put("_minutes_stale", true);
                return copy;
            }
        }
        throw new Exception("تعذر العثور على المقطع المطلوب تعديله.");
    }
}
