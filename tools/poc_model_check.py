"""M0 → M2 gate: verify required OpenAI model IDs exist.

Run BEFORE starting M2 (image PoC). This avoids burning real $$ only to
discover the model name is wrong.

Behavior:
    - Reads OPENAI_API_KEY from .env via python-dotenv (or env).
    - Calls openai.models.retrieve() for each ID in MODELS_TO_CHECK.
    - Does NOT call generations / responses endpoints (no token / image cost).
    - Exits 0 on full success; 1 if any model is missing or auth fails.

Usage:
    uv run python tools/poc_model_check.py
"""

from __future__ import annotations

import os
import sys

MODELS_TO_CHECK: tuple[str, ...] = (
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-image-2",
)


def main() -> int:
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
    except ImportError:
        load_dotenv = None

    if load_dotenv is not None:
        load_dotenv()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set (.env or environment).", file=sys.stderr)
        return 1

    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai package not installed. Run `uv sync --group dev`.", file=sys.stderr)
        return 1

    client = OpenAI(api_key=api_key)

    failures: list[str] = []
    for model_id in MODELS_TO_CHECK:
        try:
            info = client.models.retrieve(model_id)
            print(f"OK   {model_id}  owned_by={info.owned_by!r}  created={info.created}")
        except Exception as exc:
            print(f"FAIL {model_id}  {type(exc).__name__}: {exc}", file=sys.stderr)
            failures.append(model_id)

    if failures:
        print(
            f"\n{len(failures)} model(s) missing: {failures}. "
            "Update [models] / [image_provider] in config.toml before running M2.",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {len(MODELS_TO_CHECK)} models reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
