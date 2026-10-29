from typing import Literal

from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior: Literal[
        "ok", "raise", "invalid_output", "not_json", "sleep", "interrupt", "not_applicable"
    ] = "ok"
