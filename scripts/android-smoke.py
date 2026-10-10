#!/usr/bin/env python3
"""Exercise the installable APK through Android's accessibility hierarchy.

This uses a fresh emulator and public Arabic UI controls for native recording,
synthetic experiment references, and local text decoding. It does not contact
an analysis provider or measure physical microphone, transcription,
speaker-recognition, or translation quality.
"""

import argparse
import json
import math
import re
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


PACKAGE = "com.majlis.app"
COMPONENT = PACKAGE + "/.MainActivity"
DEMO_TITLE = "مثال تجريبي: إطلاق المنتج"
CORRECTED_TEXT = "reviewed_transcript_2026"
TEST_NOTES = "emulator_native_recorder_no_physical_microphone"


class Smoke:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.steps = []
        self.last_xml = None
        self.fixture_entries = {}
        self.fixtures = {}

    def adb(self, *args, binary=False, check=True):
        completed = subprocess.run(
            ["adb", *args], check=check, capture_output=True,
            text=not binary, timeout=45,
        )
        return completed.stdout

    def snapshot(self):
        for attempt in range(3):
            try:
                self.adb("shell", "rm", "-f", "/sdcard/majlis-smoke.xml")
                self.adb("shell", "uiautomator", "dump", "--compressed", "/sdcard/majlis-smoke.xml")
                raw = self.adb("shell", "cat", "/sdcard/majlis-smoke.xml")
                self.last_xml = ET.fromstring(raw)
                return self.last_xml
            except (ET.ParseError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise AssertionError("Android did not return a valid accessibility hierarchy")
                time.sleep(0.5)

    @staticmethod
    def bounds(node):
        numbers = [int(value) for value in re.findall(r"-?\d+", node.get("bounds", ""))]
        if len(numbers) != 4 or numbers[2] <= numbers[0] or numbers[3] <= numbers[1]:
            return None
        return numbers

    def scroll(self, direction, tree):
        for node in tree.iter("node"):
            if node.get("scrollable") == "true" and self.bounds(node):
                left, top, right, bottom = self.bounds(node)
                x = (left + right) // 2
                start = top + (bottom - top) * 4 // 5
                end = top + (bottom - top) // 5
                if direction == "up":
                    start, end = end, start
                self.adb("shell", "input", "swipe", str(x), str(start), str(x), str(end), "250")
                time.sleep(0.15)
                return True
        return False

    @staticmethod
    def signature(tree):
        return tuple((node.get("text"), node.get("bounds")) for node in tree.iter("node"))

    def find(self, predicate, description, scroll=True):
        tree = self.snapshot()
        for direction in (["up", "down"] if scroll else [None]):
            previous = None
            for _ in range(18 if scroll else 1):
                for node in tree.iter("node"):
                    if predicate(node) and node.get("enabled") == "true" and self.bounds(node):
                        return node
                signature = self.signature(tree)
                if direction is None or signature == previous or not self.scroll(direction, tree):
                    break
                previous = signature
                tree = self.snapshot()
        raise AssertionError("Missing accessible UI control/content: " + description)

    def text(self, value, partial=False, scroll=True):
        return self.find(
            lambda node: value in node.get("text", "") if partial else node.get("text") == value,
            value, scroll=scroll,
        )

    def tap_node(self, node):
        left, top, right, bottom = self.bounds(node)
        self.adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))
        time.sleep(0.3)

    def tap(self, text):
        self.tap_node(self.text(text))

    def paired_controls(self, first, second):
        """Get neighboring full-width controls from one idle hierarchy."""
        pair = []

        def neighboring(node):
            if node.get("text") != second or not self.bounds(node):
                return False
            first_node = next(
                (item for item in self.last_xml.iter("node")
                 if item.get("text") == first and item.get("enabled") == "true"
                 and self.bounds(item)), None,
            )
            if first_node is None:
                return False
            a, b = self.bounds(first_node), self.bounds(node)
            height = a[3] - a[1]
            if a[0] != b[0] or a[2] != b[2] or height != b[3] - b[1] or not 0 < b[1] - a[3] < height:
                return False
            pair[:] = [first_node, node]
            return True

        self.find(neighboring, first + " / " + second + " with complete neighboring bounds")
        return tuple(pair)

    def preferences(self):
        raw = self.adb("exec-out", "run-as", PACKAGE, "cat", "shared_prefs/majlis.xml")
        tree = ET.fromstring(raw)
        return {node.get("name"): node.get("value", node.text or "") for node in tree}

    def timer_seconds(self):
        node = self.find(
            lambda item: item.get("package") == PACKAGE
            and re.fullmatch(r"\d{2,}:\d{2}", item.get("text", "")) is not None,
            "native recording timer",
        )
        minutes, seconds = (int(part) for part in node.get("text").split(":"))
        return minutes * 60 + seconds

    def record(self, name):
        tree = self.snapshot()
        (self.output / (name + ".xml")).write_bytes(ET.tostring(tree, encoding="utf-8"))
        (self.output / (name + ".png")).write_bytes(self.adb("exec-out", "screencap", "-p", binary=True))
        if not self.adb("shell", "pidof", PACKAGE, check=False).strip():
            raise AssertionError("The app process stopped at " + name)
        self.steps.append(name)
        (self.output / "steps.json").write_text(json.dumps(self.steps, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS:", name, flush=True)

    def replace_edit_text(self, value=CORRECTED_TEXT, node=None):
        if node is None:
            node = self.find(lambda item: item.get("class") == "android.widget.EditText", "edit dialog text field", scroll=False)
        self.tap_node(node)
        # Android 15 sends Ctrl+A to the focused native EditText. ASCII test
        # content avoids dependency on an emulator keyboard's Arabic layout.
        self.adb("shell", "input", "keycombination", "113", "29")
        self.adb("shell", "input", "keyevent", "67")
        self.adb("shell", "input", "text", value)
        field_has_value = lambda item: item.get("class") == "android.widget.EditText" and item.get("text") == value
        self.find(field_has_value, "edited field value", scroll=False)
        input_method = self.adb("shell", "dumpsys", "input_method")
        if re.search(r"\b(?:mInputShown|mIsInputViewShown|isInputShown)=true\b", input_method):
            self.adb("shell", "input", "keyevent", "4")
        self.find(field_has_value, "edited field value after keyboard dismissal", scroll=False)

    def export_pdf(self):
        self.adb("shell", "rm", "-f", "/sdcard/Download/majlis-smoke.pdf")
        self.tap("PDF")
        self.replace_edit_text("majlis-smoke.pdf")
        save = lambda node: node.get("resource-id", "").endswith(":id/save") or node.get("text", "").casefold() == "save"
        try:
            save_node = self.find(save, "Android save document control", scroll=False)
        except AssertionError:
            # A fresh DocumentsUI may open in Recent, where saving requires a
            # writable root. Select Downloads through its accessible drawer.
            drawer = self.find(
                lambda node: "show roots" in node.get("content-desc", "").casefold()
                or node.get("resource-id") == "android:id/home",
                "Android document picker navigation", scroll=False,
            )
            self.tap_node(drawer)
            self.tap("Downloads")
            save_node = self.find(save, "Android save document control", scroll=False)
        self.tap_node(save_node)
        pdf = b""
        for _ in range(20):
            pdf = self.adb("exec-out", "cat", "/sdcard/Download/majlis-smoke.pdf", binary=True, check=False)
            if pdf.startswith(b"%PDF-") and b"%%EOF" in pdf[-1024:]:
                break
            time.sleep(0.5)
        if not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-1024:] or len(pdf) < 500:
            raise AssertionError("The native document flow did not create a complete PDF in Downloads")
        (self.output / "majlis-smoke.pdf").write_bytes(pdf)
        self.record("13-exported-pdf")

    def native_recording(self):
        self.tap("أعلمت المشاركين بالتسجيل وحصلت على موافقتهم")
        start, future_pause = self.paired_controls("بدء التسجيل", "استيراد ملف صوتي")
        self.record("02-recording-controls-ready")
        # MainActivity inserts an identically sized pause row directly after
        # the record button. Its first position is the cached import row's
        # bounds. Avoid waitForIdle while the recording timer changes every
        # 500 ms; all subsequent controls come from a paused hierarchy.
        self.tap_node(start)
        for _ in range(20):
            if self.preferences().get("recording_incomplete") == "true":
                break
            time.sleep(0.25)
        else:
            raise AssertionError("The native recording did not start")
        time.sleep(3)
        service = self.adb("shell", "dumpsys", "activity", "services", PACKAGE)
        (self.output / "recording-service.txt").write_text(service, encoding="utf-8")
        if "RecordingService" not in service or "isForeground=true" not in service:
            raise AssertionError("The microphone recorder is not running as a foreground service")
        (self.output / "recording-active.png").write_bytes(
            self.adb("exec-out", "screencap", "-p", binary=True)
        )
        self.tap_node(future_pause)
        self.text("التسجيل متوقف مؤقتًا • الوقت ثابت")
        paused_seconds = self.timer_seconds()
        if paused_seconds < 2:
            raise AssertionError("The native recording timer did not advance before pausing")
        self.record("03-recording-paused")
        time.sleep(2.2)
        if self.timer_seconds() != paused_seconds:
            raise AssertionError("Paused recording time advanced")
        self.record("04-recording-pause-time-stable")

        _, resume = self.paired_controls("إنهاء التسجيل", "متابعة التسجيل")
        self.tap_node(resume)
        time.sleep(2.5)
        self.tap_node(resume)
        self.text("التسجيل متوقف مؤقتًا • الوقت ثابت")
        resumed_seconds = self.timer_seconds()
        if resumed_seconds <= paused_seconds:
            raise AssertionError("The native recording timer did not advance after resuming")
        self.record("05-recording-resumed-and-paused")
        stop, _ = self.paired_controls("إنهاء التسجيل", "متابعة التسجيل")
        self.tap_node(stop)
        self.text("التسجيل محفوظ على الجهاز • جاهز للتحليل")
        prefs = self.preferences()
        if prefs.get("recording_incomplete") != "false":
            raise AssertionError("The recording was not finalized")
        path = prefs.get("last_audio", "")
        if not re.fullmatch(r"/data/(?:user/0|data)/com\.majlis\.app/files/recordings/meeting_\d+\.m4a", path):
            raise AssertionError("The recording was not saved in app-private storage")
        audio = self.adb("exec-out", "run-as", PACKAGE, "cat", path, binary=True)
        if len(audio) < 500:
            raise AssertionError("The finalized recording is unexpectedly small")
        recording = self.output / "native-recording.m4a"
        recording.write_bytes(audio)
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
             "-show_format", "-of", "json", str(recording)],
            check=False, capture_output=True, text=True, timeout=30,
        )
        (self.output / "native-recording-ffprobe.json").write_text(result.stdout, encoding="utf-8")
        (self.output / "native-recording-ffprobe-errors.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise AssertionError("FFprobe could not read the native recording; see native-recording-ffprobe-errors.txt")
        probe = json.loads(result.stdout)
        streams = probe.get("streams", [])
        if (len(streams) != 1 or streams[0].get("codec_name") != "aac"
                or streams[0].get("channels") != 1 or streams[0].get("sample_rate") != "32000"):
            raise AssertionError("The native recorder did not produce 32 kHz mono AAC")
        container = probe.get("format", {})
        if "m4a" not in container.get("format_name", "").split(","):
            raise AssertionError("The native recorder did not produce an M4A container")
        duration = float(container.get("duration", "0"))
        if not math.isfinite(duration) or duration < 3 or abs(duration - resumed_seconds) > 2:
            raise AssertionError("Saved AAC duration differs from the active recording timer")
        (self.output / "native-recording-result.json").write_text(json.dumps({
            "paused_timer_seconds": paused_seconds,
            "resumed_timer_seconds": resumed_seconds,
            "encoded_duration_seconds": duration,
            "bytes": len(audio),
            "physical_microphone_quality_measured": False,
        }, indent=2), encoding="utf-8")
        self.record("06-native-aac-saved")

    def translation_without_service(self):
        self.tap("ترجمة اللقاء")
        self.text("لغة الترجمة", scroll=False)
        for language in ("الإنجليزية", "الفرنسية", "الألمانية", "الإسبانية", "التركية"):
            self.text(language, scroll=False)
        self.record("14-translation-language-chooser")
        self.tap("الإنجليزية")
        self.text("ترجمة اللقاء · en")
        self.text("إنشاء الترجمة")
        self.record("15-translation-page")
        prefs = self.preferences()
        if prefs.get("server_url") or prefs.get("access_token"):
            raise AssertionError("This smoke must run without a configured analysis service")
        self.tap("إنشاء الترجمة")
        self.text("اضبط رابط الخدمة ورمز الوصول في الإعدادات أولًا.", scroll=False)
        self.record("16-translation-needs-service-without-upload")
        self.tap("حسنًا")
        self.tap("المحضر والملخص")
        self.text(DEMO_TITLE)

    def documented_test_mode(self):
        self.tap("تجربة قياس موثقة")
        self.text("تفعيل الاختبار", scroll=False)
        self.tap("تفعيل الاختبار")
        # An empty note must keep the dialog open. No distance, participant
        # identity, or independent accuracy result can be inferred here.
        self.text("تفعيل الاختبار", scroll=False)
        self.find(lambda item: item.get("class") == "android.widget.EditText", "test conditions", scroll=False)
        if self.preferences().get("test_mode") == "true":
            raise AssertionError("An empty conditions note activated test mode")
        self.record("19-test-conditions-required")
        self.replace_edit_text(TEST_NOTES)
        self.tap("تفعيل الاختبار")
        self.text("وضع الاختبار: " + TEST_NOTES)
        self.record("20-documented-test-active")
        self.tap("إلغاء وضع الاختبار")
        self.text("تجربة قياس موثقة")
        prefs = self.preferences()
        if prefs.get("test_mode") == "true" or prefs.get("test_notes"):
            raise AssertionError("Canceling test mode did not remove its saved conditions")
        self.record("21-documented-test-canceled")

    def load_fixtures(self, apk):
        # Read the exact reference shipped in the APK under test, avoiding a
        # dependency on an independently edited checkout or generated audio.
        with zipfile.ZipFile(apk) as archive:
            index = json.loads(archive.read("assets/fixtures/index.json"))
            if len(index.get("fixtures", [])) != 4:
                raise AssertionError("The APK does not contain the four experiment references")
            for entry in index["fixtures"]:
                asset = entry["asset"]
                if not re.fullmatch(r"fixtures/[a-z0-9-]+\.json", asset):
                    raise AssertionError("Unexpected fixture asset path")
                reference = json.loads(archive.read("assets/" + asset))
                if reference.get("_fixture", {}).get("id") != entry["id"]:
                    raise AssertionError("Fixture index/reference identity mismatch")
                self.fixture_entries[entry["id"]] = entry
                self.fixtures[entry["id"]] = reference

    def button_below_title(self, title, button, titles):
        """Select a repeated card button by its preceding accessible title."""
        def belongs_to_title(node):
            if node.get("text") != button:
                return False
            nearest_title = None
            for item in self.last_xml.iter("node"):
                if item is node:
                    break
                if item.get("text") in titles:
                    nearest_title = item.get("text")
            return nearest_title == title

        return self.find(belongs_to_title, button + " for " + title)

    def open_fixture(self, fixture_id):
        self.tap("الاجتماع")
        self.tap("مختبر المتحدثين واللغات والضوضاء")
        self.text("مختبر قابل لإعادة التجربة")
        title = self.fixture_entries[fixture_id]["title"]
        titles = {entry["title"] for entry in self.fixture_entries.values()}
        self.tap_node(self.button_below_title(title, "فتح مرجع التجربة", titles))
        self.text(title)
        self.text(self.fixtures[fixture_id]["_fixture"]["notice_ar"])

    @staticmethod
    def formatted_time(seconds):
        value = int(seconds)
        return f"{value // 60:02d}:{value % 60:02d}"

    def fixture_roster(self):
        self.open_fixture("six-speakers-distance")
        self.record("23-synthetic-reference-opened")
        fixture = self.fixtures["six-speakers-distance"]
        segments = fixture["segments"]
        speakers = {speaker["id"]: speaker["name"] for speaker in fixture["speakers"]}
        expected_roster = fixture["_fixture"]["expected_roster"]
        if len(speakers) != 6 or len(segments) != 13:
            raise AssertionError("The roster scenario needs six speakers and thirteen reference turns")
        self.tap("محاكاة ظهور المتحدثين وعودتهم")
        self.text("محاكاة قائمة المتحدثين", scroll=False)
        self.text("لم يبدأ أحد الكلام بعد")
        self.text("القائمة: 0 / 6 متحدثين", partial=True)
        observed = []
        for count in range(1, 14):
            self.tap("المقطع التالي")
            segment = segments[count - 1]
            expected = next(item for item in expected_roster if item["speaker_id"] == segment["speaker_id"])
            first_appearance = segment["id"] == expected["first_segment_id"]
            event = ("إضافة متحدث إلى القائمة: " if first_appearance else "عودة متحدث مسجل: ")
            event += speakers[segment["speaker_id"]] + " · " + self.formatted_time(segment["start"])
            event += f" · {count}/{len(segments)}"
            self.text(event)
            seen = {item["speaker_id"] for item in segments[:count]}
            roster = self.text(f"القائمة: {len(seen)} / 6 متحدثين", partial=True).get("text")
            expected_lines = [f"القائمة: {len(seen)} / 6 متحدثين"]
            for entry in expected_roster:
                if entry["speaker_id"] not in seen:
                    continue
                turns = sum(item["speaker_id"] == entry["speaker_id"] for item in segments[:count])
                expected_lines.append(
                    speakers[entry["speaker_id"]] + " · أول كلام "
                    + self.formatted_time(entry["first_seen_seconds"]) + f" · مرات الكلام {turns}"
                )
            if roster.splitlines() != expected_lines:
                raise AssertionError("The simulated roster duplicated, renamed, or lost a returning identity")
            observed.append({
                "segment_id": segment["id"], "speaker_id": segment["speaker_id"],
                "first_appearance": first_appearance, "roster_count": len(seen), "visible_roster": roster,
            })
            if count in (1, 4, 5, 10, 12, 13):
                self.record(f"24-roster-segment-{count:02d}")
        if observed[9]["roster_count"] != 6 or observed[11]["first_appearance"]:
            raise AssertionError("The fixture did not demonstrate six identities and the late identity returning")
        if {item["speaker_id"] for item in observed if not item["first_appearance"]} != set(speakers):
            raise AssertionError("The fixture did not demonstrate all six speaker identities returning")
        (self.output / "synthetic-roster-results.json").write_text(json.dumps({
            "fixture_id": fixture["_fixture"]["id"], "source": "packaged_synthetic_reference",
            "provider_recognition_measured": False, "steps": observed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.tap("إعادة ضبط القائمة")
        self.text("لم يبدأ أحد الكلام بعد")
        self.text("القائمة: 0 / 6 متحدثين", partial=True)
        self.record("25-roster-reset")
        self.tap("إغلاق")

    def filter_transcript(self, query):
        field = self.find(
            lambda item: item.get("class") == "android.widget.EditText"
            and item.get("package") == PACKAGE,
            "transcript search field",
        )
        self.replace_edit_text(query, node=field)

    def saved_fixture(self, fixture_id):
        names = self.adb("exec-out", "run-as", PACKAGE, "ls", "files/meetings").splitlines()
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", name):
                continue
            meeting = json.loads(self.adb("exec-out", "run-as", PACKAGE, "cat", "files/meetings/" + name))
            if meeting.get("_fixture", {}).get("id") == fixture_id:
                return meeting
        raise AssertionError("The fixture edits were not saved in app-private history")

    def fixture_decoders(self):
        fixture_id = "unknown-and-ciphers"
        self.open_fixture(fixture_id)
        fixture = self.fixtures[fixture_id]
        examples = fixture["_fixture"]["encoding_examples"]
        known = next(item for item in examples if item["method"] == "base64")
        unknown = next(item for item in examples if item["method"] == "unknown")
        if known["expected_plaintext"] != "راجع التقرير غدًا" or unknown["expected_plaintext"] is not None:
            raise AssertionError("The decoder fixture lost its explicit known/unknown contract")

        self.filter_transcript(known["input"])
        self.text(known["input"])
        # Save through the native UI first, so both later decoder operations
        # must commit to history and survive activity/process recreation.
        self.tap("حفظ المثال التجريبي")
        self.tap("فك ترميز النص أو توثيق عدم فهمه")
        self.text("فك وتوثيق النص", scroll=False)
        self.text("Base64 — ترميز")
        self.text(known["input"])
        self.tap("فك وتوثيق")
        self.text("الناتج: " + known["expected_plaintext"], partial=True)
        self.record("26-local-base64-decoded")

        # Keep the original Unicode source in the native dialog. Search by a
        # unique ASCII token because adb input text has no Unicode IME support.
        self.filter_transcript("7QX9")
        self.text(unknown["input"])
        self.tap("فك ترميز النص أو توثيق عدم فهمه")
        self.text("فك وتوثيق النص", scroll=False)
        self.text(unknown["input"])
        self.tap("Base64 — ترميز")
        self.tap("غير معروف أو مفتاحه غير متوفر")
        self.tap("فك وتوثيق")
        self.text("لم يمكن فك النص؛ الأصل محفوظ ولا يوجد إثبات آلي للتشفير", partial=True)
        self.text("الأصل: " + unknown["input"], partial=True)
        self.record("27-unknown-source-preserved")

        saved = self.saved_fixture(fixture_id)
        if saved["segments"] != fixture["segments"] or saved["speakers"] != fixture["speakers"]:
            raise AssertionError("Local decoding changed the source transcript or speaker identities")
        notes = saved.get("_code_notes", {})
        for example in (known, unknown):
            source = next(item for item in fixture["segments"] if item["id"] == example["segment_id"])
            note = notes.get(source["id"], {})
            for field in ("speaker_id", "start", "end"):
                if note.get(field) != source[field]:
                    raise AssertionError("A decoder note lost its source speaker/timing: " + field)
            if (note.get("segment_id") != source["id"] or note.get("segment_text") != source["text"]
                    or note.get("encoded_text") != example["input"] or note.get("scheme") != example["method"]
                    or note.get("reviewed_by_user") is not True or not note.get("reason")):
                raise AssertionError("A decoder note lost its original text or explicit user choice")
            if example["method"] == "base64":
                if note.get("status") != "decoded" or note.get("decoded_text") != example["expected_plaintext"]:
                    raise AssertionError("The local Base64 result did not match the known reference")
            elif note.get("status") != "unresolved" or "decoded_text" in note:
                raise AssertionError("The unknown source received an invented decoded result")
        (self.output / "fixture-decoder-persisted.json").write_text(
            json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "am", "start", "-W", "-n", COMPONENT)
        self.tap("المحاضر")
        titles = {DEMO_TITLE, fixture["title"]}
        self.tap_node(self.button_below_title(fixture["title"], "فتح المحضر", titles))
        self.tap("النص الكامل")
        self.filter_transcript(known["input"])
        self.text("الناتج: " + known["expected_plaintext"], partial=True)
        self.record("28-decoded-note-after-restart")
        self.filter_transcript("7QX9")
        self.text("لم يمكن فك النص؛ الأصل محفوظ ولا يوجد إثبات آلي للتشفير", partial=True)
        self.text("الأصل: " + unknown["input"], partial=True)
        self.record("29-unknown-note-after-restart")

    def run(self, apk):
        self.load_fixtures(apk)
        self.adb("wait-for-device")
        self.adb("install", "-r", "-g", str(apk))
        self.adb("shell", "pm", "clear", PACKAGE)
        self.adb("shell", "pm", "grant", PACKAGE, "android.permission.RECORD_AUDIO")
        self.adb("shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS")
        self.adb("shell", "settings", "put", "secure", "show_ime_with_hard_keyboard", "1")
        self.adb("logcat", "-c")
        self.adb("shell", "am", "start", "-W", "-n", COMPONENT)
        self.text("اجتماع جديد")
        self.record("01-meeting")

        self.native_recording()

        self.tap("تجربة اجتماع بمحتوى تجريبي")
        self.text(DEMO_TITLE)
        self.record("07-demo-minutes")
        self.tap("النص الكامل")
        self.text("نراجع اليوم جاهزية إطلاق المنتج. نحتاج إلى اختبار النسخة قبل موعد الإطلاق.")
        self.record("08-transcript")
        self.tap("تعديل النص والمتحدث")
        self.replace_edit_text()
        self.tap("حفظ التعديل")
        self.text("النص تغيّر؛ المحضر يحتاج إعادة تلخيص ومراجعة.")
        self.text(CORRECTED_TEXT)
        self.record("09-corrected-transcript")

        self.tap("حفظ المثال التجريبي")
        self.tap("المحاضر")
        self.text(DEMO_TITLE)
        self.record("10-history-saved")

        # Native activity/process recreation proves the edit was persisted,
        # rather than merely displayed in the current activity instance.
        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "am", "start", "-W", "-n", COMPONENT)
        self.tap("المحاضر")
        self.tap("فتح المحضر")
        self.tap("النص الكامل")
        self.text(CORRECTED_TEXT)
        self.record("11-correction-after-restart")

        self.tap("المحضر والملخص")
        self.tap("تصدير المحضر")
        self.text("PDF", partial=True, scroll=False)
        self.text("JSON", partial=True, scroll=False)
        self.text("نص", scroll=False)
        self.record("12-export-options")
        self.export_pdf()
        self.translation_without_service()

        self.tap("الأصوات")
        self.record("17-voice-profiles")
        self.tap("الإعدادات")
        self.text("خدمة التحليل")
        self.text("اختبار الاتصال")
        self.record("18-settings")
        self.tap("الاجتماع")
        self.text("اجتماع جديد")
        self.documented_test_mode()
        self.record("22-return-to-meeting")
        self.fixture_roster()
        self.fixture_decoders()
        self.tap("الاجتماع")
        self.text("اجتماع جديد")
        self.record("30-return-after-fixture-experiments")

    def logs(self):
        full = self.adb("logcat", "-d", "-v", "threadtime")
        crash = self.adb("logcat", "-d", "-b", "crash", "-v", "brief")
        (self.output / "logcat.txt").write_text(full, encoding="utf-8")
        (self.output / "crash-log.txt").write_text(crash, encoding="utf-8")
        if "com.majlis.app" in crash:
            raise AssertionError("Android reported an app crash; see crash-log.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--output", default="dist/android-smoke")
    args = parser.parse_args()
    if not args.apk.is_file():
        raise SystemExit("APK not found: " + str(args.apk))
    smoke = Smoke(args.output)
    try:
        smoke.run(args.apk)
    except Exception:
        try:
            smoke.record("failure-ui")
        except Exception:
            pass
        raise
    finally:
        smoke.logs()
    print("Android UI, native AAC recording, synthetic roster and local decoder smoke passed; live analysis and physical microphone/AI quality were not exercised.")


if __name__ == "__main__":
    main()
