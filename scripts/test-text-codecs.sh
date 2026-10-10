#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA_COMMAND="${JAVA_HOME:+$JAVA_HOME/bin/}java"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/majlis-text-codecs-tests.XXXXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT

# The installed JDK may omit ct.sym; source/target 8 keeps this pure Java check local.
"$JAVA_COMMAND" com.sun.tools.javac.Main -source 8 -target 8 -encoding UTF-8 \
    -d "$TEST_DIR" \
    "$ROOT_DIR/android/app/src/main/java/com/majlis/app/TextCodecs.java" \
    "$ROOT_DIR/android/tests/com/majlis/app/TextCodecsTest.java"
"$JAVA_COMMAND" -cp "$TEST_DIR" com.majlis.app.TextCodecsTest
