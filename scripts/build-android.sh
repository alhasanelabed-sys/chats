#!/usr/bin/env bash
set -euo pipefail

# Offline build using Google's installed SDK. No Gradle, download, or external
# Android library is required. Supports Linux/macOS with Bash and a JDK 17+.
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-"$ROOT_DIR/tools/android-sdk"}}"
PLATFORM_JAR="$SDK_DIR/platforms/android-35/android.jar"
BUILD_TOOLS_DIR="$SDK_DIR/build-tools/35.0.0"
SOURCE_DIR="$ROOT_DIR/android/app/src/main"
APK_PATH="$ROOT_DIR/dist/majlis-debug.apk"
KEYSTORE_PATH="$ROOT_DIR/tools/debug.keystore"
JAVA_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}java"
KEYTOOL_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}keytool"

bash "$ROOT_DIR/scripts/bootstrap-android.sh"
for required_command in "$JAVA_COMMAND" "$KEYTOOL_COMMAND" python3 zip find; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        printf 'Required build command is missing: %s\n' "$required_command" >&2
        exit 1
    fi
done

# Keep the installable APK's metadata aligned with the Gradle build.
version_metadata="$(python3 - "$ROOT_DIR/android/app/build.gradle" <<'PY'
import re
import sys
from pathlib import Path

gradle = Path(sys.argv[1]).read_text(encoding="utf-8")
code = re.findall(r"^\s*versionCode\s+(\d+)\s*$", gradle, re.MULTILINE)
name = re.findall(r"^\s*versionName\s+'([A-Za-z0-9._-]+)'\s*$", gradle, re.MULTILINE)
if len(code) != 1 or len(name) != 1 or int(code[0]) < 1:
    raise SystemExit("Expected one positive versionCode and one versionName in app/build.gradle")
print(code[0], name[0])
PY
)"
read -r VERSION_CODE VERSION_NAME <<< "$version_metadata"

mkdir -p "$ROOT_DIR/android/app/build" "$ROOT_DIR/dist" "$ROOT_DIR/tools"
BUILD_DIR="$(mktemp -d "$ROOT_DIR/android/app/build/manual.XXXXXXXX")"
trap 'rm -rf -- "$BUILD_DIR"' EXIT
mkdir -p "$BUILD_DIR/generated" "$BUILD_DIR/classes" "$BUILD_DIR/dex"

# AGP uses namespace in build.gradle. AAPT2's direct CLI requires the package
# attribute, so set it in a temporary manifest without touching app sources.
python3 - "$SOURCE_DIR/AndroidManifest.xml" "$BUILD_DIR/AndroidManifest.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

ET.register_namespace("android", "http://schemas.android.com/apk/res/android")
manifest = ET.parse(sys.argv[1])
manifest.getroot().set("package", "com.majlis.app")
# Direct AAPT2 linking must include uses-sdk explicitly; Gradle normally
# merges this element from defaultConfig. Keep the offline manifest equivalent.
uses_sdk = manifest.getroot().find("uses-sdk")
if uses_sdk is None:
    uses_sdk = ET.Element("uses-sdk")
    manifest.getroot().insert(0, uses_sdk)
uses_sdk.set("{http://schemas.android.com/apk/res/android}minSdkVersion", "26")
uses_sdk.set("{http://schemas.android.com/apk/res/android}targetSdkVersion", "35")
application = manifest.getroot().find("application")
if application is None:
    raise SystemExit("AndroidManifest.xml has no application element")
application.set("{http://schemas.android.com/apk/res/android}debuggable", "true")
manifest.write(sys.argv[2], encoding="utf-8", xml_declaration=True)
PY

"$BUILD_TOOLS_DIR/aapt2" compile \
    --dir "$SOURCE_DIR/res" -o "$BUILD_DIR/resources.zip"
asset_args=()
if [[ -d "$SOURCE_DIR/assets" ]]; then
    asset_args=(-A "$SOURCE_DIR/assets")
fi
"$BUILD_TOOLS_DIR/aapt2" link \
    -o "$BUILD_DIR/unsigned.apk" \
    -I "$PLATFORM_JAR" \
    --manifest "$BUILD_DIR/AndroidManifest.xml" \
    --java "$BUILD_DIR/generated" \
    --min-sdk-version 26 --target-sdk-version 35 \
    --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" \
    "${asset_args[@]}" \
    "$BUILD_DIR/resources.zip"

java_sources=()
while IFS= read -r -d '' source_file; do
    java_sources+=("$source_file")
done < <(find "$SOURCE_DIR/java" "$BUILD_DIR/generated" -type f -name '*.java' -print0)
if (( ${#java_sources[@]} == 0 )); then
    printf 'No Java sources were found.\n' >&2
    exit 1
fi

# This compiler entry point also works when the JDK exposes javac as a module
# through java but has no separate javac launcher on PATH.
"$JAVA_COMMAND" com.sun.tools.javac.Main \
    -encoding UTF-8 --release 8 \
    -classpath "$PLATFORM_JAR" \
    -d "$BUILD_DIR/classes" "${java_sources[@]}"

class_files=()
while IFS= read -r -d '' class_file; do
    class_files+=("$class_file")
done < <(find "$BUILD_DIR/classes" -type f -name '*.class' -print0)
if (( ${#class_files[@]} == 0 )); then
    printf 'Java compilation produced no class files.\n' >&2
    exit 1
fi
"$BUILD_TOOLS_DIR/d8" \
    --lib "$PLATFORM_JAR" --min-api 26 \
    --output "$BUILD_DIR/dex" "${class_files[@]}"
zip -q -j "$BUILD_DIR/unsigned.apk" "$BUILD_DIR/dex/"*.dex
"$BUILD_TOOLS_DIR/zipalign" -f 4 "$BUILD_DIR/unsigned.apk" "$BUILD_DIR/aligned.apk"

if [[ ! -f "$KEYSTORE_PATH" ]]; then
    "$KEYTOOL_COMMAND" -genkeypair -noprompt \
        -keystore "$KEYSTORE_PATH" -alias androiddebugkey \
        -storetype JKS -storepass android -keypass android \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -dname 'CN=Android Debug,O=Android,C=US'
fi
"$BUILD_TOOLS_DIR/apksigner" sign \
    --ks "$KEYSTORE_PATH" --ks-key-alias androiddebugkey \
    --ks-pass pass:android --key-pass pass:android \
    --out "$APK_PATH" "$BUILD_DIR/aligned.apk"
"$BUILD_TOOLS_DIR/apksigner" verify --verbose "$APK_PATH"
"$BUILD_TOOLS_DIR/aapt2" dump badging "$APK_PATH" > "$BUILD_DIR/apk-metadata.txt"
python3 - "$APK_PATH" "$BUILD_DIR/apk-metadata.txt" "$VERSION_CODE" "$VERSION_NAME" <<'PY'
import sys
import re
import zipfile
from pathlib import Path

apk, metadata_file, version_code, version_name = sys.argv[1:]
metadata = Path(metadata_file).read_text(encoding="utf-8")
def mismatch(expected):
    print("AAPT2 APK badging:\n" + metadata, file=sys.stderr)
    raise SystemExit(f"APK metadata mismatch: {expected}")

package_line = re.search(r"^\s*package\s*:\s*(.*)$", metadata, re.MULTILINE)
if package_line is None:
    mismatch("package declaration")
package_attributes = dict(re.findall(r"(\w+)\s*=\s*'([^']*)'", package_line.group(1)))
for attribute, expected in (
    ("name", "com.majlis.app"),
    ("versionCode", version_code),
    ("versionName", version_name),
):
    if package_attributes.get(attribute) != expected:
        mismatch(f"{attribute}={expected}")
for label, expected in (("(?:minSdkVersion|sdkVersion)", "26"), ("targetSdkVersion", "35")):
    value = re.search(r"^\s*" + label + r"\s*:\s*['\"]?(\d+)['\"]?\s*$", metadata, re.MULTILINE)
    if value is None or value.group(1) != expected:
        mismatch(f"{label}={expected}")
with zipfile.ZipFile(apk) as archive:
    for required_file in ("AndroidManifest.xml", "classes.dex", "resources.arsc"):
        if required_file not in archive.namelist():
            raise SystemExit(f"APK is missing {required_file}")
print(f"Verified APK package, version {version_name} ({version_code}), SDK levels, and required files")
PY
printf '\nBuilt and signature-verified: %s\n' "$APK_PATH"
