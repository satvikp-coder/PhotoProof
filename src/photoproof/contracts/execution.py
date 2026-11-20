"""Host-provided execution facts, never inferred from missing numerical outputs."""

from typing import Literal, NotRequired, TypedDict


class UpstreamOutputStatus(TypedDict):
    """State of a planned output producer at the consuming plugin's invocation."""

    plugin_id: str
    state: str
    failure_category: str | None


class UpstreamStatus(TypedDict):
    """Absent entries mean not in the plan; PENDING is explicitly not a result."""

    format_version: Literal[1]
    outputs: dict[str, UpstreamOutputStatus]
    required_prior_outputs: NotRequired[list[str]]
