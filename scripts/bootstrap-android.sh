#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-"$ROOT_DIR/tools/android-sdk"}}"
PLATFORM_JAR="$SDK_DIR/platforms/android-35/android.jar"
BUILD_TOOLS_DIR="$SDK_DIR/build-tools/35.0.0"

# Read-only check. Install through Google's SDK Manager so it presents the
# Android SDK license and validates the official package downloads.
missing=()
[[ -f "$PLATFORM_JAR" ]] || missing+=("Android SDK Platform 35")
for sdk_tool in aapt2 d8 zipalign apksigner; do
    [[ -x "$BUILD_TOOLS_DIR/$sdk_tool" ]] || missing+=("$sdk_tool (Build-Tools 35.0.0)")
done

if (( ${#missing[@]} > 0 )); then
    printf 'Missing Android SDK packages under: %s\n' "$SDK_DIR" >&2
    printf '  %s\n' "${missing[@]}" >&2
    printf '\nIn Android Studio, open Tools > SDK Manager and install:\n' >&2
    printf '  Android SDK Platform 35\n  Android SDK Build-Tools 35.0.0\n' >&2
    printf '\nOr run Google SDK Manager (review its license when requested):\n' >&2
    printf '  sdkmanager --sdk_root=%q "platforms;android-35" "build-tools;35.0.0"\n' "$SDK_DIR" >&2
    printf '\nSet ANDROID_HOME to that SDK directory before building.\n' >&2
    exit 1
fi

printf 'Android SDK is ready: %s\n' "$SDK_DIR"
printf 'Build with: bash %q\n' "$ROOT_DIR/scripts/build-android.sh"
