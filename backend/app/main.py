import json
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import TypeAdapter, ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from .media import save_and_normalize_audio, save_m4a
from .models import MeetingAnalysis, MeetingTranslation, Participant, SummaryRequest, TranslationRequest
from .provider import OpenAIProvider, ProviderFailure
from .settings import Settings


def check_content_length(request: Request, limit: int):
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) < 0:
                raise ValueError()
            if int(declared_length) > limit:
                raise HTTPException(413, "حجم الطلب يتجاوز الحد المسموح.")
        except ValueError:
            raise HTTPException(400, "حجم الطلب غير صالح.") from None


async def read_bounded_json(request: Request, limit: int) -> bytearray:
    check_content_length(request, limit)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(422, "يلزم إرسال نص الاجتماع بصيغة JSON.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "حجم نص الاجتماع يتجاوز الحد المسموح.")
        body.extend(chunk)
    return body


def create_app(settings: Settings | None = None, provider: OpenAIProvider | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_environment()
        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            raise RuntimeError("ffprobe and ffmpeg are required; install FFmpeg before server startup")
        app.state.provider = provider or OpenAIProvider(app.state.settings)
        try:
            yield
        finally:
            await app.state.provider.close()

    application = FastAPI(title="Majlis meeting analysis", version="0.3", lifespan=lifespan)

    async def authorize(request: Request):
        expected = f"Bearer {request.app.state.settings.access_token}".encode("utf-8")
        supplied = request.headers.get("Authorization", "").encode("utf-8")
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "رمز الوصول غير صالح.", headers={"WWW-Authenticate": "Bearer"})

    @application.get("/health")
    async def health():
        return {"status": "ok"}

    @application.get("/v1/status", dependencies=[Depends(authorize)])
    async def status(request: Request):
        configuration = request.app.state.settings
        return {"status": "ok", "version": "0.3", "max_audio_bytes": configuration.max_audio_bytes,
                "max_duration_seconds": configuration.max_duration_seconds,
                "formats": ["m4a", "mp3", "wav"], "reference_speakers": 4}

    @application.post("/v1/meetings/summarize", response_model=MeetingAnalysis,
                      dependencies=[Depends(authorize)])
    async def summarize(request: Request):
        configuration = request.app.state.settings
        body = await read_bounded_json(request, configuration.max_summary_request_bytes)
        try:
            transcript = SummaryRequest.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "نص الاجتماع أو أسماء المتحدثين أو التوقيتات غير صالحة.") from None
        if transcript.duration_seconds > configuration.max_duration_seconds:
            raise HTTPException(422, "يتجاوز الاجتماع الحد الأقصى البالغ 60 دقيقة.")
        try:
            return await request.app.state.provider.summarize(
                transcript.title, transcript.duration_seconds, transcript.segments, transcript.speakers,
            )
        except ProviderFailure:
            raise HTTPException(502, "تعذر إعداد المحضر لدى مزود الخدمة. تحقق من إعدادات الخادم ثم أعد المحاولة.") from None

    @application.post("/v1/meetings/translate", response_model=MeetingTranslation,
                      dependencies=[Depends(authorize)])
    async def translate(request: Request):
        configuration = request.app.state.settings
        body = await read_bounded_json(request, configuration.max_summary_request_bytes)
        try:
            translation = TranslationRequest.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "لغة الترجمة أو نص الاجتماع أو أدلة المحضر غير صالحة.") from None
        if translation.meeting.duration_seconds > configuration.max_duration_seconds:
            raise HTTPException(422, "يتجاوز الاجتماع الحد الأقصى البالغ 60 دقيقة.")
        try:
            return await request.app.state.provider.translate(translation.meeting, translation.target_language)
        except ProviderFailure:
            raise HTTPException(502, "تعذرت ترجمة الاجتماع لدى مزود الخدمة. تحقق من إعدادات الخادم ثم أعد المحاولة.") from None

    @application.post("/v1/meetings/analyze", response_model=MeetingAnalysis,
                      dependencies=[Depends(authorize)])
    async def analyze(request: Request):
        configuration = request.app.state.settings
        check_content_length(request, configuration.max_request_bytes)

        # Count actual bytes too: chunked transfers cannot bypass the request limit.
        original_receive = request._receive
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await original_receive()
            received += len(message.get("body", b""))
            if received > configuration.max_request_bytes:
                # Starlette's multipart parser closes its partial temporary files on this exception.
                raise MultiPartException("request_body_too_large")
            return message

        request._receive = bounded_receive
        try:
            async with request.form(max_files=5, max_fields=2, max_part_size=16_384) as form:
                # Reject duplicates so the client and server cannot interpret different values.
                keys = [key for key, _ in form.multi_items()]
                if len(keys) != len(set(keys)):
                    raise HTTPException(422, "حقول الطلب مكررة.")
                title = form.get("title")
                participants_value = form.get("participants", "[]")
                audio = form.get("audio")
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                    raise HTTPException(422, "يلزم عنوان اجتماع بطول من 1 إلى 160 حرفا.")
                if not isinstance(audio, UploadFile) or not isinstance(participants_value, str):
                    raise HTTPException(422, "ملف التسجيل أو قائمة المشاركين غير صالحة.")
                try:
                    participants = TypeAdapter(list[Participant]).validate_python(json.loads(participants_value))
                except (ValueError, ValidationError):
                    raise HTTPException(422, "قائمة المشاركين غير صالحة.") from None
                if len(participants) > 32 or len({item.id for item in participants}) != len(participants):
                    raise HTTPException(422, "يلزم استخدام معرفات مختلفة لعدد لا يتجاوز 32 مشاركا.")
                for participant in participants:
                    participant.name = participant.name.strip()
                    if not participant.name:
                        raise HTTPException(422, "اسم المشارك فارغ.")
                referenced = [item for item in participants if item.reference_field]
                reference_keys = [item.reference_field for item in referenced]
                if len(referenced) > 4 or len(reference_keys) != len(set(reference_keys)):
                    raise HTTPException(422, "يسمح بأربع عينات صوتية مختلفة كحد أقصى.")
                allowed = {"audio", "title", "participants", *reference_keys}
                if set(keys) - allowed:
                    raise HTTPException(422, "يتضمن الطلب حقولا غير معروفة.")
                if any(not isinstance(form.get(key), UploadFile) for key in reference_keys):
                    raise HTTPException(422, "عينة صوت أحد المشاركين مفقودة.")
                # User filenames never become paths. Both parser and our private directory are cleaned.
                with tempfile.TemporaryDirectory(prefix="majlis-") as temporary:
                    directory = Path(temporary)
                    audio_path = directory / "meeting.m4a"
                    duration = await save_and_normalize_audio(
                        audio, directory / "uploaded-audio", audio_path,
                        configuration.max_audio_bytes, configuration.max_duration_seconds,
                        configuration.normalization_timeout_seconds,
                    )
                    references = []
                    for index, participant in enumerate(referenced):
                        reference_path = directory / f"reference_{index}.m4a"
                        reference_duration = await save_m4a(
                            form[participant.reference_field], reference_path,
                            configuration.max_reference_bytes,
                        )
                        if not 2 <= reference_duration <= 10:
                            raise HTTPException(422, "يجب أن تتراوح مدة كل عينة صوت بين ثانيتين و10 ثوان.")
                        references.append((participant, reference_path))
                    return await request.app.state.provider.analyze(audio_path, title.strip(), duration, references)
        except StarletteHTTPException as error:
            if error.detail == "request_body_too_large":
                raise HTTPException(413, "حجم الطلب يتجاوز الحد المسموح.") from None
            raise
        except ProviderFailure:
            raise HTTPException(502, "تعذر تحليل الاجتماع لدى مزود الخدمة. تحقق من إعدادات الخادم ثم أعد المحاولة.") from None
        except TimeoutError:
            raise HTTPException(422, "تعذر التحقق من الملف الصوتي ضمن الوقت المسموح.") from None

    return application


app = create_app()
