# CLAUDE.md

## Project Overview

mangaka — 短編漫画を LLM (Claude/GPT) + 画像生成モデル (gpt-image-2) で生成する Python プロジェクト。
姉妹プロジェクト **mlsg2** (`/Users/dux/repos/mlsg2/`) の「多層プロンプト + Result-based パイプライン」アーキテクチャを継承し、出力を散文小説から漫画ページ画像に置き換える。

**ゴール: "ちょいおもろい" 短編漫画**。商業原稿レベルの完成度を目指さない。絵柄一貫性・コマ割り・テンポ・キャラの感情が伝わる絵が出ればよい。文字精度は犠牲にしてよい（gpt-image-2 の出力をそのまま採用、Lettering 層は持たない）。

## Project Status

**設計フェーズ**。実装スキャフォールド（`pyproject.toml`, `src/mangaka/`）は未着手。
ARCHITECTURE.md / SCHEMA.md で大枠は固まったので、次は実装着手 (or 小規模 PoC) のステージ。

## Documentation

すべての設計判断は以下の **living docs** に集約する。新規ルールや設計変更を導入する場合は、コードより先にこれらを更新する。

- `CLAUDE.md` (this file) — coding rules, harness 規約
- `docs/ARCHITECTURE.md` — レイヤー構造、ドメイン型、永続化、依存関係
- `docs/SCHEMA.md` — 各レイヤーの入出力スキーマ（Markdown / JSON 詳細）
- `README.md` — セットアップ・CLI 使い方 (TODO: 実装着手後に書く)

矛盾を見つけた場合: **止めて確認する**。code と doc がズレている時は doc を真として再設計するか、明示的に doc を変える。勝手な「ad-hoc 折衷」をしない。

## Project Philosophy

- **ちょいおもろい程度がゴール**。商業品質を目指さない
- **mlsg2 流用最大化**: 上位層 (Plot/Backstory/MPBV/Stylist/Character/Location) はほぼそのまま継承。mlsg2 の Chapter / Timeline は短編向けに **PagePlan** 1 層に統合（章概念は v1 で持たない）。下位層 (PageBeat/PageRender) は mangaka 固有
- **コスト意識**: gpt-image-2 は ~\$0.21/枚。1 作品 \$10-25 の予算を念頭に、speculative な画像生成は避ける
- **段階導入**: PoC は 4-8 ページ、標準短編 16-24 ページ、商業相当 45 ページ。常に少ない方から検証

## Setup (future)

実装着手後に確定。想定:

```bash
uv sync --group dev
uv run mangaka --version
```

依存関係は `pyproject.toml` + `uv.lock` で管理。`uv run <cmd>` で各種ツール (ruff, pyright, pytest 等) を実行。

## Code Quality

実装着手時にセットアップ:

- **Ruff** (formatter + linter + import sorting, line length: 100, target: py312)
- **Pyright** in `strict` mode
- 設定は `pyproject.toml` / `pyrightconfig.json` に集約

新規・変更コードはこの規約に従う。`# pyright: basic` の追加はテストコード以外で原則禁止。

## Coding Conventions

### Language

- **Code comments / docstrings: 英語**
- **prompts/ 配下のプロンプトテキスト: 日本語**（LLM への入力、gpt-image-2 への入力）
- **README / ユーザー向け説明: 日本語可**
- **ドキュメント (`docs/*.md`, `CLAUDE.md`): 日本語ベース**（mlsg2 / aurora と揃える）

### Type Annotations

- すべての関数に完全な型注釈（引数・戻り値）。Pyright strict で enforce
- モダン構文: `X | Y` ユニオン、組み込みジェネリクス、`Self` 型
- 構造化 LLM 出力は Pydantic `BaseModel` で受ける

### Style

- mutable な default 引数禁止
- 1 関数 1 責任、深いネストを避ける
- コメントは「なぜ」だけ書く（コードが「何を」を示す）

## Error Handling

**Result-first**。詳細ルールは mlsg2 の `CLAUDE.md` (`/Users/dux/repos/mlsg2/CLAUDE.md`) を**継承する**。要点だけ抜粋:

- 外部 I/O (LLM API, Image API, ファイル, ネットワーク) を行うレイヤーは `Result[T, MangaError]` を返す
- 例外は programmer bug / invariant violation 用のみ
- ドメインエラーは typed (`dataclass` + `Enum`)。生の文字列・整数は返さない
- 外部 I/O や検索結果の「無い」は `Optional[T]` で表現しない。専用の `Failure(NotFound(...))` 等を使う
- 例外: `MangaState` の未到達レイヤーや `Page.image_path` のような pipeline 進捗表現では `T | None` を許容する。利用前に境界で検証し、欠落を domain error に昇格する
- リトライはレイヤー外側 (オーケストレータ/CLI) の責任。I/O 関数内で無限リトライしない
- `Result[Result[T, E2], E1]` のようなネストは禁止

mangaka 固有のエラー型は `src/mangaka/errors.py` に集約予定。レイヤーごとに `PlotError`, `PageRenderError` 等の variant を持つ。

## Architectural Discipline

### Pipeline Layer Signature

各レイヤーは純粋に近い関数として実装:

```python
def generate_X_layer(
    state: MangaState,
    llm: LLMClient,
    img: ImageClient | None,   # 画像生成層のみ要求
    config: LayerConfig,
) -> Result[MangaState, MangaError]:
    ...
```

- `state` を受け取って新しい `state` を返す（immutable に作る、in-place 変更しない）
- 副作用は `llm` / `img` 経由のみ。それ以外（ファイル書き込み等）も慎重に
- pure な部分（パース、整形、Ref 組み立て）は副作用なしのヘルパーに切り出す

### Client Abstraction

LLM / 画像生成はすべて Protocol 経由:

```python
class LLMClient(Protocol):
    def complete(self, prompt: str, *, ...) -> Result[str, MangaError]: ...

class ImageClient(Protocol):
    def generate(self, prompt: str, *, size, quality, model) -> Result[bytes, MangaError]: ...
    def edit(self, prompt: str, *, base, refs, ...) -> Result[bytes, MangaError]: ...
```

実装は `OpenAILLMClient`, `OpenAIImageClient` (v1 は OpenAI 単一)。テストは `FakeLLMClient`, `FakeImageClient` で代替。将来 provider 追加が必要になったら `AnthropicLLMClient` 等を増やす想定だが v1 では実装しない。

### Asset Immutability

`assets/` 配下と `page_beats/` 配下の生成物は **canonical artifact** として扱う:

- 一度生成したファイルは上書きしない
- 差し替えは `--inject-*` CLI 経由で行う。CLI は `*_vNNN` などの新パスに保存し、state 参照を更新し、波及無効化を内部で実施する
- 手動編集したい場合も、canonical file を直接上書きせず、コピーを編集して `--inject-*` で取り込む
- 直接 `rm` するのは低レベル復旧用（state_*.json も一緒に消す）

これは ARCHITECTURE.md「アセット依存と再生成の波及」の運用ルール。コード側でも保証する（書き込みは生成時のみ、上書き禁止）。

### State Persistence

- `state_*.json` は **path 参照とパース済みサマリのみ**。画像本体や原文 Markdown を埋め込まない
- canonical な内容物の置き場:
  - `assets/style.png`, `assets/characters/*.png`, `assets/locations/*.png` (PNG)
  - `page_beats/page_beat_NNN.md` (Markdown + YAML frontmatter)
  - `pages/page_NNN.png` (PNG)
- state JSON への base64 埋め込み禁止（肥大化）

## Cost-aware Image Generation

gpt-image-2 は \$0.15-0.25/枚で、**意識せずに使うと 1 作品 \$30 超え**まで行く。以下のルール:

- ユニットテストで実 API を呼ばない (`FakeImageClient` を使う)
- PoC 時は `limits.max_pages = 4-8` などで上限を絞る
- リテイクは目的を絞ってから（`--inject-*` で範囲を最小化）
- speculative な「念のため別バージョン」生成は避ける
- 失敗時のリトライは `limits.max_image_retries = 2` 程度に抑える

`limits.max_image_calls` 等の安全ストッパは v2 候補（v1 は手動運用）。

## Testing

- `pytest` で unit / integration を分ける
- **unit**: Fake clients で動かす、外部 API を呼ばない。1 秒以内で完走することを目指す
- **smoke** マーカー: CLI / import / 起動の sanity check。`pytest -m smoke` を Stop hook で走らせる
- **integration**: 実 API キー使用、`RUN_INTEGRATION=1 pytest -m integration` で明示的に走らせる
- 各レイヤーは `(synthetic_state, fake_clients, default_config)` で単体テスト可能であること
- 画像生成のテストはバイト列を返す Fake で代替（実画像比較しない）

## Environment Variables

`.env` で管理（python-dotenv で load）:

```
OPENAI_API_KEY=sk-...           # gpt-image-2 + GPT モデル
ANTHROPIC_API_KEY=sk-ant-...    # Claude モデル
```

絶対にコミットしない。`.gitignore` で `.env` を除外。

## Hooks (planned)

aurora-intellica の `.claude/hooks/` パターンを参考に、以下を設置予定:

| Hook | 役割 |
|---|---|
| `pre-edit-protect-config.sh` | `pyproject.toml`, `pyrightconfig.json`, `.claude/settings.json` 等の保護 |
| `post-edit-lint.sh` | `.py` 編集後に `ruff check` + `ruff format` + `pyright` をかけてフィードバック |
| `stop-smoke-test.sh` | `.py` 変更ありの完了前に `pytest -m smoke` を走らせる |

実装スキャフォールド後にセットアップ。スクリプトは aurora の流儀（`jq` で stdin JSON を読み、graceful fallback、`set -euo pipefail`）に揃える。

## Planning Discipline

aurora-intellica の規律を継承:

**architectural complexity** (新層、新ファイルタイプ、永続化変更、CLI 拡張、依存関係追加) を入れる前に:

1. どの invariant / user-facing failure を防ぐためか?
2. これを削ったら何が壊れるか?
3. 既存の仕組みで同じ invariant を維持できないか?

**local complexity** (1 関数内のヘルパー、normalization、軽い分岐) は安い。気軽に導入してよい。

投機的 refactor / 未観測のエッジケース対応 / 「将来のため」だけでは architectural complexity を入れない。
v1 スコープ外項目（Lettering, 見開き, webtoon, LTR 等）は ARCHITECTURE.md に明記済み — そちらに追加してから実装に入る。

## Browser / External Tools

ドキュメント生成中に gpt-image-2 や OpenAI Image API の仕様を確認する必要が出た場合:
- 公式 docs: `https://platform.openai.com/docs/guides/image-generation`
- モデルページ: `https://developers.openai.com/api/docs/models/gpt-image-2`

`.env` に `OPENAI_API_KEY` が入っているので、PoC 用の検証は自由に実施してよい（コストは念頭に置く）。

## Contradictions

ルール間や code と doc の間で矛盾を見つけたら、**止めて確認**。
明示的な設計判断を求める。複数のやや異なる ad-hoc パターンが並ぶ状態は避ける。

## References

- mlsg2 (流用元、Result discipline 完全版): `/Users/dux/repos/mlsg2/CLAUDE.md`
- aurora-intellica (hooks 参考、harness 設計): `/Users/dux/repos/aurora-intellica/.claude/hooks/`
- mangaka 設計: `docs/ARCHITECTURE.md`, `docs/SCHEMA.md`
