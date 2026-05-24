"""One-shot debug: dump the raw location-layer LLM output to see what
format-drift caused `parse_location_markdown` to find zero `## Name (id)`
headers. Run after a failed location layer.
"""

from __future__ import annotations

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
    config = load_config(Path("config_poc.toml")).unwrap()
    state = load_state(run_dir / "state_05_character.json").unwrap()
    if state.mpbv is None or state.stylist is None:
        print("state missing mpbv or stylist", file=sys.stderr)
        return 1

    retry_cfg = RetryConfig(**config.retry.model_dump()).model_copy(
        update={"max_retries": config.limits.max_retries}
    )
    llm = OpenAILLMClient(default_model=config.models.default, retry_config=retry_cfg)
    loader = PromptLoader()

    prompt = loader.render(
        "06_location.md",
        mpbv=state.mpbv.raw_markdown,
        stylist=state.stylist.raw_markdown,
        max_locations=config.limits.max_locations,
    ).unwrap()

    layer = config.layers.location
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
    dump_path = run_dir / "debug_location_raw.md"
    dump_path.write_text(output, encoding="utf-8")
    print(f"i wrote raw output to {dump_path} ({len(output)} chars)")
    print("\n=== first 80 lines ===")
    for line in output.splitlines()[:80]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
