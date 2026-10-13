"""Evidence-backed model description shared with static exports."""

from typing import Literal

from pydantic import BaseModel, Field


class ModelMetadata(BaseModel):
    schema_version: Literal[1] = 1
    model_digest: str | None = None
    source_model_digest: str | None = None
    training_range: dict[str, list[float]] | None = None
    training_range_status: Literal["known", "unknown"] = "unknown"
    training_source: Literal["measured", "simulation", "mixed", "unknown"] = "unknown"
    source_evidence: str | None = None
    simulation_only_areas: list[float] = Field(default_factory=list)
    validation_metrics: dict[str, float | int | None] | None = None
    validation_dataset_digest: str | None = None
    validation_status: Literal["verified", "unverified", "unknown"] = "unknown"
    validation_summary: dict[str, int] | None = None
    applicability: Literal["within_range", "out_of_range", "unknown"] = "unknown"
