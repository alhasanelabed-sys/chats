#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
GRADLE_VERSION='8.9'
# Official binary distribution checksum: https://gradle.org/release-checksums/
GRADLE_SHA256='d725d707bfabd4dfdc958c624003b3c80accc03f7037b5122c4b1d0ef15cecab'
CACHE_DIR="${MAJLIS_GRADLE_CACHE_DIR:-"$ROOT_DIR/tools/gradle"}"
ARCHIVE_PATH="$CACHE_DIR/gradle-$GRADLE_VERSION-bin.zip"
GRADLE_DIR="$CACHE_DIR/gradle-$GRADLE_VERSION"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3 is required to verify and extract Gradle.\n' >&2
    exit 1
fi
mkdir -p "$CACHE_DIR"

if [[ ! -f "$ARCHIVE_PATH" ]]; then
    if ! command -v curl >/dev/null 2>&1; then
        printf 'curl is required for the first Gradle download.\n' >&2
        exit 1
    fi
    TEMP_ARCHIVE="$(mktemp "$CACHE_DIR/gradle-download.XXXXXXXX")"
    trap 'rm -f -- "$TEMP_ARCHIVE"' EXIT
    printf 'Downloading Gradle %s from the official distribution service.\n' "$GRADLE_VERSION" >&2
    curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
        --output "$TEMP_ARCHIVE" "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
    mv -- "$TEMP_ARCHIVE" "$ARCHIVE_PATH"
    trap - EXIT
fi

# Verify the archive before trusting an extracted copy or executing Gradle.
python3 - "$ARCHIVE_PATH" "$GRADLE_SHA256" <<'PY'
import hashlib
from pathlib import Path
import sys

archive, expected = sys.argv[1:]
digest = hashlib.sha256()
with Path(archive).open('rb') as handle:
    for block in iter(lambda: handle.read(1024 * 1024), b''):
        digest.update(block)
if digest.hexdigest() != expected:
    raise SystemExit('Gradle SHA-256 verification failed; remove the cached archive and retry.')
PY

if [[ ! -x "$GRADLE_DIR/bin/gradle" || ! -f "$GRADLE_DIR/.majlis-verified-sha256" ]] || \
   [[ "$(cat "$GRADLE_DIR/.majlis-verified-sha256" 2>/dev/null || true)" != "$GRADLE_SHA256" ]]; then
    python3 - "$ARCHIVE_PATH" "$CACHE_DIR" "$GRADLE_VERSION" "$GRADLE_SHA256" <<'PY'
from pathlib import Path
import shutil
import sys
import zipfile

archive, cache, version, checksum = sys.argv[1:]
destination = Path(cache).resolve()
distribution = destination / ('gradle-' + version)
if distribution.exists():
    shutil.rmtree(distribution)
with zipfile.ZipFile(archive) as source:
    for member in source.infolist():
        target = (destination / member.filename).resolve()
        if not target.is_relative_to(distribution) or member.filename.startswith('/'):
            raise SystemExit('Gradle archive contains an unexpected path.')
    source.extractall(destination)
(distribution / 'bin/gradle').chmod(0o755)
(distribution / '.majlis-verified-sha256').write_text(checksum, encoding='ascii')
PY
fi

# stdout is deliberately just the executable path for build-android.sh.
printf '%s\n' "$GRADLE_DIR/bin/gradle"
