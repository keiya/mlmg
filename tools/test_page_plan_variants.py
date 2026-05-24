"""Run page_plan with several prompt variants against the SAME upstream state.

Goal: find a prompt that doesn't pad repeated beats across pages. Each
variant is rendered as plain text (no Jinja templating), fed to gpt-5.4,
and parsed. Results are written to `runs/{name}/debug_page_plan_v{N}.json`
plus a `debug_page_plan_compare.md` summary.

Run pre-requisite: a run-dir that's reached at least location layer
(state_06_location.json present).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from mangaka.config import RetryConfig, load_config
from mangaka.llm.client_openai import OpenAILLMClient
from mangaka.parse.page_plan import parse_page_plan_text
from mangaka.persistence import load_state
from mangaka.result import Failure, Result


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    body: str  # full prompt text (already includes inputs)


def _id_block(items: list[tuple[str, str]]) -> str:
    return "\n".join(f"- `{i}` — {n}" for i, n in items)


def _build_inputs(state, config):
    char_block = _id_block([(c.id, c.name) for c in state.characters])
    loc_block = _id_block([(loc.id, loc.name) for loc in state.locations])
    return {
        "mpbv": state.mpbv.raw_markdown,
        "stylist": state.stylist.raw_markdown,
        "character_ids_block": char_block,
        "location_ids_block": loc_block,
        "max_pages": config.limits.max_pages,
        "max_arc_phases": config.limits.max_arc_phases,
    }


_COMMON_HEADER = """あなたは漫画の構成作家です。MPBV、Style Guide、登場キャラクター、舞台ロケーションを踏まえて、この短編漫画の **PagePlan** を JSON で出力してください。

PagePlan は次の 2 つで構成されます:

1. `arc` — 起承転結のフェーズ分割 (典型 3〜5 個)
2. `page_outline` — 各ページの軽い outline (全 `total_pages` ぶん、ページ番号順)

# Input: MPBV (final)

{mpbv}

---

# Input: Style Guide (narrative sections に注目)

{stylist}

---

# Input: 登場キャラクター ID 一覧

{character_ids_block}

---

# Input: 舞台ロケーション ID 一覧

{location_ids_block}

---
"""

_COMMON_CONSTRAINTS = """
- 総ページ数 `total_pages` は **{max_pages} 以下**
- `arc` の長さは **{max_arc_phases} 以下**
- ページは 1 から始まる連番、歯抜けなし
- `arc` 全体で隣接フェーズの `end_page + 1 == 次の start_page`、最初の `start_page == 1`、最後の `end_page == total_pages`
- 各 `page_outline[*].phase` は `arc[*].phase` のいずれかと **完全一致**する文字列
- 各 `page_outline[*].character_ids` は上に列挙された **既存のキャラ ID** のみ使用
- 各 `page_outline[*].location_id` は上に列挙された **既存のロケ ID** のみ使用
- 出力は **JSON のみ**、コードフェンス（```json ... ```）に包んで返してください。前後に解説テキストは不要です
"""

_EXAMPLE = """
# 出力フォーマット (例)

```json
{{
  "total_pages": 6,
  "arc": [
    {{"phase": "セットアップ", "start_page": 1, "end_page": 2, "summary": "..."}},
    {{"phase": "対立", "start_page": 3, "end_page": 4, "summary": "..."}},
    {{"phase": "クライマックス", "start_page": 5, "end_page": 5, "summary": "..."}},
    {{"phase": "結末", "start_page": 6, "end_page": 6, "summary": "..."}}
  ],
  "page_outline": [
    {{"page_number": 1, "phase": "セットアップ", "summary": "...", "character_ids": ["alice"], "location_id": "rooftop"}}
  ]
}}
```
"""


def variants_for(state, config) -> list[Variant]:
    inputs = _build_inputs(state, config)
    header = _COMMON_HEADER.format(**inputs)
    constraints = _COMMON_CONSTRAINTS.format(**inputs)
    example = _EXAMPLE  # contains literal "{...}" already escaped

    # ------------------------------------------------------------------
    # V1: control — current production prompt
    # ------------------------------------------------------------------
    v1_body = (
        header
        + "# 制約\n"
        + constraints
        + example
        + "\n短編としての密度と「ちょいおもろい」テンポを意識して、各ページに 1〜2 ビート程度を割り当ててください。\n"
    )

    # ------------------------------------------------------------------
    # V2: soft-limit + no-padding rule
    # ------------------------------------------------------------------
    v2_body = (
        header
        + "# 制約\n"
        + constraints
        + example
        + f"\n**重要: `total_pages = {inputs['max_pages']}` を必ず使う必要はありません**。"
        + f"ストーリーが必要とするビート数に応じて {inputs['max_pages']} 以下の自然な数を選んでください。"
        + "**前のページで描いたビートの言い換えで紙数を埋めるのは禁止** — 各ページは必ず distinct な進展を含むこと。"
        + "ストーリーが 5 ビートで完結するなら 5 ページで終わらせて構いません。\n"
    )

    # ------------------------------------------------------------------
    # V3: V2 + explicit "distinct beat per page" + advance/contrast checklist
    # ------------------------------------------------------------------
    v3_body = (
        header
        + "# 制約\n"
        + constraints
        + example
        + f"\n**ページ数の選び方**: `total_pages` は **ストーリーが本当に持つ distinct な beat 数** に合わせる ({inputs['max_pages']} 以下)。"
        + "padding 禁止。\n\n"
        + "**各ページに必須**: 前ページに無い**新情報** (新事実 / 新感情 / 新行動 / 新場所 / 新発見) のいずれかを 1 つ以上含む。"
        + "「観察」と「説明」を別ページに分ける、「同じ行動を別の場所でもう一度やる」は禁止。\n\n"
        + "**自己チェック**: 出力前に、隣接ページ ({{n, n+1}}) を比べて「言い換えではない、本当に新しい beat が n+1 にある」ことを確認してください。\n"
    )

    # ------------------------------------------------------------------
    # V4: two-pass — first count distinct beats, THEN map to pages
    # ------------------------------------------------------------------
    v4_body = (
        header
        + "# 出力前の思考プロセス\n\n"
        + "1. MPBV を読み、このストーリーに含まれる **distinct な beat (場面・転換・発見・感情変化)** を箇条書きで数える\n"
        + "2. ビート数 K を確定する (典型 4-10 個)\n"
        + f"3. `total_pages = min(K, {inputs['max_pages']})` で確定。**K より多くのページにはしない**\n"
        + "4. 各 beat を 1 ページに割り当てる (場合によっては 1 beat = 2 ページの「ためコマ」も可、ただしこの場合は 2 ページで明確に異なる視点を持つこと)\n"
        + "5. arc 分割を beat 数に合わせる (起承転結を 1+2+3+1 など可変)\n\n"
        + "# 制約\n"
        + constraints
        + example
        + "\n出力 JSON だけ返してください。思考プロセスの中間結果は不要です。\n"
    )

    # ------------------------------------------------------------------
    # V5: explicit override of MPBV's page distribution proposal
    # ------------------------------------------------------------------
    v5_body = (
        header
        + "# **MPBV のページ配分提案は無視する**\n\n"
        + "MPBV 内の「ページ配分の案」「全 N ページ」「起 X-Y / 承 X-Y / ...」のような **ページ数指定は単なる初稿の目安** であり、しばしば実際の beat 数より多めに膨らんでいます。\n"
        + "あなたの責任は **MPBV のページ提案を捨て** て、ストーリーが本当に必要とする distinct な beat を数え直し、それを適切なページ数に割り当てることです。\n\n"
        + f"特に MPBV が「全 N ページ」と提案していても、それを無視して `total_pages` を **{inputs['max_pages']} 以下** で **本当に必要な数** に絞ってください (典型 4-7 ページ)。padding 禁止。\n\n"
        + "# 制約\n"
        + constraints
        + example
    )

    # ------------------------------------------------------------------
    # V6: programmatically strip MPBV's page-distribution section from input
    # ------------------------------------------------------------------
    import re
    stripped_mpbv = re.sub(
        r"## 物語構造.*?(?=^## |\Z)",
        "",
        inputs["mpbv"],
        flags=re.MULTILINE | re.DOTALL,
    )
    stripped_inputs = {**inputs, "mpbv": stripped_mpbv}
    v6_header = _COMMON_HEADER.format(**stripped_inputs)
    v6_constraints = _COMMON_CONSTRAINTS.format(**stripped_inputs)
    v6_body = (
        v6_header
        + "# 制約\n"
        + v6_constraints
        + example
        + f"\n**ページ数の選び方**: ストーリーが必要とする distinct な beat 数に合わせて、{inputs['max_pages']} 以下の自然な数を選んでください。"
        + "padding 禁止 — 隣接ページが互いの言い換えにならないこと。\n"
    )

    # ------------------------------------------------------------------
    # V7: V5 + V3 — MPBV override AND distinct-beat self-check
    # ------------------------------------------------------------------
    v7_body = (
        header
        + "# **MPBV のページ配分提案は無視する**\n\n"
        + "MPBV 内の「ページ配分の案」「全 N ページ」のような **ページ数指定は初稿の目安にすぎず、しばしば実際の beat 数より多めに膨らんでいます**。盲信しないでください。\n\n"
        + "あなたの責任は、ストーリーが本当に必要とする distinct な beat を数え直し、それを適切なページ数に割り当てることです。\n\n"
        + "**各ページに必須**: 前ページに無い**新情報** (新事実 / 新感情 / 新行動 / 新場所 / 新発見) のいずれかを 1 つ以上含む。"
        + "「観察」と「説明」を別ページに分ける、「同じ行動を別の場所でもう一度やる」は禁止。\n\n"
        + "**自己チェック**: 出力前に、隣接ページ ({{n, n+1}}) を比べて「言い換えではない、本当に新しい beat が n+1 にある」ことを確認してください。padding に気づいたら統合 (delete + merge) してください。\n\n"
        + "# 制約\n"
        + constraints
        + example
    )

    # ------------------------------------------------------------------
    # V8: V5 + soft target (典型 5-6 ページ)
    # ------------------------------------------------------------------
    v8_body = (
        header
        + "# **MPBV のページ配分提案は無視する**\n\n"
        + "MPBV 内のページ配分指定は無視してください。\n\n"
        + f"**目安**: 8 ページ短編のシード入力でも、実際に distinct な beat があるのは典型 5-7 ページ分です。{inputs['max_pages']} 上限ギリギリまで使う必要はありません。`total_pages` は **5-6 ページ程度を target** にしつつ、ストーリーが本当に必要なら 7-8 まで許容、で考えてください。\n\n"
        + "# 制約\n"
        + constraints
        + example
    )

    return [
        Variant("v1_control", "V1 control (現行)", v1_body),
        Variant("v2_soft_no_padding", "V2 soft + no-padding", v2_body),
        Variant("v3_distinct_beat", "V3 distinct beat + self-check", v3_body),
        Variant("v4_two_pass", "V4 two-pass: count beats first", v4_body),
        Variant("v5_override_mpbv", "V5 explicit override of MPBV page proposal", v5_body),
        Variant("v6_strip_mpbv_section", "V6 strip MPBV 物語構造 section", v6_body),
        Variant("v7_override_plus_distinct", "V7 V5 + V3 (override + distinct beat)", v7_body),
        Variant("v8_override_plus_soft_target", "V8 V5 + soft target 5-6 pages", v8_body),
    ]


async def _run_variant(
    variant: Variant, llm: OpenAILLMClient, config, state
) -> tuple[Variant, str, Result]:
    layer = config.layers.page_plan
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: llm.complete(
            variant.body,
            model=layer.model,
            temperature=layer.temperature,
            max_tokens=layer.max_tokens,
            thinking=layer.thinking,
            reasoning_effort=layer.reasoning_effort,
        ),
    )
    if isinstance(result, Failure):
        return variant, "", result
    raw = result.unwrap()
    parsed = parse_page_plan_text(
        raw,
        max_pages=config.limits.max_pages,
        max_arc_phases=config.limits.max_arc_phases,
        known_character_ids=[c.id for c in state.characters],
        known_location_ids=[loc.id for loc in state.locations],
    )
    return variant, raw, parsed


async def main_async(run_dir: Path) -> int:
    load_dotenv()
    config = load_config(run_dir / "config.toml").unwrap()
    state = load_state(run_dir / "state_06_location.json").unwrap()

    retry_cfg = RetryConfig(**config.retry.model_dump()).model_copy(
        update={"max_retries": config.limits.max_retries}
    )
    llm = OpenAILLMClient(default_model=config.models.default, retry_config=retry_cfg)

    vs = variants_for(state, config)
    print(f"i running {len(vs)} variants in parallel against state_06_location ...")

    tasks = [_run_variant(v, llm, config, state) for v in vs]
    results = await asyncio.gather(*tasks)

    summary_lines: list[str] = ["# Page-plan variant comparison\n"]
    for variant, raw, parsed in results:
        out_raw = run_dir / f"debug_page_plan_{variant.key}_raw.txt"
        out_raw.write_text(raw, encoding="utf-8")
        if isinstance(parsed, Failure):
            print(f"✗ {variant.key}: parse failed — {parsed.failure().message}")
            summary_lines.append(f"## {variant.label} — **PARSE FAILED**\n\n{parsed.failure().message}\n")
            continue
        plan = parsed.unwrap()
        out_json = run_dir / f"debug_page_plan_{variant.key}.json"
        out_json.write_text(
            json.dumps(
                {
                    "total_pages": plan.total_pages,
                    "arc": [
                        {"phase": a.phase, "start_page": a.start_page, "end_page": a.end_page, "summary": a.summary}
                        for a in plan.arc
                    ],
                    "page_outline": [
                        {
                            "page_number": po.page_number,
                            "phase": po.phase,
                            "summary": po.summary,
                            "character_ids": list(po.character_ids),
                            "location_id": po.location_id,
                        }
                        for po in plan.page_outline
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"✓ {variant.key}: total_pages={plan.total_pages} "
            f"arc={len(plan.arc)} → {out_json}"
        )
        summary_lines.append(
            f"## {variant.label} — total_pages={plan.total_pages}\n"
        )
        summary_lines.append("Arc phases:")
        for a in plan.arc:
            summary_lines.append(
                f"- {a.phase} ({a.start_page}-{a.end_page}): {a.summary}"
            )
        summary_lines.append("\nPage outlines:\n")
        for po in plan.page_outline:
            summary_lines.append(
                f"**page {po.page_number} [{po.phase}]** @ {po.location_id}: {po.summary[:200]}"
            )
            summary_lines.append("")

    summary_path = run_dir / "debug_page_plan_compare.md"
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"✓ wrote {summary_path}")
    return 0


def main() -> int:
    run_dir = Path(sys.argv[1])
    return asyncio.run(main_async(run_dir))


if __name__ == "__main__":
    raise SystemExit(main())
