# mangaka

短編漫画を LLM + 画像生成モデル (gpt-image-2) で生成する Python パイプライン。
seed テキスト → plot → backstory → MPBV → 設定画 → コマ割り → ページ画像 → A5 PDF まで一気通貫で出力する。

姉妹プロジェクト **mlsg2** (散文小説生成) の多層プロンプト + Result-based パイプライン設計を、漫画ページ出力に置き換えたもの。
**ゴールは「ちょいおもろい」レベルの 4-24 ページ短編**。商業同人クオリティは目指していない。

## ステータス

M1〜M4 完了。M5 (inject CLI / status / cost tracking) は未着手。

完走するパイプライン:

```
Plot → Backstory → MPBV → Stylist → Character → Location
     → PagePlan → PageBeat → PageRender → PDF
```

PoC として **4 ページ短編** を 1 本完走済み（約 $5、所要 55 分）。

## 必要要件

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (パッケージマネージャ)
- OpenAI API key (`gpt-5.4-mini` / `gpt-5.4` / `gpt-image-2` 利用)

## セットアップ

```bash
# 依存解決
uv sync --group dev

# API key を .env に設定
cp .env.example .env
$EDITOR .env  # OPENAI_API_KEY=sk-... を埋める

# CLI 動作確認
uv run mangaka --version
```

## 使い方

### 1. シード文を渡してフルパイプラインを走らせる

```bash
uv run mangaka run "コンビニ深夜バイトの高校生が、毎晩黒スーツの常連客に話しかけられ、最後に常連客が宇宙人だとバレるオチ。" \
  --until page_render \
  --name convenience_alien \
  --config config_poc.toml
```

主なオプション:

| Flag | 説明 |
|---|---|
| `seed` (positional) | 物語のシード文。1〜3 文程度の premise |
| `-f, --seed-file PATH` | シード文をファイルから読む（positional の代替） |
| `--until LAYER` | このレイヤーで停止 (`plot` / `backstory` / `mpbv` / `stylist` / `character` / `location` / `page_plan` / `page_beat` / `page_render`)。デフォルトは `mpbv`（text only で安全） |
| `--name NAME` | 出力ディレクトリ名。省略時は seed から自動派生 |
| `--config PATH` | `config.toml` のパス。デフォルト `./config.toml` |
| `--force` | 既存 run ディレクトリへの上書き許可（state を消去して新規スタート） |
| `-v, --verbose` | DEBUG ログを表示 |
| `-q, --quiet` | INFO ログを抑制 |

### 2. PDF として書き出す

```bash
uv run mangaka export runs/convenience_alien
```

`runs/{name}/manga.pdf` が生成される。A5 縦・RTL 綴じ。

### 3. 中間 layer まで確認する

text layer だけ動かして内容をチェックしたい場合:

```bash
uv run mangaka run "シード文" --until mpbv  # ここで止めて出力を確認
```

`runs/{name}/state_03_mpbv.json` を読んで、プロット・キャラ設定・伏線が妥当か検証してから先へ進める。

### 4. 特定 layer だけ再生成する（手動）

v1 では resume / inject 機能が無いため、layer 単位のリテイクは **state ファイル削除＋`--force`** で行う:

```bash
# Stylist だけ気に入らないので再生成 (それ以降の layer も連鎖再生成される)
rm runs/convenience_alien/state_04_stylist.json \
   runs/convenience_alien/state_05_*.json \
   runs/convenience_alien/state_06_*.json \
   runs/convenience_alien/state_07_*.json \
   runs/convenience_alien/state_08_*.json \
   runs/convenience_alien/state_09_*.json

uv run mangaka run "シード文" \
  --name convenience_alien --force --until page_render
```

⚠️ `--force` を付けると **すべての state ファイルが削除され、テキスト層から再生成** される。  
既に生成された `assets/` `page_beats/` `pages/` の PNG / MD ファイルは残るが、state が消えるので **コストを払って LLM 出力は作り直す** ことになる。

中間状態を保持したまま resume したい場合は PoC ヘルパー `tools/poc_continue.py` を使う:

```bash
# 最新の state_*.json から続行 (完了済み layer は skip される)
uv run python tools/poc_continue.py runs/convenience_alien --until page_render
```

このヘルパーは PoC 用の暫定実装。proper な resume / inject は M5 で `mangaka run --from` / `mangaka run --inject-*` として CLI 化予定 (`docs/PLAN.md` 参照)。

## 出力ディレクトリ構造

```
runs/{name}/
├── config.toml                       # この run で使った設定の snapshot
├── state_00_init.json
├── state_01_plot.json                # 各 layer 完了時のスナップショット
├── ...
├── state_09_page_render.json
├── assets/
│   ├── style.png                     # スタイル参照画
│   ├── characters/{char_id}.png      # キャラ設定画
│   └── locations/{loc_id}.png        # ロケ設定画
├── page_beats/
│   └── page_beat_NNN.md              # ページごとのコマ割り指示書 (Markdown + YAML)
├── pages/
│   └── page_NNN.png                  # gpt-image-2 出力ページ画像
└── manga.pdf                         # 最終 PDF (export 後)
```

## 設定 (config.toml)

主要セクション:

```toml
[limits]
max_pages = 24                # ページ数上限
max_arc_phases = 5            # 起承転結フェーズ数の上限
max_panels_per_page = 8
max_main_characters = 8
max_locations = 6
max_parse_retries = 2         # PageBeat / PagePlan の parse retry 回数

[image_provider]
provider = "openai"
model = "gpt-image-2"
default_size = "1024x1536"    # A5 縦に近い縦長サイズ
quality = "high"

[pdf]
page_size = "A5"
fit = "contain"
binding = "rtl"
image_format = "jpeg"          # "jpeg" (default, ~9× smaller) or "png" (lossless)
jpeg_quality = 85              # 1-100. 85 is the manga sweet spot for gpt-image-2 output

[models]
default = "gpt-5.4-mini"      # 大半のレイヤーで使う安価モデル
validation = "gpt-5.4"        # MPBV / PagePlan (高 reasoning が必要)
```

PoC 用の小規模設定例は `config_poc.toml` (max_pages=4) を参照。
全項目の解説は `docs/ARCHITECTURE.md §設定` にある。

## コスト目安

| 規模 | ページ数 | 推定コスト |
|---|---|---|
| PoC | 4 | $4-6 |
| 標準短編 | 16-24 | $15-25 |
| 商業相当 | 45+ | $35-60 |

内訳目安 (4 ページ):
- LLM (plot/backstory/MPBV/page_plan): $1-2
- style ref + character sheets + location sheets: $1.5-2
- page render: $0.21/ページ

> 💡 ユニットテストは `FakeImageClient` / `FakeLLMClient` で動くので API 課金は発生しない。
> integration test は `RUN_INTEGRATION=1 pytest -m integration` で明示実行。

## 既知の制約

1. **日本語テキスト** はある程度まで verbatim render される (Phase 2 までの prompt engineering で大幅改善) が、長文や複雑漢字は崩れることがある。Lettering 層は v1 スコープ外（`docs/ARCHITECTURE.md §v1 スコープ外`）。
2. **同じ施設の複数アングル** は LLM が独立 location として扱うことがある（例: 店内 / レジ前 / 外観が別ロケ）。整合が部分的に崩れる。`docs/PLAN.md` M5 で prompt 修正予定。
3. **Resume / inject は未実装**。途中失敗したらヘルパー `tools/poc_continue.py` か state ファイル手動削除＋`--force` で対応。
4. **コスト追跡は未実装**。`runs/{name}/state_*.json` のメタ情報と OpenAI Usage ダッシュボードで間接確認。

## 開発者向け

### テスト

```bash
uv run pytest -q                       # 全テスト (高速、Fake clients 使用)
uv run pytest -m smoke                 # CLI/import の sanity check のみ
RUN_INTEGRATION=1 uv run pytest -m integration   # 実 API を呼ぶ統合テスト
```

### コード品質

```bash
uv run ruff format src/ tests/         # フォーマット
uv run ruff check src/ tests/          # lint
uv run pyright src/ tests/             # 型チェック (strict)
```

`.claude/hooks/` 配下に pre-edit-protect / post-edit-lint / stop-smoke-test の hook がある。Claude Code 経由で編集する場合は自動発火。

### ディレクトリ構造

```
mangaka/
├── src/mangaka/
│   ├── cli.py                # argparse エントリポイント
│   ├── pipeline.py           # 各 layer のオーケストレーション
│   ├── domain.py             # 不変ドメイン型 (MangaState, PageBeat, etc.)
│   ├── result.py             # Result[T, MangaError]
│   ├── config.py             # config.toml ローダ (pydantic)
│   ├── persistence.py        # state JSON serializer
│   ├── errors.py             # ErrorKind enum + MangaError
│   ├── llm/                  # LLMClient Protocol / OpenAI 実装 / Fake
│   ├── image/                # ImageClient + ref builder + page prompt builder
│   ├── parse/                # Markdown / YAML パーサ (character, location, page_beat, page_plan)
│   ├── layers/               # 各 layer の generate_X_layer 関数
│   └── export/               # PDF 出力 (reportlab + PIL)
├── prompts/                  # Jinja2 テンプレート (日本語)
├── tests/                    # pytest unit + smoke + integration
├── tools/                    # PoC 用一時スクリプト (M5 で CLI 化予定)
└── docs/
    ├── ARCHITECTURE.md       # レイヤー構造・永続化・依存関係
    ├── SCHEMA.md             # 各 layer の入出力スキーマ詳細
    └── PLAN.md               # M1〜M5 マイルストーン
```

詳細は `CLAUDE.md` / `docs/ARCHITECTURE.md` / `docs/SCHEMA.md` を参照。
