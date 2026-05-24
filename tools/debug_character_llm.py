"""Dump the raw character-layer LLM output for a run to count what characters
the LLM proposes, without spending the per-character image $0.21.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from mangaka.config import RetryConfig, load_config
from mangaka.llm.client_openai import OpenAILLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.persistence import load_state
from mangaka.result import Failure


def main() -> int:
    load_dotenv()
    run_dir = Path(sys.argv[1])
    config = load_config(run_dir / "config.toml").unwrap()
    state = load_state(run_dir / "state_04_stylist.json").unwrap()
    if state.mpbv is None or state.stylist is None:
        print("missing mpbv/stylist", file=sys.stderr)
        return 1

    retry_cfg = RetryConfig(**config.retry.model_dump()).model_copy(
        update={"max_retries": config.limits.max_retries}
    )
    llm = OpenAILLMClient(default_model=config.models.default, retry_config=retry_cfg)
    loader = PromptLoader()

    prompt = loader.render(
        "05_character.md",
        mpbv=state.mpbv.raw_markdown,
        stylist=state.stylist.raw_markdown,
        max_main_characters=config.limits.max_main_characters,
    ).unwrap()

    layer = config.layers.character
    result = llm.complete(
        prompt,
        model=layer.model,
        temperature=layer.temperature,
        max_tokens=layer.max_tokens,
        thinking=layer.thinking,
        reasoning_effort=layer.reasoning_effort,
    )
    if isinstance(result, Failure):
        print(f"LLM failed: {result.failure().message}", file=sys.stderr)
        return 1
    output = result.unwrap()
    out_path = run_dir / "debug_character_raw.md"
    out_path.write_text(output, encoding="utf-8")
    print(f"i wrote raw output to {out_path} ({len(output)} chars)")
    # Count H2 character headers
    headers = re.findall(r"^## ([^\n(]+)\s*\(([^)]+)\)", output, re.MULTILINE)
    print(f"\nDetected {len(headers)} characters:")
    for name, cid in headers:
        print(f"  {cid.strip()}: {name.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
