#!/usr/bin/env bash
set -euo pipefail

# Standard Gradle build includes the Firebase/App Check runtime dependencies.
# The first build needs network access to Google's/Maven/Gradle repositories.
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-"$ROOT_DIR/tools/android-sdk"}}"
BUILD_TOOLS_DIR="$SDK_DIR/build-tools/35.0.0"
BUILD_APK="$ROOT_DIR/android/app/build/outputs/apk/debug/app-debug.apk"
APK_PATH="$ROOT_DIR/dist/majlis-debug.apk"
KEYSTORE_PATH="$ROOT_DIR/tools/debug.keystore"
JAVA_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}java"
KEYTOOL_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}keytool"

for required_command in "$JAVA_COMMAND" "$KEYTOOL_COMMAND" python3; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        printf 'Required build command is missing: %s\n' "$required_command" >&2
        exit 1
    fi
done
bash "$ROOT_DIR/scripts/bootstrap-android.sh"
export ANDROID_HOME="$SDK_DIR"
export ANDROID_SDK_ROOT="$SDK_DIR"

version_metadata="$(python3 - "$ROOT_DIR/android/app/build.gradle" <<'PY'
import re
import sys
from pathlib import Path

gradle = Path(sys.argv[1]).read_text(encoding='utf-8')
code = re.findall(r'^\s*versionCode\s+(\d+)\s*$', gradle, re.MULTILINE)
name = re.findall(r"^\s*versionName\s+'([A-Za-z0-9._-]+)'\s*$", gradle, re.MULTILINE)
if len(code) != 1 or len(name) != 1 or int(code[0]) < 1:
    raise SystemExit('Expected one positive versionCode and one versionName in app/build.gradle')
print(code[0], name[0])
PY
)"
read -r VERSION_CODE VERSION_NAME <<< "$version_metadata"

mkdir -p "$ROOT_DIR/tools" "$ROOT_DIR/dist"
if [[ ! -f "$KEYSTORE_PATH" ]]; then
    # Preserve this ignored private file for update-compatible builds and
    # register its public fingerprints with Firebase/App Check as documented.
    "$KEYTOOL_COMMAND" -genkeypair -noprompt \
        -keystore "$KEYSTORE_PATH" -alias androiddebugkey \
        -storetype JKS -storepass android -keypass android \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -dname 'CN=Android Debug,O=Android,C=US'
fi

GRADLE_COMMAND="$(bash "$ROOT_DIR/scripts/bootstrap-gradle.sh")"
"$GRADLE_COMMAND" --project-dir "$ROOT_DIR/android" --no-daemon :app:assembleDebug

VERIFY_DIR="$(mktemp -d "$ROOT_DIR/dist/apk-verify.XXXXXXXX")"
trap 'rm -rf -- "$VERIFY_DIR"' EXIT
"$BUILD_TOOLS_DIR/apksigner" verify --verbose --print-certs "$BUILD_APK" > "$VERIFY_DIR/signature.txt"
"$BUILD_TOOLS_DIR/aapt2" dump badging "$BUILD_APK" > "$VERIFY_DIR/metadata.txt"
python3 - "$BUILD_APK" "$VERIFY_DIR/metadata.txt" "$VERIFY_DIR/signature.txt" \
    "$VERIFY_DIR/majlis-signing-certificate.txt" "$VERSION_CODE" "$VERSION_NAME" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

apk, metadata_file, signature_file, certificate_file, version_code, version_name = sys.argv[1:]
metadata = Path(metadata_file).read_text(encoding='utf-8')
def mismatch(expected):
    raise SystemExit('APK metadata mismatch: ' + expected)

package_line = re.search(r'^\s*package\s*:\s*(.*)$', metadata, re.MULTILINE)
if package_line is None:
    mismatch('package declaration')
attributes = dict(re.findall(r"(\w+)\s*=\s*'([^']*)'", package_line.group(1)))
for attribute, expected in (('name', 'com.majlis.app'), ('versionCode', version_code),
                            ('versionName', version_name)):
    if attributes.get(attribute) != expected:
        mismatch(attribute + '=' + expected)
for label, expected in (('(?:minSdkVersion|sdkVersion)', '26'), ('targetSdkVersion', '35')):
    value = re.search(r'^\s*' + label + r"\s*:\s*['\"]?(\d+)['\"]?\s*$", metadata, re.MULTILINE)
    if value is None or value.group(1) != expected:
        mismatch(label + '=' + expected)
with zipfile.ZipFile(apk) as archive:
    names = archive.namelist()
    for required in ('AndroidManifest.xml', 'classes.dex', 'resources.arsc', 'assets/cloud-config.json'):
        if required not in names:
            raise SystemExit('APK is missing ' + required)
    cloud = json.loads(archive.read('assets/cloud-config.json'))
    if set(cloud) != {'enabled', 'project_id', 'api_key', 'app_id', 'model'} or not isinstance(cloud['enabled'], bool):
        raise SystemExit('APK cloud configuration does not follow the public asset contract')
    for name in names:
        if name == 'assets/google-services.json' or name.startswith('assets/models/') or (
            name.startswith('assets/') and name.lower().endswith(('.onnx', '.tflite', '.gguf', '.safetensors', '.pt'))
        ):
            raise SystemExit('APK unexpectedly includes operator config or a local model')
signature = Path(signature_file).read_text(encoding='utf-8')
fingerprints = {}
for label, length in (('SHA-1', 40), ('SHA-256', 64)):
    values = re.findall(r'^Signer #1 certificate ' + label + r' digest: ([a-fA-F0-9]{' + str(length) + r'})\s*$',
                        signature, re.MULTILINE)
    if len(values) != 1:
        raise SystemExit('APK signing certificate fingerprint is missing: ' + label)
    fingerprints[label] = values[0].upper()
apk_hash = hashlib.sha256(Path(apk).read_bytes()).hexdigest()
report = ('Package: com.majlis.app\nVersion: ' + version_name + ' (' + version_code + ')\n'
          + 'Certificate SHA-1: ' + fingerprints['SHA-1'] + '\n'
          + 'Certificate SHA-256: ' + fingerprints['SHA-256'] + '\n'
          + 'APK SHA-256: ' + apk_hash + '\n')
Path(certificate_file).write_text(report, encoding='utf-8')
print('Verified APK signature, package, version ' + version_name + ' (' + version_code
      + '), SDK levels, cloud asset, and required files')
print(report, end='')
PY

cp -- "$BUILD_APK" "$APK_PATH"
cp -- "$VERIFY_DIR/majlis-signing-certificate.txt" "$ROOT_DIR/dist/majlis-signing-certificate.txt"
python3 - "$APK_PATH" <<'PY'
import hashlib
from pathlib import Path
import sys

apk = Path(sys.argv[1])
apk.with_suffix('.apk.sha256').write_text(hashlib.sha256(apk.read_bytes()).hexdigest()
                                       + '  ' + apk.name + '\n', encoding='ascii')
PY
printf '\nBuilt and signature-verified: %s\n' "$APK_PATH"
