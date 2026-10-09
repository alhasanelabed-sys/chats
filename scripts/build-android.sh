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
application = manifest.getroot().find("application")
if application is None:
    raise SystemExit("AndroidManifest.xml has no application element")
application.set("{http://schemas.android.com/apk/res/android}debuggable", "true")
manifest.write(sys.argv[2], encoding="utf-8", xml_declaration=True)
PY

"$BUILD_TOOLS_DIR/aapt2" compile \
    --dir "$SOURCE_DIR/res" -o "$BUILD_DIR/resources.zip"
"$BUILD_TOOLS_DIR/aapt2" link \
    -o "$BUILD_DIR/unsigned.apk" \
    -I "$PLATFORM_JAR" \
    --manifest "$BUILD_DIR/AndroidManifest.xml" \
    --java "$BUILD_DIR/generated" \
    --min-sdk-version 26 --target-sdk-version 35 \
    --version-code 1 --version-name 0.1 \
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
printf '\nBuilt and signature-verified: %s\n' "$APK_PATH"
