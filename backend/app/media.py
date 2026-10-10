import asyncio
import json
import math
import shutil
from pathlib import Path

from fastapi import HTTPException
from starlette.datastructures import UploadFile


async def _save_upload(upload: UploadFile, path: Path, max_bytes: int):
    total = 0
    with path.open("wb") as destination:
        while chunk := await upload.read(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(413, "حجم التسجيل يتجاوز الحد المسموح.")
            destination.write(chunk)
    if total == 0:
        raise HTTPException(422, "ملف التسجيل فارغ.")


def _audio_format(path: Path) -> str:
    # Actual signatures choose a fixed demuxer. A client filename/MIME never permits
    # playlists, other containers, or network-backed media to reach FFmpeg.
    with path.open("rb") as source:
        header = source.read(12)
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "m4a"
    if header[:3] == b"ID3" or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0):
        return "mp3"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    raise HTTPException(422, "يلزم ملف صوت M4A أو MP3 أو WAV صالح.")


async def _run_media_command(arguments: list[str], timeout: float, *, capture=False):
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await asyncio.shield(process.wait())
        raise
    if process.returncode != 0:
        raise HTTPException(422, "تعذر قراءة الملف الصوتي؛ تأكد من سلامة التسجيل.")
    return output


async def audio_duration(path: Path, audio_format: str) -> float:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required; install FFmpeg before server startup")
    output = await _run_media_command([
        executable, "-v", "error", "-protocol_whitelist", "file,pipe",
        "-f", {"m4a": "mov", "mp3": "mp3", "wav": "wav"}[audio_format],
        "-show_entries", "format=duration,format_name:stream=codec_type,codec_name",
        "-of", "json", str(path),
    ], 15, capture=True)
    try:
        metadata = json.loads(output)
        streams = metadata["streams"]
        duration = float(metadata["format"]["duration"])
        formats = metadata["format"]["format_name"].split(",")
        if not streams or any(item.get("codec_type") != "audio" for item in streams):
            raise ValueError("not audio-only")
        if audio_format not in formats or not math.isfinite(duration) or duration <= 0:
            raise ValueError("not a finite supported audio duration")
        if audio_format == "mp3" and any(item.get("codec_name") != "mp3" for item in streams):
            raise ValueError("not MP3 audio")
        return duration
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "يلزم ملف صوتي صالح خال من الفيديو.") from None


async def save_m4a(upload: UploadFile, path: Path, max_bytes: int) -> float:
    """Keep known-speaker references in their original, strictly M4A form."""
    await _save_upload(upload, path, max_bytes)
    if _audio_format(path) != "m4a":
        raise HTTPException(422, "يلزم ملف M4A صالح لعينة المتحدث.")
    return await audio_duration(path, "m4a")


async def m4a_duration(path: Path) -> float:
    return await audio_duration(path, "m4a")


async def save_and_normalize_audio(
    upload: UploadFile, source_path: Path, output_path: Path,
    max_bytes: int, max_duration: float, timeout: float = 180,
) -> float:
    await _save_upload(upload, source_path, max_bytes)
    audio_format = _audio_format(source_path)
    duration = await audio_duration(source_path, audio_format)
    if duration > max_duration:
        raise HTTPException(422, "يتجاوز التسجيل الحد الأقصى البالغ 60 دقيقة.")
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required; install FFmpeg before server startup")
    try:
        # Bound decoding even if a damaged container understates its duration.
        # The extra second is checked afterwards; long audio is rejected, never silently clipped.
        await _run_media_command([
            executable, "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-f", {"m4a": "mov", "mp3": "mp3", "wav": "wav"}[audio_format],
            "-i", str(source_path), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-ac", "1", "-ar", "32000", "-c:a", "aac", "-b:a", "48k",
            "-t", str(max_duration + 1), "-fs", str(max_bytes + 1),
            "-movflags", "+faststart", "-f", "ipod", str(output_path),
        ], timeout)
        if output_path.stat().st_size >= max_bytes:
            raise HTTPException(413, "حجم التسجيل بعد تهيئته يتجاوز الحد المسموح.")
        normalized_duration = await audio_duration(output_path, "m4a")
        if normalized_duration > max_duration:
            raise HTTPException(422, "يتجاوز التسجيل الحد الأقصى البالغ 60 دقيقة.")
        return normalized_duration
    except BaseException:
        # Delete partial normalization output after errors, timeouts, or cancellation.
        output_path.unlink(missing_ok=True)
        raise
