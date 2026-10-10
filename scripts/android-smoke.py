#!/usr/bin/env python3
"""Exercise the installable APK through Android's accessibility hierarchy.

This uses a fresh emulator and public Arabic UI controls. It does not contact
an analysis provider or measure microphone/speaker-recognition quality.
"""

import argparse
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path


PACKAGE = "com.majlis.app"
COMPONENT = PACKAGE + "/.MainActivity"
DEMO_TITLE = "مثال تجريبي: إطلاق المنتج"
CORRECTED_TEXT = "reviewed_transcript_2026"


class Smoke:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.steps = []
        self.last_xml = None

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
            except (ET.ParseError, subprocess.CalledProcessError):
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

    def record(self, name):
        tree = self.snapshot()
        (self.output / (name + ".xml")).write_bytes(ET.tostring(tree, encoding="utf-8"))
        (self.output / (name + ".png")).write_bytes(self.adb("exec-out", "screencap", "-p", binary=True))
        if not self.adb("shell", "pidof", PACKAGE, check=False).strip():
            raise AssertionError("The app process stopped at " + name)
        self.steps.append(name)
        (self.output / "steps.json").write_text(json.dumps(self.steps, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS:", name, flush=True)

    def replace_edit_text(self, value=CORRECTED_TEXT):
        node = self.find(lambda item: item.get("class") == "android.widget.EditText", "edit dialog text field", scroll=False)
        self.tap_node(node)
        # Android 15 sends Ctrl+A to the focused native EditText. ASCII test
        # content avoids dependency on an emulator keyboard's Arabic layout.
        self.adb("shell", "input", "keycombination", "113", "29")
        self.adb("shell", "input", "keyevent", "67")
        self.adb("shell", "input", "text", value)
        self.text(value, scroll=False)
        input_method = self.adb("shell", "dumpsys", "input_method")
        if re.search(r"\b(?:mInputShown|mIsInputViewShown|isInputShown)=true\b", input_method):
            self.adb("shell", "input", "keyevent", "4")
        self.text(value, scroll=False)

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
        self.record("08-exported-pdf")

    def run(self, apk):
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

        self.tap("تجربة اجتماع بمحتوى تجريبي")
        self.text(DEMO_TITLE)
        self.record("02-demo-minutes")
        self.tap("النص الكامل")
        self.text("نراجع اليوم جاهزية إطلاق المنتج. نحتاج إلى اختبار النسخة قبل موعد الإطلاق.")
        self.record("03-transcript")
        self.tap("تعديل النص والمتحدث")
        self.replace_edit_text()
        self.tap("حفظ التعديل")
        self.text("النص تغيّر؛ المحضر يحتاج إعادة تلخيص ومراجعة.")
        self.text(CORRECTED_TEXT)
        self.record("04-corrected-transcript")

        self.tap("حفظ المثال التجريبي")
        self.tap("المحاضر")
        self.text(DEMO_TITLE)
        self.record("05-history-saved")

        # Native activity/process recreation proves the edit was persisted,
        # rather than merely displayed in the current activity instance.
        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "am", "start", "-W", "-n", COMPONENT)
        self.tap("المحاضر")
        self.tap("فتح المحضر")
        self.tap("النص الكامل")
        self.text(CORRECTED_TEXT)
        self.record("06-correction-after-restart")

        self.tap("المحضر والملخص")
        self.tap("تصدير المحضر")
        self.text("PDF", partial=True, scroll=False)
        self.text("JSON", partial=True, scroll=False)
        self.text("نص", scroll=False)
        self.record("07-export-options")
        self.export_pdf()

        self.tap("الأصوات")
        self.record("09-voice-profiles")
        self.tap("الإعدادات")
        self.text("خدمة التحليل")
        self.text("اختبار الاتصال")
        self.record("10-settings")
        self.tap("الاجتماع")
        self.text("اجتماع جديد")
        self.record("11-return-to-meeting")

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
    print("Android UI smoke passed; cloud analysis and audio quality were not exercised.")


if __name__ == "__main__":
    main()
