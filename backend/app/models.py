from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Participant(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=80)
    reference_field: str | None = Field(default=None, pattern=r"^reference_[0-3]$")


class Segment(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    speaker_id: str = Field(min_length=1, max_length=128)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(max_length=20_000)

    @field_validator("id", "speaker_id")
    @classmethod
    def meaningful_id(cls, value):
        if not value.strip():
            raise ValueError("An identifier must not be blank")
        return value


class Speaker(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    matched_reference: bool

    @field_validator("id", "name")
    @classmethod
    def meaningful_value(cls, value):
        if not value.strip():
            raise ValueError("Speaker values must not be blank")
        return value


class SummaryRequest(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    language: str = Field(pattern=r"^ar$")
    duration_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    segments: list[Segment] = Field(max_length=2000)
    speakers: list[Speaker] = Field(max_length=128)

    @field_validator("title")
    @classmethod
    def meaningful_title(cls, value):
        if not value.strip():
            raise ValueError("A meeting title must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def consistent_transcript(self):
        segment_ids = [item.id for item in self.segments]
        speaker_ids = [item.id for item in self.speakers]
        if len(set(segment_ids)) != len(segment_ids) or len(set(speaker_ids)) != len(speaker_ids):
            raise ValueError("Segment and speaker identifiers must be unique")
        if bool(self.segments) != bool(self.speakers):
            raise ValueError("An empty transcript must also have no speakers")
        known_speakers = set(speaker_ids)
        if any(item.speaker_id not in known_speakers or not item.text.strip()
               or item.end < item.start or item.end > self.duration_seconds + 1
               for item in self.segments):
            raise ValueError("Segments must contain speech and valid speakers and timestamps")
        if self.segments and self.duration_seconds <= 0:
            raise ValueError("A transcript with speech needs a positive duration")
        if sum(len(item.text) for item in self.segments) > 300_000:
            raise ValueError("The transcript is too long")
        return self


class Decision(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    segment_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("A decision needs meaningful text")
        return value


class ActionItem(StrictModel):
    task: str = Field(min_length=1, max_length=2000)
    owner: str | None = Field(max_length=160)
    due_date: str | None = Field(max_length=160)
    segment_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("task")
    @classmethod
    def meaningful_task(cls, value):
        if not value.strip():
            raise ValueError("An action needs meaningful text")
        return value


class Minutes(StrictModel):
    summary: str = Field(min_length=1, max_length=12_000)
    discussion_points: list[str] = Field(max_length=100)
    decisions: list[Decision] = Field(max_length=100)
    action_items: list[ActionItem] = Field(max_length=100)
    open_questions: list[str] = Field(max_length=100)

    @field_validator("summary")
    @classmethod
    def meaningful_summary(cls, value):
        if not value.strip():
            raise ValueError("A summary needs meaningful text")
        return value

    @field_validator("discussion_points", "open_questions")
    @classmethod
    def meaningful_items(cls, values):
        if any(not item.strip() for item in values):
            raise ValueError("Minute items must not be blank")
        return values


class MeetingAnalysis(StrictModel):
    title: str
    language: str
    duration_seconds: float
    segments: list[Segment]
    speakers: list[Speaker]
    minutes: Minutes
