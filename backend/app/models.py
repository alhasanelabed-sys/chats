from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Speaker(StrictModel):
    id: str
    name: str
    matched_reference: bool


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
