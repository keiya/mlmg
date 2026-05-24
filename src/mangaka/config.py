"""Pydantic configuration model loaded from TOML.

`load_config(path)` reads a TOML file via stdlib `tomllib` and validates it
into a `MangakaConfig`. All validation lives here (e.g. ref budget >= 2).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = "ja"
    runs_dir: str = "runs"


class LLMProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai"] = "openai"


class ImageProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai"] = "openai"
    model: str = "gpt-image-2"
    default_size: str = "1024x1536"
    quality: str = "high"


class PdfConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_size: Literal["A5", "B5", "A4"] = "A5"
    # `fit` is currently fixed to "contain" — export_pdf does not implement
    # cover-mode cropping yet. ARCHITECTURE.md keeps `cover` as a v2 candidate
    # since "コマが切れるのは致命" for manga; accepting it in config would be
    # a silent no-op. Re-add when export_pdf gains a cover branch.
    fit: Literal["contain"] = "contain"
    binding: Literal["rtl", "ltr"] = "rtl"
    # PoC 2026-05-24: PNG-embedded PDFs were ~16 MB for 4 pages, too heavy
    # for sharing / Kindle / multi-page work. JPEG q=85 cuts size ~9× with
    # no visible quality loss on gpt-image-2 output. PNG remains opt-in via
    # config for users who want lossless.
    image_format: Literal["jpeg", "png"] = "jpeg"
    jpeg_quality: int = Field(default=85, ge=1, le=100)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_pages: int = Field(default=24, ge=1)
    max_arc_phases: int = Field(default=5, ge=1)
    max_panels_per_page: int = Field(default=8, ge=1)
    max_main_characters: int = Field(default=8, ge=1)
    max_locations: int = Field(default=6, ge=1)
    max_retries: int = Field(default=3, ge=0)
    max_image_retries: int = Field(default=2, ge=0)
    max_parse_retries: int = Field(default=2, ge=0)


class AssetsConfig(BaseModel):
    """Per-asset sheet counts.

    v1 only generates one sheet per character/location; both fields are
    constrained to 1. The character/location layers don't consume these
    values at all — values >1 in earlier drafts of config would have been
    silent no-ops, which is worse than a load-time rejection. ARCH lists
    multi-sheet variants as a v2 candidate.
    """

    model_config = ConfigDict(extra="forbid")
    character_sheets_per_char: int = Field(default=1, ge=1, le=1)
    location_sheets_per_loc: int = Field(default=1, ge=1, le=1)


class ImageBudgetConfig(BaseModel):
    """Budget guards for PageRender prompt assembly.

    `max_refs_per_page` must be >= 2: at minimum we need a style ref plus
    one content ref (loc / char / prev). Below that the labeled-ref scheme
    degenerates.
    """

    model_config = ConfigDict(extra="forbid")
    max_refs_per_page: int = Field(default=16, ge=2, le=16)
    # Default flipped to False after the 8-page PoC (2026-05-24). Including
    # the previous page as a ref produced visible "loop" artifacts — adjacent
    # pages copied panel layout, character poses, and bubble positions from
    # the prior page. Style / character / location consistency are already
    # carried by style.png + char sheets + location sheets, so the prev-page
    # ref was over-engineering. As a bonus, with no inter-page dependency,
    # page renders are now embarrassingly parallel — see PLAN.md.
    include_prev_page_ref: bool = False
    max_prompt_chars: int = Field(default=20000, ge=1)
    warn_prompt_chars: int = Field(default=12000, ge=1)
    max_location_summary_chars: int = Field(default=600, ge=1)
    max_character_summary_chars: int = Field(default=300, ge=1)
    max_character_summary_total_chars: int = Field(default=1500, ge=1)

    @model_validator(mode="after")
    def _warn_below_hard_limit(self) -> ImageBudgetConfig:
        if self.warn_prompt_chars > self.max_prompt_chars:
            raise ValueError("image.warn_prompt_chars must be <= image.max_prompt_chars")
        if self.max_character_summary_chars > self.max_character_summary_total_chars:
            raise ValueError(
                "image.max_character_summary_chars must be "
                "<= image.max_character_summary_total_chars"
            )
        return self


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str = "gpt-5.4-mini"
    validation: str = "gpt-5.4"
    naming: str = "gpt-5.4-mini"


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_retries: int = Field(default=3, ge=0)
    initial_delay: float = Field(default=1.0, ge=0.0)
    max_delay: float = Field(default=60.0, ge=0.0)
    exponential_base: float = Field(default=2.0, gt=1.0)
    # ±jitter_ratio uniform multiplicative jitter on each retry delay. 0.0
    # disables jitter (deterministic backoff). 0.25 means each computed delay
    # is multiplied by a uniform sample in [0.75, 1.25]. Mitigates retry
    # storms when N parallel workers simultaneously hit 429.
    jitter_ratio: float = Field(default=0.25, ge=0.0, le=1.0)


class ConcurrencyConfig(BaseModel):
    """Parallel-execution knobs for image generation.

    `image_workers` bounds the `ThreadPoolExecutor` width used by
    `image/parallel.py` for page_render, character sheet, and location
    sheet generation. Default sized for OpenAI Tier 5 (IPM=250) at ~7.7%
    steady-state utilization given gpt-image-2's ~50s/job latency,
    leaving 5x headroom for retry storms and concurrent runs. Drop to 1
    for serial debugging.

    Upper bound is a soft architectural ceiling: a single mangaka run
    has at most ~32 image calls (24 pages + 8 sheets), so values >64
    just leave workers idle. Tier 6+ users may want to lift this — bump
    the cap rather than tuning around it.
    """

    model_config = ConfigDict(extra="forbid")
    image_workers: int = Field(default=16, ge=1, le=256)


class LayerConfig(BaseModel):
    """Per-layer LLM settings.

    `reasoning_effort` is the OpenAI Responses-API knob, used only when
    `thinking=True`. Anthropic-style token budgets do not apply.
    """

    model_config = ConfigDict(extra="forbid")
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    thinking: bool = False
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None

    @model_validator(mode="after")
    def _check_reasoning_effort(self) -> LayerConfig:
        if self.reasoning_effort is not None and not self.thinking:
            raise ValueError("reasoning_effort is only valid when thinking=True")
        if self.thinking and self.reasoning_effort is None:
            raise ValueError(
                "thinking=True requires an explicit reasoning_effort "
                "(minimal/low/medium/high) — defaults are not safe"
            )
        return self


class LayersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plot: LayerConfig
    backstory: LayerConfig
    mpbv: LayerConfig
    stylist: LayerConfig
    character: LayerConfig
    location: LayerConfig
    page_plan: LayerConfig

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_page_beat(cls, data: object) -> object:
        # PageBeat layer was removed in commit 71f9119 (2026-05-24) but
        # pre-existing run dirs snapshotted a config containing
        # `[layers.page_beat]`. `mangaka export <old_run>` loads that
        # snapshot; with extra="forbid" the legacy key would now break
        # the export of every previously-generated run. Drop it silently
        # rather than make every user re-edit their run snapshots.
        if not isinstance(data, dict):
            return data
        data_dict = cast("dict[str, object]", data)
        data_dict.pop("page_beat", None)
        return data_dict


class MangakaConfig(BaseModel):
    """Top-level config matching the schema in ARCHITECTURE.md §設定."""

    model_config = ConfigDict(extra="forbid")
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    llm_provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    image_provider: ImageProviderConfig = Field(default_factory=ImageProviderConfig)
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    assets: AssetsConfig = Field(default_factory=AssetsConfig)
    image: ImageBudgetConfig = Field(default_factory=ImageBudgetConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    layers: LayersConfig

    @model_validator(mode="after")
    def _check_parallel_vs_prev_page_ref(self) -> MangakaConfig:
        # The parallel page_render layer pre-builds every job's refs
        # BEFORE any worker runs, so page N's prompt cannot see page
        # N-1's freshly-rendered image. include_prev_page_ref=True would
        # silently degrade to "no prev ref" — better to loud-fail at
        # config load. Drop to image_workers=1 (still uses the executor
        # but serial) if you genuinely need prev_page refs.
        if self.image.include_prev_page_ref and self.concurrency.image_workers > 1:
            raise ValueError(
                "image.include_prev_page_ref=True is incompatible with "
                "concurrency.image_workers > 1: parallel page_render cannot "
                "feed page N-1's freshly-rendered image to page N. Either "
                "set image_workers=1 or include_prev_page_ref=false."
            )
        return self


def load_config(path: Path) -> Result[MangakaConfig, MangaError]:
    """Read a TOML file and validate it into a `MangakaConfig`.

    All validation errors are wrapped into `Failure(MangaError)` with
    `ErrorKind.CONFIG_ERROR`.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to read config file: {path}",
                detail={"path": str(path), "errno": exc.errno},
            )
        )

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message=f"invalid TOML in config: {exc}",
                detail={"path": str(path)},
            )
        )

    try:
        cfg = MangakaConfig.model_validate(data)
    except ValidationError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message=f"config validation failed: {exc}",
                detail={"path": str(path)},
            )
        )
    return Success(cfg)


__all__ = [
    "AssetsConfig",
    "ConcurrencyConfig",
    "GeneralConfig",
    "ImageBudgetConfig",
    "ImageProviderConfig",
    "LLMProviderConfig",
    "LayerConfig",
    "LayersConfig",
    "LimitsConfig",
    "MangakaConfig",
    "ModelsConfig",
    "PdfConfig",
    "RetryConfig",
    "load_config",
]
