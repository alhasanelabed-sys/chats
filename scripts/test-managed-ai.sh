#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
JSON_JAR="${MAJLIS_JSON_JAR:-"$ROOT_DIR/tools/json-20240303.jar"}"
JAVA_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}java"
if [[ ! -f "$JSON_JAR" ]]; then
    printf 'Missing org.json:json:20240303 jar. Set MAJLIS_JSON_JAR to its path.\n' >&2
    exit 1
fi
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/majlis-managed-ai-tests.XXXXXXXX")"
trap 'rm -rf -- "$BUILD_DIR"' EXIT
# Some minimal JDK distributions omit ct.sym, which --release requires.
"$JAVA_COMMAND" com.sun.tools.javac.Main -source 8 -target 8 -encoding UTF-8 \
    -classpath "$JSON_JAR" -d "$BUILD_DIR" \
    "$ROOT_DIR/android/app/src/main/java/com/majlis/app/MeetingApi.java" \
    "$ROOT_DIR/android/app/src/main/java/com/majlis/app/ProviderPrompts.java" \
    "$ROOT_DIR/android/app/src/main/java/com/majlis/app/ManagedAiClient.java" \
    "$ROOT_DIR/android/tests/com/majlis/app/ManagedAiClientTest.java"
"$JAVA_COMMAND" --add-modules jdk.httpserver -classpath "$BUILD_DIR:$JSON_JAR" \
    com.majlis.app.ManagedAiClientTest
