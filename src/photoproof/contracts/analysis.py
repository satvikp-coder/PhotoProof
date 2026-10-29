"""Analysis request and result contracts."""

from pydantic import BaseModel, ConfigDict, Field

from photoproof.contracts.enums import AnalysisMode, RunStatus


class InputDescriptor(BaseModel):
    """Identity of one input file. Bytes are never embedded in contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(pattern=r"^(target|reference)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    media_format: str | None = None  # e.g. "jpeg"; None until format detection exists


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: InputDescriptor
    reference: InputDescriptor | None = None
    profile: str | None = None  # None = settings.profiles.default
    use_cache: bool = True


class PluginSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    state: str
    failure_category: str | None = None
    message: str | None = None


class AnalysisResult(BaseModel):
    """What a run produced. It deliberately carries no authenticity verdict field:
    evidence fusion is not implemented, and absent detector output is not evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: RunStatus
    mode: AnalysisMode
    profile: str
    cache_hit: bool
    plugins: tuple[PluginSummary, ...]
    outputs: dict[str, object]
    blocked_reasons: tuple[str, ...] = ()
    manifest_path: str | None = None
