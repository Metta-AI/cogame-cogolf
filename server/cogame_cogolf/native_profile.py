"""Immutable game-owned native policy configuration for each external seat."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

DEFAULT_MODEL = "anthropic/claude-haiku-4.5"


class NativeProfile(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    profile: Literal["cogolf_native_text_v1"] = "cogolf_native_text_v1"
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=256)
    temperature: float = Field(default=0.7, strict=True, ge=0, le=1)
    max_tokens: StrictInt = Field(default=1800, gt=0, le=8192)
    top_p: StrictInt = Field(default=1, ge=1, le=1)
    strategy: str = Field(default="", max_length=12000)
