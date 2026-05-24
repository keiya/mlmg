# mangaka Implementation Plan

実装の**順序・スキャフォールド・マイルストーン**を扱う。アーキ・スキーマは `ARCHITECTURE.md` / `SCHEMA.md`、コーディング規約・harness 規律は `CLAUDE.md`。本書はそれらを前提に「次は何をどの順で書くか」を決める。

---

## 1. スコープ

| 項目 | 担当ドキュメント |
|---|---|
| レイヤー構造・ドメイン型・依存・PDF 出力仕様 | `docs/ARCHITECTURE.md` |
| 各レイヤーの I/O スキーマ詳細 | `docs/SCHEMA.md` |
| Result discipline・型注釈・テスト方針・hooks・Asset Immutability | `CLAUDE.md` |
| **実装順序・マイルストーン・dependency list・PoC 計画** | **本書 (PLAN.md)** |

実装中に重複が見つかったら、本書は削って ARCH/SCHEMA/CLAUDE 側に寄せる。

---

## 2. Tech Stack

mlsg2 / aurora-intellica の慣行を踏襲、Anthropic は v1 では使わない。

| 領域 | 採用 |
|---|---|
| Python | **3.12+** |
| パッケージ管理 | **uv** (`uv sync --group dev`, `uv run ...`) |
| Result 型 | `returns` ライブラリ (`returns.result.Result`) — mlsg2 と同じ |
| Lint / Format | `ruff` (line=100, target=py312, lints F/I/UP/B/SIM/RUF/N/PT/C90) |
| 型検査 | `pyright` strict mode |
| テスト | `pytest` (markers: `smoke`, `integration`) |
| LLM SDK | `openai>=2.0` のみ（gpt-5.4-mini 等 mainline models） |
| 画像 SDK | 同 `openai` (`/v1/images/{generations,edits}`) |
| Prompt template | `jinja2` (mlsg2 と同じ) |
| 構造化 I/O | `pydantic>=2.0` |
| Markdown + frontmatter | `python-frontmatter` |
| ロギング | `structlog` + `rich` |
| PDF 出力 | `reportlab` + `pillow` |
| `.env` ロード | `python-dotenv` |

### LLM model 設定

`config.toml` で全レイヤーのモデルを変更可能（mlsg2 と同パターン）:

- デフォルト: `gpt-5.4-mini` (or `gpt-5-mini` 系の安価で高速なやつ)
- `[models].validation` で MPBV 用に別モデル（例: `gpt-5.4` 等の thinking 系）を指定可能
- `[layers.*].model` で各層を個別に override 可能

mlsg2 が Anthropic 専用だった部分は `OpenAILLMClient` 単一実装に置き換える。`LLMClient` Protocol は維持して将来差し替え可能に。

---

## 3. マイルストーン

各マイルストーンは **動く** ことをゴールとする。完成度より end-to-end の通電を優先。

### M0 — Skeleton

スコープ:
- `pyproject.toml`, `pyrightconfig.json`, `.gitignore`, `.env.example`
- `src/mangaka/__init__.py`, `__main__.py`, `cli.py` (`--version` だけ)
- `src/mangaka/result.py`, `errors.py` (`MangaError`)
- `src/mangaka/domain.py` (ARCHITECTURE.md §ドメイン型 を全部書く)
- `src/mangaka/config.py` (`tomllib` 読み込み + Pydantic validation、`image.max_refs_per_page >= 2` 等の検証ここで実装)
- `tests/` レイアウトは **flat** で開始 (`tests/test_smoke.py`, `tests/test_domain.py` 等)。サブディレクトリ化は必要になってから
- `tests/test_smoke.py` (import + `mangaka --version`)
- `.claude/hooks/` (3 つ: pre-edit-protect-config, post-edit-lint, stop-smoke-test) — aurora-intellica からポート
- **M0 末で OpenAI モデル ID 存在を確認**: `tools/poc_model_check.py` を 1 本書いて `openai.models.retrieve("gpt-5.4-mini")` / `retrieve("gpt-image-2")` で ID 検証（実呼び出しではなく metadata だけ）。404 が出たら名前を確認して config を直す。M2 PoC 前の必須 gate。**実際の画像生成や Responses API 呼び出しの smoke は M1 (LLM) と M2 (画像) でそれぞれ実施**。Responses API は `max_output_tokens`、Images API は `size`/`quality` とパラメータが違うので、共通の「1-call ping」では括らない

Deliverable: `uv run mangaka --version` が動く。`pytest -m smoke` が通る。`uv run pyright` が cleancly 通る。`tools/poc_model_check.py` で model 名が実在する確認済み。

### M1 — Text-only パイプライン

スコープ:
- `src/mangaka/llm/client.py` (`LLMClient` Protocol)
- `src/mangaka/llm/client_openai.py` (`OpenAILLMClient` 実装、`/v1/responses` 経由)
  - **`thinking=True` → `reasoning_effort` mapping は M1 の非自明タスク**。mlsg2 の Anthropic `thinking_budget` (token 数) と概念が違うので、layer config の `reasoning_effort` (`"high"`/`"medium"`/`"low"`/`"minimal"`) をそのまま渡す形にする
- `src/mangaka/llm/client_fake.py` (`FakeLLMClient`、固定文字列を返す test ダブル)
- `src/mangaka/llm/prompts.py` (jinja2 template loader)
- `src/mangaka/llm/retry.py` (exponential backoff)
- `src/mangaka/persistence.py` (state JSON のシリアライズ/デシリアライズ) — **layer 移植より先**に書く。Plot/Backstory/MPBV layer が `save_state` を呼ぶため
- `prompts/01_master_plot.md` (mlsg2 から流用)
- `prompts/02_backstory.md` (流用)
- `prompts/03_mpbv.md` (流用)
- `src/mangaka/layers/plot.py`, `backstory.py`, `mpbv.py` (mlsg2 のロジック移植、`LLMClient` 経由)
- `src/mangaka/pipeline.py` (`run_pipeline(state, llm, img=None, config, *, until)` のオーケストレータ、ここまでは `img=None` で OK)
- `cli.py` 拡張: `mangaka run "seed" --until {plot,backstory,mpbv}`
- `tests/test_layers_text.py` (unit per layer with `FakeLLMClient`)

注意: `pipeline.py` の `until` enum は M2/M3/M4 で値を順次追加する（M2 で stylist/character/location、M3 で page_plan、M4 で page_beat/page_render）。M1 で完璧な enum を確定せず、必要に応じて拡張する前提で書く。

Deliverable: `uv run mangaka run "魔法学校の話" --until mpbv` で `runs/{name}/state_03_mpbv.json` が作られる。`FakeLLMClient` で全テストが通る。`RUN_INTEGRATION=1` で実 API smoke も通る。

### M2 — Image-bearing layers (PoC ★)

ここが**最初の本物の正念場**。gpt-image-2 の挙動を実 API で初めて検証する。

スコープ:
- `src/mangaka/image/client.py` (`ImageClient` Protocol: `generate(prompt, *, size, quality)` と `edit(prompt, *, base, refs, size, quality)`)
- `src/mangaka/image/client_openai.py` (`OpenAIImageClient` — `/v1/images/generations` / `/v1/images/edits`)
- `src/mangaka/image/client_fake.py` (`FakeImageClient` — 固定 PNG バイト列を返す)
- `src/mangaka/image/retry.py`
- `src/mangaka/image/sections.py` (`extract_sections(stylist_md, section_nos)` 実装)
- `prompts/04_stylist.md`, `04b_style_ref.md`
- `prompts/05_character.md`, `05b_character_sheet.md`
- `prompts/06_location.md`, `06b_location_sheet.md`
- `src/mangaka/layers/stylist.py` (text + style_ref 生成)
- `src/mangaka/layers/character.py` (text + sheet 生成)
- `src/mangaka/layers/location.py` (text + sheet 生成)
- `src/mangaka/persistence.py` 拡張: アセット versioned save (`alice.png` → `alice_v002.png`)
- `cli.py` 拡張: `--until stylist`, `--until character`, `--until location`
- `tests/test_layers_image.py` (Fake で動作確認)
- `tests/test_sections.py` (extract_sections の unit)

**PoC ステップ (実 API、コスト ~$1.5-3 想定)**:
画像 5 枚 (style 1 + char 2 + loc 2) × $0.15-0.25 + LLM 数呼び出し。リテイク 1〜2 回まで折り込み済み。


1. `seed = "高校生 2 人の屋上短編"` で `--until mpbv` まで
2. `--until stylist` で style.png 生成、目視確認
3. `--until character` でキャラ設定画 2 枚を生成、Stylist の絵柄と整合してるか
4. `--until location` でロケ設定画 2 枚

検証ポイント:
- gpt-image-2 が `style.png` を ref に取った時、Character/Location sheet がスタイル整合してるか
- 日本語キャラ説明テキストでキャラの一貫性が出るか
- セクション分配（SECTION_SETS）がプロンプトとして機能するか

ここで問題があったら ARCH/SCHEMA に戻ってリビジョン。

Deliverable: `runs/{name}/assets/style.png`, `characters/*.png`, `locations/*.png` が出力される。

### M3 — Structure layer (PagePlan)

スコープ:
- `prompts/07_page_plan.md` (arc 起承転結 + page_outline 生成、ID 整合性厳守)
- `src/mangaka/layers/page_plan.py` (PagePlan 生成、validate 含む)
- バリデーション規則: SCHEMA.md §6 のルールを厳格に実装:
  - `total_pages <= limits.max_pages`
  - `len(arc) <= limits.max_arc_phases`
  - arc が start_page 昇順かつ連続、`arc[0].start_page == 1`、`arc[-1].end_page == total_pages`
  - `len(page_outline) == total_pages`、`page_outline[i].page_number == i + 1`
  - 各 `page_outline[*].phase` が `arc` のいずれかと一致
  - `character_ids` / `location_id` が定義済み ID
- `cli.py` 拡張: `--until page_plan`
- `tests/test_page_plan.py`

Deliverable: `runs/{name}/state_07_page_plan.json` が出力される。`PagePlan` のバリデーションが全部通る。Timeline 層は v1 で持たない（Continuity slice は v2 候補、ARCH 参照）。

### M4 — Page generation (本命 ★★)

ここが完成形。gpt-image-2 で実際に漫画ページを生成する。

スコープ:
- `prompts/08_page_beat.md` (Markdown + YAML frontmatter 生成)
- `prompts/09_page_render.md` (jinja2 template、`build_page_prompt` の最終出力テンプレ)
- `src/mangaka/parse/page_beat.py` (frontmatter + Panel パーサ、Phase 1 tolerant / Phase 2 strict 二段)
- `src/mangaka/image/ref_builder.py` (`LabeledRef` 返却の `build_refs`)
- `src/mangaka/image/prompts.py` (画像生成プロンプト合成: `build_page_prompt(state, page_beat, labeled_refs, config) -> Result[str, MangaError]` + `extract_visual_summary(character_or_location_md, max_chars)` ヘルパー実装。長さガードは config の `image.max_prompt_chars` / `warn_prompt_chars` を使う)
- `src/mangaka/layers/page_beat.py` (LLM → .md ファイル書き出し + parse + state JSON 更新)
- `src/mangaka/layers/page_render.py` (`generate_page_render` がオーケストレータ。`build_refs()` を 1 回呼んで `build_page_prompt()` と `ImageClient.edit()` の両方に渡す。versioned save)
- `src/mangaka/export/pdf.py` (A5 portrait, RTL `/ViewerPreferences << /Direction /R2L >>`, contain fit)
- 画像連番出力は **`runs/{name}/pages/` 自体が canonical artifact** なので独立 export モジュールは不要。各 Page の current version を state JSON の `image_path` から引いて使う。「最終配信用に別ディレクトリへコピー」が必要になったら M5 で `src/mangaka/export/images.py` を追加（v1 では作らない）
- `cli.py` 拡張: `mangaka run --until page_render`, `mangaka export`
- `tests/test_ref_builder.py` (LabeledRef 順序、prev_page の page_number 引き、char_budget の max(0,...))
- `tests/test_page_beat_parse.py` (Phase 1 tolerant + Phase 2 strict)
- `tests/test_image_prompts.py` (`extract_visual_summary` の unit)
- `tests/test_export_pdf.py` (PIL でテスト用画像 4 枚 → PDF、ViewerPreferences が正しく付くか pikepdf or `pdfinfo` で検証)

**PoC ステップ (実 API、コスト ~$2-4)**:

1. `config.toml` で `max_pages=4`, `max_arc_phases=2` に絞る
2. `mangaka run "屋上で告白する高校生 2 人の短編"` を full run
3. 4 ページが生成され PDF として開けるか確認
4. 観察:
   - キャラ一貫性 (4 ページにわたり同じ顔か)
   - 日本語セリフの読みやすさ (壊れない程度に出るか)
   - コマ割りの読み順 (RTL ちゃんとしてるか)
   - 直前ページ ref がコマ運びの整合に効いてるか

Deliverable: 4 ページの A5 PDF が出力される。`runs/{name}/manga.pdf` を開いて読める。

### M5 — Polish & inject

スコープ:
- `cli.py` 拡張: 以下 5 種類の inject (versioned save + state 更新 + 波及無効化 + state_final.json 削除を内部で実施)
  - `--inject-mpbv path.md`
  - `--inject-stylist path.md`
  - `--inject-character-sheet alice=path.png`
  - `--inject-location-sheet rooftop_morning=path.png`
  - `--inject-page-plan path.json`
  - `--inject-page-beat 5=path.md`
- `cli.py` 拡張: `mangaka status [runs/{name}/]` — 最新 run の進捗、生成済み layer、累計コストを表示
- `src/mangaka/cost.py` — 各 LLM/Image 呼び出しでコストを記録、`mangaka status` から読める形で永続化（`runs/{name}/cost.jsonl` 等）
- 進捗バー (rich.progress) — `run` 実行中の表示用
- **`tests/test_inject.py`**: 5 種類の inject 挙動を Fake clients で検証
  - versioned filename への保存 (`alice.png` 残存、新規に `alice_v002.png` が作られる)
  - 該当 state ファイルの削除 (`state_09_page_NN.json`、`state_final.json`)
  - 波及範囲が ARCH §依存波及表 と一致する
  - `--inject-page-beat N` 時に N+1 以降が **cascade されない** (v1 既知の制約) ことを assert
- `README.md` 書き上げ
- 16-24 ページの本格短編を 1 本流して目視確認
- **prompts/ をパッケージ同梱**: 現状 `PromptLoader` は `Path(__file__).parents[3] / "prompts"` でディスク上の repo を前提にしている。`uv run`（editable）では動くが、wheel/sdist で配布した場合に `site-packages` から `parents[3]/prompts` を見るので解決できない。M5 で `importlib.resources` ベースの loader に切り替えるか、`pyproject.toml` で `prompts/` を package data として同梱する（mlsg2 も同じ問題を抱えていた）
- **`limits.max_parse_retries` を全レイヤーに適用**: M3 で PagePlan には parse retry を実装、M4 で PageBeat にも実装した。残りは Stylist / Character / Location — 「1 回パースして失敗したら即 abort」のまま。LLM の format drift で run 全体を落とすコストが高いので、PagePlan/PageBeat で書いた retry-with-feedback パターンを共有ヘルパー `parse_with_retry(prompt, llm, parser, layer_config, max_parse_retries)` に抽出して残りのレイヤーでも使う（M5 のクリーンアップ枠で対応、または別 milestone）
- **state JSON のパス可搬性**: 現状 `persistence._serialize` は `state.stylist.style_ref_path` 等を `str(Path(...))` でそのまま書き出している。CLI が `Path(config.general.runs_dir) / run_name` で run_dir を組み立てる際は通常 CWD 相対なので、別 CWD からの `mangaka export` や run dir の移動でパス解決が壊れる。M5 で `state.json` 内のパスを run_dir 相対に書き出し、ロード時に state file の親ディレクトリを基準に解決する形に変更する（serializer/deserializer 全体に波及するので独立 milestone）

Deliverable: `mangaka run "seed"` で end-to-end の短編漫画 PDF が出る。inject 5 種類で気に入らない部分だけリテイクできる。`mangaka status` で累計コストが見える。

---

## 4. pyproject.toml ドラフト

```toml
[project]
name = "mangaka"
version = "0.1.0"
description = "Short manga generator with multi-layered prompts + gpt-image-2"
requires-python = ">=3.12"
license = { text = "Proprietary" }
dependencies = [
    "openai>=2.0",                # Responses API + Images API
    "returns>=0.23",
    "pydantic>=2.0",
    "python-frontmatter>=1.1",
    "jinja2>=3.1",
    "python-dotenv>=1.0",
    "structlog>=24.0",
    "rich>=13.0",
    "reportlab>=4.0",
    "pillow>=10.0",
    # NOTE: Python 3.12 ships `tomllib` in stdlib なので tomli は不要
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.15",
    "pyright>=1.1",
    "pikepdf>=9.0",     # PDF 検証用
    "hypothesis>=6.0",  # parser の property test 用 (optional)
]

[project.scripts]
mangaka = "mangaka.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["F", "I", "UP", "B", "SIM", "RUF", "N", "PT", "C90"]
ignore = ["RUF002", "RUF003"]  # 日本語コメントの全角記号は許容

[tool.ruff.lint.mccabe]
max-complexity = 15

[tool.pytest.ini_options]
testpaths = ["tests"]
# integration はデフォルトで除外。実 API を叩くテストは RUN_INTEGRATION=1 で明示的に走らせる
addopts = "-q -m 'not integration'"
markers = [
  "smoke: quick import / CLI sanity checks (run on every Stop hook)",
  "integration: real OpenAI API calls (RUN_INTEGRATION=1 pytest -m integration で走らせる)",
]
filterwarnings = [
    "error::Warning:mangaka",     # mangaka 自体の Warning は error
    "default::DeprecationWarning",  # openai/pydantic/reportlab の Deprecation は許容
]
```

加えて integration test 側にも保険として `@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1")` を付けて、誤って `pytest -m integration` を素で走らせても API key 必須で fail-fast にする。

### pyrightconfig.json

```json
{
  "include": ["src", "tests"],
  "exclude": ["**/__pycache__", "**/.venv", "runs", "data"],
  "typeCheckingMode": "strict",
  "pythonVersion": "3.12",
  "pythonPlatform": "All"
}
```

---

## 5. ファイル単位の実装順 (M0-M1 詳細)

M0 で書く順番:

1. `pyproject.toml` → `uv sync --group dev` で deps 入る
2. `.gitignore` (`.venv`, `__pycache__`, `runs/`, `*.png`, `.env`)
3. `.env.example` (`OPENAI_API_KEY=sk-...`)
4. `src/mangaka/__init__.py` (`__version__ = "0.1.0"`) — pyright が src/ を含める前に存在させる
5. `pyrightconfig.json` — `src/` ディレクトリができてから書く。空 include で strict は文句を出す
6. `src/mangaka/result.py` (`Result`/`Success`/`Failure` re-export + `unreachable`)
7. `src/mangaka/errors.py` (`MangaError` + `ErrorKind` enum)
8. `src/mangaka/domain.py` (全 dataclass を ARCH の §ドメイン型 から)
9. `src/mangaka/config.py` (Pydantic で `MangakaConfig` 定義 + `image.max_refs_per_page >= 2` validator、`tomllib` で読み込み)
10. `src/mangaka/__main__.py` (`python -m mangaka` エントリ)
11. `src/mangaka/cli.py` (stdlib `argparse`、追加 dep なし、まず `--version` のみ)
12. `tests/test_smoke.py` (`import mangaka; assert mangaka.__version__`)
13. `tests/conftest.py` (共通 fixtures: synthetic state, fake clients placeholder)
14. `.claude/hooks/pre-edit-protect-config.sh`, `post-edit-lint.sh`, `stop-smoke-test.sh` (aurora からポート)
15. `.claude/settings.json` (hooks 登録)
16. `tools/poc_model_check.py` (`openai.models.retrieve(...)` で gpt-5.4-mini / gpt-image-2 の ID metadata を検証、M2 前の gate)

M1 で書く順番:

1. `src/mangaka/llm/client.py` (`LLMClient` Protocol、`reasoning_effort: str | None` を含む)
2. `src/mangaka/llm/client_fake.py` (FakeLLMClient、テストで使う)
3. `tests/test_llm_fake.py`
4. `src/mangaka/llm/client_openai.py` (OpenAILLMClient、env var で API key 取得、`thinking + reasoning_effort` → OpenAI Responses API の `reasoning.effort` への mapping を実装)
5. `src/mangaka/llm/prompts.py` (jinja2 loader、`prompts/` from disk)
6. `src/mangaka/llm/retry.py` (exponential backoff、aurora の pattern を参考)
7. `src/mangaka/persistence.py` (`save_state`, `load_state`, `latest_state_path`) — **layer 移植より先**に書く
8. `tests/test_persistence.py`
9. `prompts/01_master_plot.md` (mlsg2 から流用、軽微な調整)
10. `src/mangaka/layers/plot.py` (mlsg2 の plot.py を OpenAI 用に書き換え)
11. `tests/test_plot.py` (FakeLLMClient で、`tests/` は flat 配置)
12. 上記を backstory / mpbv で繰り返し
13. `src/mangaka/pipeline.py` (`run_pipeline(state, llm, img=None, config, *, until)`、`until` enum は M1 では `plot/backstory/mpbv` のみ、後続 M で拡張)
14. `tests/test_pipeline.py`
15. `cli.py` 拡張: `run` subcommand
16. integration test: `RUN_INTEGRATION=1 uv run pytest -m integration` で実 API を 1 回叩いて smoke。MPBV の reasoning が効いてるかも目視

---

## 6. テスト戦略

| 種別 | 走り方 | 何をテストするか |
|---|---|---|
| **smoke** (default Stop hook) | `pytest -m smoke` | import、CLI `--version`、`--help` がエラーなく動く |
| **unit** (default) | `pytest` | 各レイヤーを Fake LLM/Image で。1 秒以内目標 |
| **integration** | `RUN_INTEGRATION=1 pytest -m integration` | 実 OpenAI API、`max_pages=4` の最小 run で end-to-end |

PoC スクリプトは `tools/` 配下（テストではない、開発時の手動検証用）:

- `tools/poc_style_ref.py` — Stylist 1 個生成 (M2)
- `tools/poc_char_sheet.py` — Character sheet 1 枚生成 (M2)
- `tools/poc_page.py` — PageBeat 1 ページから PageRender (M4)

---

## 7. Hook セットアップ

aurora-intellica の `.claude/hooks/` をポート、mangaka 用に調整:

| Hook | 起動タイミング | 役割 |
|---|---|---|
| `pre-edit-protect-config.sh` | PreToolUse (Edit/Write) | `pyproject.toml`, `pyrightconfig.json`, `.claude/settings.json` への意図しない変更を弾く |
| `post-edit-lint.sh` | PostToolUse (Edit/Write) | `.py` 編集後に `ruff check --fix` + `ruff format` + `pyright`、issue があれば Claude に返す。`# pyright: basic` をプロダクションコードでブロック |
| `stop-smoke-test.sh` | Stop (Claude 完了時) | 直近のセッションで `.py` 変更があれば `pytest -m smoke` を走らせる |

`.claude/settings.json` でこの 3 つを登録。aurora の `set -euo pipefail` + `jq` パターンを踏襲。

---

## 8. リスク領域と緩和

| リスク | 影響度 | 緩和 |
|---|:-:|---|
| OpenAI モデル名 (`gpt-5.4-mini`, `gpt-image-2`) が実在しないか name が違う | 高 | M0 末に `tools/poc_model_check.py` で `openai.models.retrieve(...)` の metadata 検証を先に行う |
| gpt-image-2 のキャラ一貫性が思ったほど出ない | 高 | M2 末で実 API 検証。崩れたら `character_sheets_per_char = 2` (表情差分も生成) を `v1.5` に前倒し |
| 日本語セリフが完全に崩壊する | 中 | M4 末で実 API 検証。壊滅的なら exact_dialogue + Lettering 層を v2 で前倒し（ただし「ちょいおもろい」ゴール的に多少崩れは許容） |
| PageRender prompt が文字数上限超え (gpt-image-2 の prompt は 32k chars) | 中 | `extract_sections` で section 分配、`extract_visual_summary` で Character/Location の上限を絞る (config の `image.max_*_summary_chars`)。`max_prompt_chars` 超過は hard fail で `Failure(PROMPT_TOO_LONG)`、PoC で実測して上限値を微調整 |
| `extract_sections` / `extract_visual_summary` の実装方針が決まらない | 中 | M2 で `extract_sections` を regex 簡易実装で開始、M4 で `extract_visual_summary` も regex で。複雑化したら markdown-it に切り替え。LLM 要約依頼は速度・コスト的に最後の手段 |
| `python-frontmatter` パーサ選択が後で困る | 低 | デフォルトで `python-frontmatter` を採用。Phase 1 tolerant が機能しない時のみ pyyaml + 自前 regex に降りる |
| Ref 16 枠の重み付けが意図通り効かない | 中 | M4 PoC で `LabeledRef` の番号付き解説が gpt-image-2 に効いてるか実測。効かなければ ref 数を絞る方向に倒す |
| コストが想定の 2 倍超 | 低 | M4 PoC を 4 ページに絞って実測。`limits.max_image_calls` を v2 で実装する判断材料に |
| `reasoning_effort` の mapping が壊れて thinking 層がただの mini になる | 中 | M1 末の integration smoke で MPBV を 1 回叩いて、reasoning が効いてるか応答品質を目視 |

各リスクは PoC ステップで実際に検証してから次に進む。「動かない」より「動くけど期待外れ」のほうが多いはず。

---

## 9. v1 で書かないもの (再確認)

ARCHITECTURE.md の「v1 スコープ外」を実装計画レベルでも繰り返し:

- Lettering / Compose 層
- 見開き (spread page)
- 縦スク (webtoon)
- LTR (英訳輸出)
- モノクロ化 / トーン処理
- キャラ表情差分・variants
- ContinuitySlice (衣装・怪我・持ち物)
- 視覚的整合性 Validator
- 画像コスト上限ストッパ (`limits.max_image_calls`)
- 自動 invalidation cascade
- `--cascade-from` / `--strict-continuity` (直前ページ stale 対策)
- Anthropic / Bedrock サポート (OpenAI 一本)

これらが必要だと M2-M5 中に判明したら、ARCH/SCHEMA に追記してから実装する。投機的に先に入れない。

---

## 10. 完了条件 (DoD)

v1 として "リリース可能" と見なせる条件:

1. `uv run mangaka run "任意のシード"` で end-to-end の短編 PDF が生成される
2. `--inject-*` 系コマンドが 6 種すべて動く (mpbv / stylist / character-sheet / location-sheet / page-plan / page-beat)
3. `uv run pytest` (unit) が全 PASS、1 分以内に完走
4. `uv run pytest -m smoke` が Stop hook で走り、PASS
5. `uv run pyright` が 0 error。`python-frontmatter` / `reportlab` などの third-party は型 stub 不在なので、対象モジュールへの限定的な `# type: ignore[import-untyped]` は許容（プロダクションコード本体は ignore-free を維持）
6. `uv run ruff check` が 0 issue
7. `README.md` に最小限のセットアップと CLI 例が書かれている
8. **M4 PoC の 4 ページ artifact が author 承認** されたうえで、16-24 ページの短編を 1 本人が読める品質で生成できる (主観評価、「ちょいおもろい」基準。M4 baseline からの regression 無し)

---

## 11. 参照

- `/Users/dux/repos/mlsg2/` — レイヤー構造・prompt 流用元
- `/Users/dux/repos/mlsg2/src/mlsg/` — 移植元の Python パッケージ構成
- `/Users/dux/repos/aurora-intellica/pyproject.toml` — ruff / pyright 設定のベース
- `/Users/dux/repos/aurora-intellica/.claude/hooks/` — hooks 雛形
- `docs/ARCHITECTURE.md`, `docs/SCHEMA.md`, `CLAUDE.md` — 本計画の前提
