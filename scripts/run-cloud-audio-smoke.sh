#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-"$ROOT_DIR/tools/android-sdk"}}"
BUILD_TOOLS_DIR="$SDK_DIR/build-tools/35.0.0"
PLATFORM_JAR="$SDK_DIR/platforms/android-35/android.jar"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/majlis-cloud-audio.XXXXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/classes" "$TEST_DIR/dex" "$ROOT_DIR/dist/android-smoke"
cat > "$TEST_DIR/AndroidManifest.xml" <<'XML'
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.majlis.tests">
  <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/>
  <application android:label="Majlis codec test" android:debuggable="true"/>
  <instrumentation android:name="com.majlis.tests.CloudAudioSmoke" android:targetPackage="com.majlis.app"/>
</manifest>
XML
"$BUILD_TOOLS_DIR/aapt2" link -o "$TEST_DIR/test.apk" -I "$PLATFORM_JAR" --manifest "$TEST_DIR/AndroidManifest.xml"
java com.sun.tools.javac.Main --release 8 -encoding UTF-8 -classpath "$PLATFORM_JAR" -d "$TEST_DIR/classes" "$ROOT_DIR/android/native-tests/com/majlis/tests/CloudAudioSmoke.java"
mapfile -d '' classes < <(find "$TEST_DIR/classes" -name '*.class' -print0)
"$BUILD_TOOLS_DIR/d8" --lib "$PLATFORM_JAR" --min-api 26 --output "$TEST_DIR/dex" "${classes[@]}"
zip -q -j "$TEST_DIR/test.apk" "$TEST_DIR/dex/"*.dex
"$BUILD_TOOLS_DIR/zipalign" -f 4 "$TEST_DIR/test.apk" "$TEST_DIR/aligned.apk"
if [[ ! -f "$ROOT_DIR/tools/debug.keystore" ]]; then
    mkdir -p "$ROOT_DIR/tools"
    keytool -genkeypair -noprompt -keystore "$ROOT_DIR/tools/debug.keystore" -alias androiddebugkey -storetype JKS -storepass android -keypass android -keyalg RSA -keysize 2048 -validity 10000 -dname 'CN=Android Debug,O=Android,C=US'
fi
# The app and instrumentation must share the certificate, even though the
# emulator job starts from an APK artifact built in a different job.
"$BUILD_TOOLS_DIR/apksigner" sign --ks "$ROOT_DIR/tools/debug.keystore" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out "$TEST_DIR/app.apk" "$ROOT_DIR/dist/majlis-debug.apk"
"$BUILD_TOOLS_DIR/apksigner" sign --ks "$ROOT_DIR/tools/debug.keystore" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out "$TEST_DIR/signed.apk" "$TEST_DIR/aligned.apk"
adb uninstall com.majlis.app >/dev/null 2>&1 || true
adb install -g "$TEST_DIR/app.apk"
adb install -r "$TEST_DIR/signed.apk"
adb shell am instrument -w com.majlis.tests/com.majlis.tests.CloudAudioSmoke | tee "$ROOT_DIR/dist/android-smoke/cloud-audio-native.txt"
python3 - "$ROOT_DIR/dist/android-smoke/cloud-audio-native.txt" <<'PY'
import sys
from pathlib import Path
evidence=Path(sys.argv[1]).read_text()
if 'INSTRUMENTATION_RESULT: result=PASS' not in evidence or 'INSTRUMENTATION_CODE: -1' not in evidence:
    raise SystemExit('Native cloud audio codec verification failed')
print('Production cloud audio codecs verified on Android')
PY
adb uninstall com.majlis.tests >/dev/null
adb uninstall com.majlis.app >/dev/null
