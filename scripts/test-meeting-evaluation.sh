#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
JSON_JAR="${MAJLIS_JSON_JAR:-"$ROOT_DIR/tools/json-20240303.jar"}"
JAVA_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}java"
if [[ ! -f "$JSON_JAR" ]]; then
    printf 'Missing org.json:json:20240303 jar. Set MAJLIS_JSON_JAR to its path.\n' >&2
    exit 1
fi
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/majlis-evaluation-tests.XXXXXXXX")"
trap 'rm -rf -- "$BUILD_DIR"' EXIT
"$JAVA_COMMAND" com.sun.tools.javac.Main --release 8 -encoding UTF-8 \
    -classpath "$JSON_JAR" -d "$BUILD_DIR" \
    "$ROOT_DIR/android/app/src/main/java/com/majlis/app/MeetingEvaluation.java" \
    "$ROOT_DIR/android/tests/com/majlis/app/MeetingEvaluationTest.java"
"$JAVA_COMMAND" -classpath "$BUILD_DIR:$JSON_JAR" com.majlis.app.MeetingEvaluationTest
