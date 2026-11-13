from pydantic import BaseModel, Field


class Hook(BaseModel):
    text: str = Field(min_length=1, max_length=180)
    mechanism: str = ""
    visual: str = ""
    promise: str = ""
    audio: str = ""
    screen_text: str = ""


class HooksOutput(BaseModel):
    hooks: list[Hook] = Field(min_length=3, max_length=3)


class Structure(BaseModel):
    quote: str = ""
    role: str = ""
    reason: str = ""


class AnalysisOutput(BaseModel):
    summary: str = Field(min_length=1)
    audience: str = ""
    angle: str = ""
    hook: str = ""
    structure: list[Structure]
    steps: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)


class Shot(BaseModel):
    segment: str = ""
    speech: str = ""
    screen: str = ""
    evidence: str = ""
    time: str = ""
    visual: str = ""
    audio: str = ""
    tool_point: str = ""
    asset_method: str = ""


class ScriptOutput(BaseModel):
    content: str = Field(min_length=1)
    shooting_plan: list[Shot]
    verification: list[str] = Field(default_factory=list)
