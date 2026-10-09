import asyncio
import json
import math
import shutil
from pathlib import Path

from fastapi import HTTPException
from starlette.datastructures import UploadFile


async def save_m4a(upload: UploadFile, path: Path, max_bytes: int) -> float:
    total = 0
    with path.open("wb") as destination:
        while chunk := await upload.read(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(413, "حجم التسجيل يتجاوز الحد المسموح.")
            destination.write(chunk)
    if total == 0:
        raise HTTPException(422, "ملف التسجيل فارغ.")
    with path.open("rb") as source:
        header = source.read(12)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise HTTPException(422, "يلزم ملف صوت M4A صالح.")
    return await m4a_duration(path)


async def m4a_duration(path: Path) -> float:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required; install FFmpeg before server startup")
    process = await asyncio.create_subprocess_exec(
        executable, "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_entries", "format=duration,format_name:stream=codec_type",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise HTTPException(422, "تعذر قراءة الملف الصوتي؛ تأكد من سلامة التسجيل.")
    try:
        metadata = json.loads(output)
        streams = metadata["streams"]
        duration = float(metadata["format"]["duration"])
        formats = metadata["format"]["format_name"].split(",")
        if not streams or any(item.get("codec_type") != "audio" for item in streams):
            raise ValueError("not audio-only")
        if "m4a" not in formats or not math.isfinite(duration) or duration <= 0:
            raise ValueError("not a finite M4A duration")
        return duration
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "يلزم ملف M4A صوتي قابل للقراءة.") from None
