"""Shared test helpers — fixture builders for layer tests.

Not a test file (no `test_` prefix). Importable from individual test modules.
"""

from __future__ import annotations

from pathlib import Path

from mangaka.config import (
    GeneralConfig,
    ImageBudgetConfig,
    ImageProviderConfig,
    LayerConfig,
    LayersConfig,
    LimitsConfig,
    LLMProviderConfig,
    MangakaConfig,
    ModelsConfig,
    PdfConfig,
    RetryConfig,
)


def make_test_config(*, runs_dir: str = "runs") -> MangakaConfig:
    """A self-consistent `MangakaConfig` suitable for unit tests.

    Mirrors the defaults in `docs/ARCHITECTURE.md` §設定 but stays minimal —
    smaller retry budget, no real models needed.
    """
    layer = lambda: LayerConfig(
        model="test-model",
        temperature=0.7,
        max_tokens=1024,
        thinking=False,
    )
    mpbv_layer = LayerConfig(
        model="test-thinking",
        temperature=0.7,
        max_tokens=1024,
        thinking=True,
        reasoning_effort="high",
    )
    page_plan_layer = LayerConfig(
        model="test-thinking",
        temperature=0.7,
        max_tokens=1024,
        thinking=True,
        reasoning_effort="medium",
    )
    return MangakaConfig(
        general=GeneralConfig(runs_dir=runs_dir),
        llm_provider=LLMProviderConfig(),
        image_provider=ImageProviderConfig(),
        pdf=PdfConfig(),
        limits=LimitsConfig(max_pages=8),
        image=ImageBudgetConfig(),
        models=ModelsConfig(),
        retry=RetryConfig(max_retries=0, initial_delay=0.0, max_delay=0.0),
        layers=LayersConfig(
            plot=layer(),
            backstory=layer(),
            mpbv=mpbv_layer,
            stylist=layer(),
            character=layer(),
            location=layer(),
            page_plan=page_plan_layer,
        ),
    )


def prompts_dir() -> Path:
    """Path to the real `prompts/` directory shipped with the repo."""
    return Path(__file__).resolve().parent.parent / "prompts"
