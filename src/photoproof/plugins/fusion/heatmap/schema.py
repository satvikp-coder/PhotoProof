"""Display settings do not change forensic scores or the segmentation mask."""

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Provisional display threshold and fixed blend opacity."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    display_threshold: float = Field(default=0.3, ge=0, le=1)
    opacity: float = Field(default=0.5, ge=0, le=1)
