# mangaka Architecture

mangaka は短編漫画を LLM + 画像生成モデル (gpt-image-2) で生成する実験プロジェクト。
姉妹プロジェクトの **mlsg2** (Multi-Layered Story Generator, 中編小説向け) の層構造・オーケストレーション設計をほぼそのまま継承し、出力を「散文テキスト」から「漫画ページ画像」に置き換える。

このドキュメントは設計の全体像と、漫画固有の差分を記述する。コーディング規約や Result 型・エラーハンドリングの一般原則は本リポジトリの `CLAUDE.md` および mlsg2 の規約に準ずる。

---

## 設計原則

mlsg2 と共通:

### Library-first, UI-thin

- コアロジックは Python パッケージ (`src/mangaka`) に集約し、純粋な型付き関数として公開する
- CLI (`mangaka` コマンド) は薄いレイヤー
- 将来の TUI / Web ビューアはライブラリのみに依存

### Result-based Orchestration

- 各レイヤーは `MangaState -> Result[MangaState, MangaError]` のシグネチャ
- 隠れた例外なし

### LLM / Image Integration as Infrastructure

- LLM 呼び出しは `LLMClient` Protocol で抽象化（mlsg2 と同じ）
- **画像生成も `ImageClient` Protocol で抽象化**（mangaka 固有の追加）
- プロンプトテンプレートはインフラ層でロード・合成

### Testability

- 各レイヤーは fake / stub の `LLMClient` / `ImageClient` を注入して単体テスト可能
- 画像生成のテストはバイト列を返す fake で代替

---

## mlsg2 からの変更点まとめ

| mlsg2 | mangaka |
|---|---|
| Plot | **そのまま** |
| Backstory | **そのまま** |
| MPBV | **そのまま** |
| Stylist (文体のみ, Scene の前) | **Stylist (文体 + 絵柄を統合, 設定画前に移動)** |
| Character (text) | **Character (text + 設定画), Stylist の後** |
| ─ | **Location (新規, text + 設定画), Stylist の後** |
| Chapter / Timeline | **削除し `PagePlan` に統合**（20 ページ短編に章概念は不要、Timeline も pre-page context で代替できる） |
| Scene (散文) | **PageBeat (Markdown + YAML frontmatter, exact_dialogue は持たない)** |
| ─ | **PageRender (新規, gpt-image-2 で 1 ページ 1 画像)** |
| Export (HTML/PDF 小説) | **Export (PDF 漫画, A5 portrait 148×210mm, RTL)** |

**画像が要る層は 4 か所のみ:** Stylist (1 枚) / Character (N 枚) / Location (M 枚) / PageRender (P 枚)。
それ以外は純テキスト層で、mlsg2 のロジックを流用できる。

**重要な順序**: Stylist は Character / Location より**先**に実行される。理由は両者の設定画生成時に Stylist の `style_ref` を参照画像として使うため。

---

## レイヤー構成

```
User Input (seed)
    │
    ▼
┌─────────────────┐
│  1. Plot        │  → MasterPlot (Markdown)
└─────────────────┘
    │
    ▼
┌─────────────────────┐
│  2. Backstory       │  → Backstories (Markdown)
└─────────────────────┘
    │
    ▼
┌─────────────────┐
│  3. MPBV        │  → MPBV (validated Markdown)
└─────────────────┘
    │
    ▼
┌─────────────────────────┐
│  4. Stylist       │  → StyleGuide.md + style_ref.png
└─────────────────────────┘
    │
    ├─────────────────┐  (style_ref を ref に使う)
    ▼                 ▼
┌──────────────┐  ┌──────────────┐
│ 5. Character │  │ 6. Location  │
│  text + 設定 │  │  text + 設定 │
│  画 × N      │  │  画 × M      │
└──────────────┘  └──────────────┘
    │                 │
    └─────────┬───────┘
              ▼
   ┌─────────────────────────────┐
   │ 7. PagePlan (JSON, 単数)    │
   │   arc 起承転結 + page_outline (per page) │
   └─────────────────────────────┘
              │
              ▼ (per page)
   ┌─────────────────────────────────────┐
   │ 8. PageBeat (Markdown + YAML × P)   │
   │   visual + speech_intent + sfx       │
   │   NO exact_dialogue                  │
   │   (前 1-2 ページの PageBeat も参照)  │
   └─────────────────────────────────────┘
              │
              ▼ (per page)
   ┌─────────────────────────────────────┐
   │ 9. PageRender (gpt-image-2)         │
   │    refs: style + chars + location   │
   │         + 直前ページ                │
   │    image × P (文字込み)             │
   └─────────────────────────────────────┘
              │
              ▼
        Final PDF (A5, 148×210mm, RTL)
```

### 各層の責務

| Layer | 入力 | 出力 (text) | 出力 (image) | Temperature | Thinking |
|-------|------|-------------|--------------|-------------|----------|
| Plot | seed | MasterPlot.md | ─ | 1.0 | **ON** |
| Backstory | seed + plot | Backstories.md | ─ | 0.9 | **ON** |
| MPBV | plot + backstory | MPBV.md | ─ | 0.7 | **ON** |
| Stylist | mpbv | StyleGuide.md | スタイル参照画 (1 枚) | 0.7 | OFF |
| Character | mpbv + stylist | list[Character] | 各キャラ設定画 (N 枚) | 1.0 | OFF |
| Location | mpbv + stylist | list[Location] | 各舞台設定画 (M 枚) | 0.9 | OFF |
| PagePlan | mpbv + chars + locs | PagePlan (JSON, `arc` + `page_outline`) | ─ | 0.7 | **ON** |
| PageBeat | **stylist (narrative)** + page_plan + page_outline[n] + previous_page_beats[-2:] | list[PageBeat] (Markdown + YAML frontmatter) | ─ | 0.7 | OFF |
| PageRender | PageBeat + ref images | ─ | ページ画像 (1 枚) | ─ | (gpt-image-2) |

### イメージ生成サブステップの位置づけ

Stylist / Character / Location は **「テキスト出力 → そのテキストから画像生成」の 2 ステップ**で構成される単一の層として実装する:

```python
# 概念コード（Character の例。Stylist が先に実行済みであることを前提）
def generate_character_layer(state, llm, img, config):
    # Step 1: LLM がキャラ設定をテキストで生成
    characters_text = llm.complete(prompt_character, ...)

    # Step 2: 各キャラについて、設定画を生成
    for char in parse_characters(characters_text):
        sheet_bytes = img.edit(
            prompt=build_sheet_prompt(char),
            refs=[state.stylist.style_ref_path],  # 先に作られている
            size="1024x1024",
        )
        char.sheet_paths = [save_asset(sheet_bytes, f"characters/{char.id}.png")]
    return state.with_characters(characters)
```

実装上は 1 層として扱うが、状態保存 (`state_05_character.json`) には**テキスト + 画像 path** の両方が記録される。

---

## セリフと文字の扱い (v1 方針)

### gpt-image-2 にセリフを「描いて」もらう

v1 では **PageRender がページ画像にセリフ含む全要素を生成する**。Lettering 層は持たない。

理由:
- gpt-image-2 は manga スタイルで日本語の吹き出し・手書き文字・効果音を込みで描ける（公式 docs の Limitations 節は注意喚起するが、本プロジェクトのゴール「ちょいおもろい短編」には十分）
- Lettering 層を別に持つコストが高い: 空バルーン検出・座標推定・縦書き日本語フォント配置・改行ルール…別プロジェクト規模の作業
- 文字が多少崩れても許容範囲。完成原稿としての可読性より、絵柄・表情・コマ割り・テンポを優先

### PageBeat に正確なセリフを持たない

PageBeat は **「セリフがある場面の演出指示」**であり、台詞台本ではない:

- `speech_intent`: 「主人公が決意を口にする」「相手が驚く」など意味
- `register`: 「怒り」「震え」「無感情」など口調のトーン
- `emotion`: コマ全体の感情
- ❌ `exact_dialogue`: **持たない**

これにより LLM プロンプトの設計が「セリフを書く」モードに引っ張られず、絵作り・演出の指示に集中できる。PageRender は「このコマでこういう感情で発話している場面を描いて」と指示し、gpt-image-2 が**任意の日本語っぽい文字**を吹き出しに入れる。

### 将来の Lettering 移行余地

もし将来「セリフを正確に出したい」需要が出た場合:
- PageBeat に optional な `exact_dialogue: str` を schema migration で追加
- PageRender prompt を「文字は空バルーンで」モードに切替
- PageRender の後に Lettering 層を挿入

各層は `MangaState → Result[MangaState, MangaError]` の純粋関数なので、間に層を挟むのはアーキ的に自由。**今から先取りでデータを持つ必要はない**。

---

## ドメイン型

`src/mangaka/domain.py`:

```python
@dataclass(slots=True)
class MangaState:
    seed_input: str
    run_name: str
    master_plot: MasterPlot | None = None
    backstories: Backstories | None = None
    mpbv: MPBV | None = None
    stylist: Stylist | None = None     # Character/Location より先
    characters: list[Character] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    page_plan: PagePlan | None = None  # 1 作品 1 個 (旧 chapters + timelines を統合)
    pages: list[Page] = field(default_factory=list)

    # 派生 lookup (@cached_property などで実装、シリアライズ対象外):
    #   characters_by_id: dict[str, Character]
    #   locations_by_id: dict[str, Location]
    #   pages_by_number: dict[int, Page]    # `page_number - 1` 検索で使う
    # Ref Builder や prompt builder は ID/番号ベースで引くので、
    # `state.pages[-1]` のような順序依存のアクセスは避ける（inject 時に間違ったページを引くため）


@dataclass(slots=True)
class Stylist:
    raw_markdown: str         # 絵柄ガイド (テキスト)
    style_ref_path: Path      # スタイル参照画


@dataclass(slots=True)
class Character:
    id: str                   # 安定 ID (例: "alice")
    name: str
    description: str          # テキスト記述 (外見・性格・口調)
    sheet_paths: list[Path]   # 設定画（v1 は基本立ち絵 1 枚、list は将来の拡張余地）


@dataclass(slots=True)
class Location:
    id: str                   # 安定 ID (例: "classroom")
    name: str
    description: str          # テキスト記述
    sheet_path: Path          # 舞台設定画


@dataclass(slots=True)
class PagePlan:
    total_pages: int          # 全話通算のページ数
    arc: list[ArcPhase]       # 起承転結 / 3-5 phase 程度
    page_outline: list[PageOutline]  # 各ページの軽い outline


@dataclass(slots=True)
class ArcPhase:
    phase: str                # "セットアップ" / "対立" / "クライマックス" / "結末" 等
    start_page: int
    end_page: int
    summary: str              # この phase の意図 1〜2 文


@dataclass(slots=True)
class PageOutline:
    page_number: int          # 全話通算 (1 始まり)
    phase: str                # 紐づく arc.phase の値
    summary: str              # このページで起きることの 1 文要約
    character_ids: list[str]
    location_id: str


@dataclass(slots=True)
class Page:
    page_number: int          # 全話通算 (1 始まり)
    beat: PageBeat            # パース済み PageBeat（md_path 経由で原本にもアクセス可）
    image_path: Path | None   # PageRender 完了後にセット


@dataclass(slots=True)
class PageBeat:
    page_number: int          # 全話通算 (1 始まり)
    phase: str                # PagePlan の arc.phase ラベル (任意の文字列、参照用)
    location_id: str          # ref 組み立てに使う
    character_ids: list[str]  # 同上、主要度順
    mood: str
    continuity_note: str | None
    panels: list[Panel]       # パース済み構造
    md_path: Path             # 元の .md ファイル (canonical, page_beats/ 配下)


@dataclass(slots=True)
class Panel:
    panel_no: int                       # 読み順 (右上 → 左下、1 始まり)
    size_hint: str                      # "regular" | "large" | "wide"
    visual: str                         # コマの絵的描写
    emotion: str
    camera: str | None                  # アングル・寄り引き
    speech_intents: list[SpeechIntent]
    sfx: list[SFX]


@dataclass(slots=True)
class SpeechIntent:
    speaker_id: str            # Character ID または予約 ID "narrator"
    bubble_type: str           # "dialogue" | "inner_monologue" | "narration" | "shout"
    intent: str                # 意味的に何を伝えるか
    register: str | None       # 口調のトーン（怒り、震え、無感情、等）
    # 注: 正確なセリフ文字列は持たない。画像モデルに任意の日本語っぽい文字を描かせる。


@dataclass(slots=True)
class SFX:
    text: str                  # 実際に描く擬音 (カタカナ等)
    role: str                  # 何の音か (rain, impact, footstep, etc.)
```

`MangaState` 上の `T | None` は「そのレイヤーがまだ実行されていない」という進捗表現に限る。外部 I/O の失敗や参照 ID の未発見は `None` で返さず、`Failure(MangaError)` に正規化する。各レイヤーは必要な前段 state を入口で検証し、欠けていれば typed error を返す。

PageBeat の panel スキーマ詳細（バルーン種別、SFX、カメラ等）は `docs/SCHEMA.md` を真とする。

---

## LLMClient / ImageClient

### LLMClient (mlsg2 と同一)

```python
class LLMClient(Protocol):
    def complete(self, prompt: str, *, model, temperature, max_tokens,
                 thinking: bool = False,
                 reasoning_effort: str | None = None,   # "minimal" | "low" | "medium" | "high"
                 ) -> Result[str, MangaError]: ...
    # OpenAI Responses API では thinking=True 時に reasoning_effort を有効にする。
    # 旧 Anthropic の thinking_budget (token 数指定) とは別概念。
```

### ImageClient (新規)

OpenAI Images API は `/v1/images/generations`（テキストのみ）と `/v1/images/edits`（画像入力あり）で endpoint が分かれる。Protocol は意図を明確にするため**2 メソッドに分ける**:

```python
class ImageClient(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1536",       # HD portrait
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        """テキストプロンプトのみで新規画像生成。
        Stylist の style_ref のように最初の参照画を作る時に使う。
        内部実装: /v1/images/generations
        """

    def edit(
        self,
        prompt: str,
        *,
        base: Path | None = None,
        refs: list[Path] = (),         # base を含めて合計 16 枚以内
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        """参照画像つきで生成・編集する。
        - base がある → 既存画像のリテイク・差分修正
        - base がない & refs がある → 参照群を元に新規ページを描く（PageRender 主用途）
        内部実装: /v1/images/edits（直接 Image API を叩く）

        制約: base と refs を合わせた合計枚数は gpt-image-2 の上限 16 枚以内に
        収める（base がある時だけ 16+1 になる事故を防ぐため）。

        注: Responses API 経由の image generation tool は v1 では使わない。
        Responses API は mainline LLM (gpt-5 系等) を model に取り、画像生成は
        tool 呼び出しになるため、本 Protocol の責務とは分離する。
        """
```

実装は `OpenAIImageClient` (本物) と `FakeImageClient` (テスト用、固定バイト列を返す)。

### Ref Image Budget

PageRender 時の ref 組み立ては `src/mangaka/image/ref_builder.py` で行う。canonical 順は **`style → loc → prev → chars(主要度順)`**。

**「直前ページ」は `state.pages[-1]` ではなく `page_number - 1` で引く**。`--inject-page-beat` 経由で特定ページを再走する時、`state.pages[-1]` は最終生成済みページであって直前ページではないため、ID ベースのルックアップが必要。

詳細なアルゴリズム（負 budget の clamp、`LabeledRef` を返すシグネチャ、PageRender prompt との対応）は `docs/SCHEMA.md` §10 を参照。

---

## アセット永続化レイアウト

```
runs/{name}/
├── config.toml                  # この run で使った設定のスナップショット
├── state_00_init.json
├── state_01_plot.json
├── state_02_backstory.json
├── state_03_mpbv.json
├── state_04_stylist.json
├── state_05_character.json
├── state_06_location.json
├── state_07_page_plan.json      # PagePlan (単数、1 作品 1 個)
├── state_08_page_beat_01.json   # parsed summary + md_path (per page)
├── ...
├── state_09_page_01.json        # PageRender 完了後 (image_path 入り)
├── ...
├── state_final.json
│
├── assets/                      # 不変アセット (上書き禁止、inject は versioned 保存)
│   ├── style.png                #   初回生成、inject 時は style_v002.png 等
│   ├── characters/
│   │   ├── alice.png
│   │   ├── alice_v002.png       #   inject 後に増える
│   │   └── bob.png
│   └── locations/
│       ├── classroom.png
│       └── rooftop.png
│
├── page_beats/                  # PageBeat の canonical Markdown (上書き禁止、inject は versioned 保存)
│   ├── page_beat_001.md
│   ├── page_beat_002.md
│   ├── page_beat_002_v002.md    #   inject 後に増える
│   └── ...
│
├── pages/                       # 最終ページ画像 (上書き禁止、inject 時は versioned 保存)
│   ├── page_001.png
│   ├── page_002.png
│   ├── page_002_v002.png        #   inject 後の再 render
│   └── ...
│
└── manga.pdf                    # エクスポート成果物 (A5, RTL)
```

### アセットの扱い

**Immutable な canonical artifacts**: `assets/`、`page_beats/`、`pages/` すべて上書きしない:

- **Pipeline は既存ファイルを上書きしない**: 通常の生成パイプラインは、一度作ったファイルを変更しない（変更すると以降のページでキャラ・コマ割り・絵がブレるため）
- **`--inject-*` は versioned path で保存**: `assets/characters/alice_v002.png`、`page_beats/page_beat_005_v002.md`、`pages/page_005_v002.png` のような新パスに保存して state の参照先を更新する。既存ファイルは disk に残る
- リテイクしたい場合は、対応する `state_*.json` を削除して再生成する。既存ファイルが残っている場合は次の空き versioned filename に保存
- 旧版は run 内に残す。不要になった旧版の pruning は v2 候補

### `state_final.json` は派生ファイル

`state_final.json` は pipeline 完了時に作られる**派生スナップショット**で、canonical artifact ではない:

- どの inject でも**必ず削除して再生成**する（古い `Page.image_path` を拾って stale なページを export する事故を防ぐ）
- 単体での immutability は気にしなくてよい（中身は state_NN_*.json の集約に過ぎないため）

### state.json は path のみを持つ

画像本体や Markdown 全文を JSON にエンコードしない（base64 で肥大化するため）。常に相対パスを参照する。inject 時は新しい相対パスに state を更新するため、旧 state と旧アセットの対応は run 内に残る。

---

## アセット依存と再生成の波及

アセットを差し替えると、依存している後続レイヤーが**論理的に無効**になる。手動再実行する場合は、対応する `state_*.json` を削除して再生成する必要がある（mlsg2 の `--from` パターンと同じ）。

### 依存グラフ

**text 経路** (テキスト出力が変わると PagePlan 以降の構造設計も変わる):
```
Stylist text (visual sections)
    ├─→ Character text ─┐
    └─→ Location text ──┴─→ PagePlan → PageBeat → PageRender

Stylist text (narrative sections 1-3)
    └─→ PageBeat (LLM 生成時に直接読む)
```

**画像経路** (sheets が変わると PageRender の ref が変わる):
```
Stylist style_ref ──┐
Character sheet ──────────┼─→ PageRender (refs として消費)
Location sheet ───────────┤
直前ページ (optional) ────┘
```

加えて、Stylist の style_ref は Character/Location の sheet 生成時にも ref として使われる:
```
Stylist style_ref → Character sheet 生成 → Character sheet
                       → Location sheet 生成  → Location sheet
```

### 差し替え時の影響範囲

| 差し替えるアセット | 無効化される後続 |
|---|---|
| **Stylist (text)** | Character/Location の text + sheets, PagePlan, PageBeat, PageRender **全部** |
| **Stylist (style_ref 画像のみ)** | Character/Location の sheets, PageRender 全部（text は維持） |
| Character (text) | PagePlan, 該当キャラ登場の PageBeat, PageRender |
| Character (sheet 画像のみ) | 該当キャラ登場の PageRender のみ |
| Location (text) | PagePlan, 該当ロケ登場の PageBeat, PageRender |
| Location (sheet 画像のみ) | 該当ロケ登場の PageRender のみ |
| PagePlan | 全 PageBeat, 全 PageRender |
| PageBeat | 該当ページの PageRender |

text を差し替えると PagePlan の入力 (`mpbv + chars + locs`) が変わるため、PagePlan/PageBeat まで論理的に無効になる。**画像のみの差し替え**なら PageRender だけが影響を受ける。

`--inject-*` CLI コマンドは**該当する波及無効化を内部で実行**する（次節）。state ファイルを直接 `rm` するのは低レベル復旧用。

### 直前ページ ref と inject の cascade 問題 (v1 既知の制約)

`include_prev_page_ref = true` の場合、PageRender はページ N の生成時に **ページ N-1 の画像を ref に使う**。これは依存グラフ上「ページ N-1 → ページ N」の継続性エッジを作る。

**問題**: ページ N を inject で再生成すると、論理的にはページ N+1, N+2, ... も古い (stale) prev ref で作られたまま残る。上の波及表は inject の **直接的な依存**しか表現していない。

**v1 の方針: cascade しない、stale 許容**:

- `--inject-page-beat N` / `--inject-character-sheet` / 等が再生成するのは**該当ページの PageRender のみ**。N+1 以降は古い prev ref のままで放置
- 直前ページ ref は **soft constraint**: 失われたり stale になっても character / location sheet ref が固定されているので絵が大崩れすることはない。トーン・髪の流れ・コマ運びが微妙にズレる程度
- 「ちょいおもろい」ゴール的に許容範囲。完成原稿レベルの continuity を要求しない
- ユーザーが N+1 以降の continuity drift が気になる場合は、手動で対象ページの `state_09_page_NN.json` を削除して `mangaka run --from ...` で再走する

**将来 (v2 候補)**:
- `--cascade-from N` フラグで N 以降を強制再生成
- `--strict-continuity` mode で自動 cascade
- visual continuity validator で stale 検出

### CLI による再生成例

```bash
# style 差し替え（手動 invalidation 経路）
# Stylist の text を変えると Character/Location text + sheets,
# PagePlan, PageBeat, PageRender が全部論理的に無効になる。
# 該当 state ファイルとアセットを全削除して再生成する。
rm -f runs/my_manga/state_04_stylist.json
rm -f runs/my_manga/state_0[5-9]_*.json
rm -f runs/my_manga/state_final.json
rm -f runs/my_manga/assets/style.png
rm -f runs/my_manga/assets/characters/*.png
rm -f runs/my_manga/assets/locations/*.png
rm -f runs/my_manga/pages/*.png
mangaka run --from runs/my_manga/

# キャラ画像のみ差し替え（CLI による正規ルート、immutable 原則に従う）
# 外部で用意した alice_v2.png を inject すると:
#   1. assets/characters/alice_v002.png のような新パスに保存（元 alice.png は disk に残る）
#   2. state_05_character.json の sheet_paths を新パスへ更新
#   3. alice 登場ページの state_09_page_NN.json を内部で削除（波及無効化）
#   4. state_final.json も削除（stale な image_path 拾い防止）
#   5. pipeline 再走で該当ページの PageRender だけ再生成 → pages/page_NNN_v002.png 等
mangaka run --from runs/my_manga/ --inject-character-sheet alice=path/to/alice_v2.png
```

v1 では波及無効化は**2 ルート**:
- **CLI 正規ルート**: `--inject-*` 系コマンドが内部で対象 state ファイルを削除して再走（推奨）
- **手動ルート**: 上記 `rm -f` 例のように state ファイルを直接削除して `--from` で再走（低レベル復旧用）

包括的な自動 invalidation cascade（任意の state を変えると全依存を自動再生成）は v2 候補。

---

## 出力仕様

### PDF (主要成果物)

- **サイズ**: **A5 portrait (148×210mm)** — 単行本サイズ
  - gpt-image-2 の HD portrait 出力 (例: 1024×1536) で印刷時 ~180 DPI が確保できる
  - アスペクト比 0.705 が gpt-image-2 出力比 (~0.667) と近く、白フチが抑えられる
  - 画面表示では用紙サイズは初期ズームのみに影響、ビューアが画面幅に合わせて拡大する
  - 将来 B5 / A4 にしたい場合は config 変更だけで対応可能（同じ ISO 216 比）
- **ページあたり**: 1 画像、**contain (fit-to-page) 配置**
  - アスペクト差が出た場合は白フチ許容
  - **cover (中央クロップ) は禁止** — 漫画でコマが切れるのは致命
- **読み方向**: RTL（右開き）
  - PDF カタログに `/ViewerPreferences << /Direction /R2L >>` を設定
  - これは PDF 1.7 / ISO 32000 の仕様で、Adobe Reader / Preview / 主要ビューアが従う
- **色**: フルカラー（gpt-image-2 出力をそのまま使用、モノクロ化は v1 ではしない）
- **解像度**: gpt-image-2 の HD 出力をそのまま埋め込み（再エンコードしない）

実装は素朴な PIL + reportlab で十分。`src/mangaka/export/pdf.py`:

```python
def export_pdf(state: MangaState, output_path: Path) -> Result[Path, MangaError]:
    """pages/ 配下の画像を順番に PDF へ。R2L viewer preferences を付ける"""
    ...
```

### 副次出力（デバッグ・確認用）

- `pages/page_NNN.png`: 画像連番ディレクトリ
- `state_final.json`: 全状態の最終スナップショット
- `assets/`: 中間アセット（人間がレビュー可能）

---

## 読み方向 (RTL) の扱い

- **PageRender prompt**: 「日本のマンガ、右上から左下の読み順」を**明示的に書く**。`gpt-image-2` は manga 知識から多くの場合 RTL を出すが、公式 docs が composition control の限界を認めているので、prompt 側で読み順を毎回宣言する方が堅い
- **PageBeat の panel 配列**: 「読み順」で並べる（右上 → 左下）
- **PDF export**: `/ViewerPreferences << /Direction /R2L >>` を明示

LTR (英訳輸出) サポートは v1 では入れない。将来必要になったら上記 3 か所に flag を入れる。

---

## プロンプト言語

すべてのプロンプトは **日本語** で書く。理由:

- gpt-image-2 は日本語プロンプトを高精度で解釈できる（特に「マンガ」のような日本語起源の概念）
- セリフ意図も元から日本語、テキスト工程全体が日本語で一貫
- 「日本語 → 英訳 → 画像 prompt」という翻訳層が不要になり、情報損失を防げる

LLM 層 (GPT) も日本語で動かす。mlsg2 が Claude/GPT を切り替え可能だったのと方針は同じだが、v1 mangaka は OpenAI 単一実装。

---

## 上限値とスケール

| 項目 | デフォルト値 | 設定キー | 備考 |
|------|-------------|----------|------|
| 最大ページ数 | 24 | `limits.max_pages` | 短編の安全圏。PoC 時は 4〜8 に絞る |
| 最大 arc phase 数 | 5 | `limits.max_arc_phases` | PagePlan の `arc[]` 長 (起承転結 4 ± 1) |
| 1 ページのコマ数上限 | 8 | `limits.max_panels_per_page` | 演出上の上限 |
| 主要キャラ最大数 | 8 | `limits.max_main_characters` | ref 予算的に 6 まで楽勝、8 で打ち切り |
| 主要ロケーション最大数 | 6 | `limits.max_locations` | |
| キャラ設定画 / 人 | 1 | `assets.character_sheets_per_char` | 基本立ち絵 1 枚（表情差分は v2 候補） |
| ロケ設定画 / 場所 | 1 | `assets.location_sheets_per_loc` | |
| ref 予算 | 16 | `image.max_refs_per_page` | gpt-image-2 の上限 |
| 直前ページを ref に含める | true | `image.include_prev_page_ref` | 連続性補助 |
| PageRender prompt 上限 | 20000 chars | `image.max_prompt_chars` | gpt-image-2 の prompt 上限 32k に対して安全マージン |
| PageRender prompt 警告 | 12000 chars | `image.warn_prompt_chars` | 超えたら `extract_visual_summary` を縮める |
| ロケ要約上限 | 600 chars | `image.max_location_summary_chars` | `extract_visual_summary(location)` |
| キャラ要約上限 (1 人) | 300 chars | `image.max_character_summary_chars` | `extract_visual_summary(character)` |
| キャラ要約上限 (合計) | 1500 chars | `image.max_character_summary_total_chars` | 全 character_ids ぶんの合計 |
| LLM リトライ | 3 | `limits.max_retries` | mlsg2 と同じ |
| 画像生成リトライ | 2 | `limits.max_image_retries` | コスト高なので少なめ |

### PagePlan のページ配分

PagePlan 層は `total_pages` と `page_outline[]` (1 件/ページ) でページ数を明示する。これにより:

- PageBeat 層の完了条件が「`is_final_scene` flag」ではなく **「`page_outline` 配列を全部消化したら終了」** という明確な数値ベースになる
- ページ欠けを早期検出できる（生成完了時に `total_pages == len(pages) == len(page_outline)` を assert）
- 全体のページ数を `max_pages` 制約に収めやすい（PagePlan 層が制約内で割り振る）
- 各ページが属する `arc.phase` も outline 経由で取得可能

これは mlsg2 の Scene 層の「`is_final_scene`」フラグ判定とは異なる挙動。

### コスト試算

**注意: gpt-image-2 は token 課金**。出力画像のサイズ・品質に加え、**参照画像も入力 token として計上される**。下記は HD 出力 1024×1536 の目安で、参照画像枚数によって増減する。

公式提供の単価例（2026 年時点、変動あり）:
- HD 出力 1 枚あたり: $0.15〜$0.25 程度（サイズと quality 設定による）
- 参照画像 1 枚あたりの入力分: 比較的小さいが累積する（16 枚使うなら無視できない）

LLM コスト除く画像のみの大まかな目安:

アセット内訳: `style 1 + char N + loc M`（`character_sheets_per_char = 1`, `location_sheets_per_loc = 1`）

| 規模 | アセット画像 | ページ画像 | 概算コスト |
|---|---:|---:|---:|
| PoC (8 ページ, キャラ 2, ロケ 1) | 4 | 8 | $2〜$4 |
| 標準短編 (24 ページ, キャラ 4, ロケ 3) | 8 | 24 | $5〜$10 |
| 商業読切相当 (45 ページ, キャラ 5, ロケ 5) | 11 | 45 | $10〜$20 |

リテイク分で 1.5〜2 倍は見ておく。継続性・キャラ再現で参照画像つき edit 比率が高くなると上振れする。

`config.toml` の `limits.max_image_calls` 等で安全ストッパを置く想定（v1 未実装、v2 で追加）。

---

## CLI コマンド体系

mlsg2 の `mlsg` コマンドとほぼ同じ構造:

```bash
# 新規生成
mangaka run "魔法学校に通う少年の短編"
mangaka run -f seed.txt
mangaka run "seed" --name my_manga

# 段階的実行
mangaka run "seed" --until plot
mangaka run "seed" --until stylist     # スタイル参照画まで
mangaka run "seed" --until character          # キャラ設定画まで
mangaka run "seed" --until location           # ロケ設定画まで
mangaka run "seed" --until page_plan
mangaka run "seed" --until page_beat
mangaka run "seed" --until page_render        # 全ページ画像生成（フル実行）

# 再開
mangaka run --from runs/my_manga/
mangaka run --from runs/my_manga/ --only page_render  # 画像のみ再生成

# 状態確認・エクスポート
mangaka status runs/my_manga/
mangaka export runs/my_manga/                         # PDF を出力
mangaka export runs/my_manga/ -o my_manga.pdf
```

### 外部注入 (Human-in-the-Loop)

mlsg2 と同じパターン。MPBV / Stylist / Character / Location / PagePlan / PageBeat のレビュー差し込みをサポート:

```bash
mangaka run --from runs/my_manga/ --inject-mpbv reviewed_mpbv.md
mangaka run --from runs/my_manga/ --inject-stylist style.md
mangaka run --from runs/my_manga/ --inject-character-sheet alice=alice_v2.png
mangaka run --from runs/my_manga/ --inject-location-sheet rooftop=rooftop_v2.png
mangaka run --from runs/my_manga/ --inject-page-plan page_plan.json
mangaka run --from runs/my_manga/ --inject-page-beat 5=page_beat_005.md
```

**`--inject-*` の挙動 (v1 仕様)**:

inject コマンドは「アセットを差し替える + 論理的に無効化される後続 state を内部で削除する + pipeline を再実行」を一気通貫で行う。

**共通の後処理 (全 inject に適用)**:
- 無効化された PageRender の `pages/page_NNN.png` も versioned path (`page_NNN_v002.png` 等) で再生成する。元の `page_NNN.png` は disk に残す（assets と同じ immutable 原則）
- `state_final.json` が存在する場合は**必ず削除**する。古い `Page.image_path` を拾って stale なページを export してしまう事故を防ぐ
- pipeline 再走の最後に `state_final.json` を再生成

| コマンド | 内部処理 |
|---|---|
| `--inject-mpbv` | `state_03` を差し替え、MPBV 以降の後続 state を全削除 + `state_final.json` 削除 + `pages/*.png` も古いものは state から外す（disk には残す） → 全再生成 |
| `--inject-stylist` | `state_04` を差し替え、新しい `style_ref` を versioned path に生成、Stylist 以降の後続 state を全削除 + `state_final.json` 削除 → 全再生成 |
| `--inject-character-sheet alice=path` | 入力画像を `assets/characters/alice_vNNN.png` に保存、`state_05` の `sheet_paths` を更新、alice 登場ページの `state_09_page_NN.json` を削除 + `state_final.json` 削除 → PageRender 該当ページのみ再生成 (新 `pages/page_NNN_vNNN.png`) |
| `--inject-location-sheet rooftop=path` | 入力画像を `assets/locations/rooftop_vNNN.png` に保存、`state_06` の `sheet_path` を更新、該当ロケ登場ページの `state_09_page_NN.json` を削除 + `state_final.json` 削除 → PageRender 該当ページのみ再生成 |
| `--inject-page-plan path` | `state_07_page_plan.json` を差し替え、全 PageBeat / PageRender state を削除 + `state_final.json` 削除 → 全ページ再生成 |
| `--inject-page-beat N=path` | `page_beats/page_beat_NNN_vNNN.md` に保存、`state_08_page_beat_NN.json` を更新、`state_09_page_NN.json` を削除 + `state_final.json` 削除 → 該当ページの PageRender だけ再走（新 versioned `pages/page_NNN_vNNN.png`） |

これにより通常操作はユーザーが波及無効化を意識せずに済む。低レベル復旧（手動 invalidation）が必要な場合のみ state ファイルを直接 `rm` する。`state_final.json` の削除はどの inject でも必須。

---

## ディレクトリ構造（実装側）

```
src/mangaka/
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── domain.py
├── errors.py
├── result.py
├── pipeline.py
├── persistence.py
├── logging.py
├── layers/
│   ├── plot.py
│   ├── backstory.py
│   ├── mpbv.py
│   ├── stylist.py      # text + image sub-step (Character/Location より前)
│   ├── character.py           # text + image sub-step
│   ├── location.py            # NEW: text + image sub-step
│   ├── page_plan.py           # NEW: arc + page_outline を出力 (旧 chapter/timeline 統合)
│   ├── page_beat.py           # NEW: scene.py の置き換え (exact_dialogue なし)
│   └── page_render.py         # NEW: 画像生成のみ
├── llm/
│   ├── client.py
│   ├── prompts.py
│   └── retry.py
├── image/                     # NEW
│   ├── client.py              # ImageClient Protocol + OpenAIImageClient
│   ├── ref_builder.py         # ref 予算アロケータ
│   ├── prompts.py             # 画像生成プロンプト合成
│   └── retry.py
└── export/
    └── pdf.py                 # A5 portrait 148×210mm, RTL viewer preferences
    # images.py は v1 では作らない。pages/ 自体が canonical artifact なので、
    # 画像連番が必要なら state JSON の Page.image_path から取れば十分

prompts/
├── 01_master_plot.md          # mlsg2 から流用 + 微調整
├── 02_backstory.md            # mlsg2 から流用
├── 03_mpbv.md                 # mlsg2 から流用
├── 04_stylist.md       # NEW: 絵柄ガイド (Character/Location より前)
├── 04b_visual_style_ref.md    # NEW: スタイル参照画生成
├── 05_character.md            # 「外見の具体描写を必ず含める」を追加
├── 05b_character_sheet.md     # NEW: 設定画生成プロンプト
├── 06_location.md             # NEW: ロケ抽出
├── 06b_location_sheet.md      # NEW: ロケ設定画生成
├── 07_page_plan.md            # NEW: arc 起承転結 + per-page outline 生成
├── 08_page_beat.md            # NEW: コマ/speech_intent/SFX の Markdown+frontmatter 生成
└── 09_page_render.md          # NEW: 1 ページ画像生成プロンプト (文字込み)
```

---

## 設定 (config.toml)

```toml
[general]
language = "ja"
runs_dir = "runs"

[llm_provider]
provider = "openai"             # v1 は OpenAI 固定。LLMClient Protocol は他 provider 追加に対応可能

[image_provider]
provider = "openai"             # gpt-image-2 のため OpenAI 固定 (v1)
model = "gpt-image-2"
default_size = "1024x1536"      # HD portrait, A5 比に近い
quality = "high"

[pdf]
page_size = "A5"                # A5 / B5 / A4 (ISO 216 比なので画像変換不要で切替可)
fit = "contain"                 # contain (白フチ許容) / cover (クロップ, 漫画では非推奨)
binding = "rtl"                 # rtl / ltr (v1 は rtl 固定運用)

[limits]
max_pages = 24
max_arc_phases = 5
max_panels_per_page = 8
max_main_characters = 8
max_locations = 6
max_retries = 3
max_image_retries = 2
max_parse_retries = 2

[assets]
character_sheets_per_char = 1
location_sheets_per_loc = 1

[image]
max_refs_per_page = 16
include_prev_page_ref = true
# PageRender prompt の文字数ガード (gpt-image-2 の prompt 上限は 32,000 chars)
max_prompt_chars = 20000        # hard fail line
warn_prompt_chars = 12000       # warning ライン、超えたら extract_visual_summary を短く
# Character/Location 視覚要約の出力上限 (extract_visual_summary 用)
max_location_summary_chars = 600
max_character_summary_chars = 300       # 1 キャラあたり
max_character_summary_total_chars = 1500  # 全キャラ合計

[models]
default = "gpt-5.4-mini"        # 軽量・高速、ほとんどの層のデフォルト
validation = "gpt-5.4"          # MPBV 等 reasoning が効く層用
naming = "gpt-5.4-mini"         # run 名生成、軽量で十分

[retry]
max_retries = 3
initial_delay = 1.0
max_delay = 60.0
exponential_base = 2.0

# Layer-specific (mlsg2 と同じパターン)
# OpenAI Responses API での reasoning は thinking=true 時に reasoning_effort で指定。
# Anthropic の thinking_budget (token 数) とは異なる concept なので、LLMClient 実装側で
# thinking=true → reasoning_effort="high"/"medium" のような mapping を行う。
[layers.plot]
model = "gpt-5.4-mini"
temperature = 1.0
max_tokens = 48000
thinking = false                # mini は thinking なしで運用

[layers.backstory]
model = "gpt-5.4-mini"
temperature = 0.9
max_tokens = 48000
thinking = false

[layers.mpbv]
model = "gpt-5.4"
temperature = 0.7
max_tokens = 64000
thinking = true                 # 矛盾検出のため reasoning ON
reasoning_effort = "high"

[layers.stylist]
model = "gpt-5.4-mini"
temperature = 0.7
max_tokens = 8192
thinking = false

[layers.character]
model = "gpt-5.4-mini"
temperature = 1.0
max_tokens = 16000
thinking = false

[layers.location]
model = "gpt-5.4-mini"
temperature = 0.9
max_tokens = 16000
thinking = false

[layers.page_plan]
model = "gpt-5.4"
temperature = 0.7
max_tokens = 16000
thinking = true                 # arc 配分・page_outline の整合性検証
reasoning_effort = "medium"

[layers.page_beat]
model = "gpt-5.4-mini"
temperature = 0.7
max_tokens = 16000
thinking = false
```

---

## v1 スコープ外（明示的に含めない）

将来検討する余地はあるが、初版では複雑度が上がるため**含めない**:

| 項目 | 含めない理由 | 拡張時の影響 |
|---|---|---|
| **Lettering / Compose 層** | gpt-image-2 が「マンガ」の知識で吹き出し・手書き文字・SFX を込みで描いてくれる。完全な文字精度を求めないので画像モデル任せ。Lettering は空バルーン検出・座標推定・縦書き日本語フォント配置という別プロジェクト規模 | PageRender の後に層を追加、PageBeat に optional な `exact_dialogue` / `balloon_hints` を schema migration で足す |
| **見開き (spread page)** | アスペクト比・製本・スキーマが全部複雑化 | Page に `format: portrait \| spread` を追加 |
| **縦スク (webtoon)** | コマ密度・「ページ」概念・出力レイアウトが別物 | output_format mode として並列追加 |
| **LTR (英訳輸出)** | 現状は日本語固定で十分 | RTL に関わる 3 箇所に flag を入れる |
| **モノクロ化 / トーン処理** | gpt-image-2 のフルカラー出力をそのまま使う | 後処理パイプラインを足す |
| **キャラの表情差分設定画** | v1 は基本立ち絵 1 枚のみ。表情は PageRender 時に gpt-image-2 が補完する想定 | `character_sheets_per_char` を上げ、設定画生成プロンプトに表情指示バリエーションを追加 |
| **キャラのバリエーション設定画 (衣装替え・成長)** | 短編なら基本起きない | Character に `variants: list[CharacterVariant]` を追加 |
| **ContinuitySlice (衣装・怪我・持ち物の連続性)** | 「ちょいおもろい」ゴールなら設定画 + 直前ページ ref で十分。長編で重要になる | PagePlan 横に独立した `Continuity` 層を新設、Character variants と連動 |
| **視覚的整合性 Validator** | 全ページ通しでブレを検出する MPBV 的検証 | mpbv 層の後ろに `visual_mpbv` を追加 |
| **ページ単位リテイク UI** | CLI で十分 | エクスポート抽象は維持されるので大きな変更不要 |
| **画像コスト上限ストッパ** | v1 は手動管理。リテイク多発時の安全網は欲しい | `limits.max_image_calls` を実装 |
| **自動 invalidation cascade** | v1 は手動 state ファイル削除で十分 | 依存グラフを実装に落とす |

---

## 残る議論ポイント

アーキテクチャ大枠は固まり、スキーマも `docs/SCHEMA.md` で定義済み。実装着手前に残るのは細部のみ:

1. **frontmatter / Stylist セクション抽出の実装選定**: `python-frontmatter` 直接利用か、ad-hoc regex か、markdown-it ベースか
2. **PageRender prompt 上限の実測微調整**: 初期値 `image.max_prompt_chars = 20000` / `warn_prompt_chars = 12000` は gpt-image-2 の硬い上限 32k に対する安全マージン。PoC で必要なら調整
3. **Ref Builder の登場順テスト**: ref を numbered で「1 枚目はスタイル、2 枚目はロケ...」と prompt で参照したとき、gpt-image-2 が順序を尊重するかの実測

実装に進める状態。

---

## 参照

- `/Users/dux/repos/mlsg2/ARCHITECTURE.md` — 流用元の設計詳細
- gpt-image-2 API: https://developers.openai.com/api/docs/models/gpt-image-2
- OpenAI Image generation guide (Limitations 節): https://platform.openai.com/docs/guides/image-generation
