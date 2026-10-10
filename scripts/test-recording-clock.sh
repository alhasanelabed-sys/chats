#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}java"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/majlis-clock-tests.XXXXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT

"$JAVA_COMMAND" com.sun.tools.javac.Main --release 8 -encoding UTF-8 \
    -d "$TEST_DIR" \
    "$ROOT_DIR/android/app/src/main/java/com/majlis/app/RecordingClock.java" \
    "$ROOT_DIR/android/app/src/test/java/com/majlis/app/RecordingClockTest.java"
"$JAVA_COMMAND" -cp "$TEST_DIR" com.majlis.app.RecordingClockTest
