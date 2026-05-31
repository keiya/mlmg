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
| Scene (散文) | **PageRender (gpt-image-2 で 1 ページ 1 画像、PagePlan.page_outline を直接 consume)** |
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
   ┌─────────────────────────────────────────┐
   │ 8. PageRender (gpt-image-2)             │
   │    入力: PagePlan.page_outline[n] +    │
   │          MPBV §1+§2 + stylist + 場所・  │
   │          キャラ設定画                   │
   │    出力: ページ画像 (文字込み, 1 枚 × P)│
   └─────────────────────────────────────────┘
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
| PageRender | page_outline[n] + MPBV §1+§2 + stylist + ref images | ─ | ページ画像 (1 枚) | ─ | (gpt-image-2) |

> **PageBeat layer 撤退 (PoC 2026-05-24)**: 当初は PagePlan と PageRender の間に PageBeat 層 (panel 単位の visual / camera / speech / sfx 構造化指示) を挟む設計だった。PoC で比較検証した結果、gpt-image-2 に **PagePlan.page_outline.summary を意味論の塊として直接渡す** 方が、コマ割り・カメラ・narration 配置を model 内蔵の manga 知識で自発的に決めてくれて、結果として **物語性が読者に伝わる**画になると判明したため撤退。詳細は本ドキュメント末尾「設計の進化」節を参照。

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

### セリフは gpt-image-2 に直接書かせる

PageRender prompt に MPBV §1+§2 + PagePlan の page_outline (1-2 文の beat summary) を渡すと、gpt-image-2 は **吹き出し・ナレーション枠・効果音文字を自発的に決めて描画する**。必要な key dialogue が page_outline.summary 内に引用形式で書かれていれば、モデルがそれを verbatim で吹き出しに採用する。

短い JP テキストの描画精度は十分実用的 (PoC 2026-05-24 確認)。文字精度より絵柄・表情・コマ割り・テンポ優先のゴール (「ちょいおもろい」) に合致。

### 将来の Lettering 移行余地

さらに高精度を求めるなら:
- PageRender prompt を「文字は空バルーンで」モードに切替
- PageRender の後に Lettering 層を挿入 (PIL/Pillow で吹き出し検出 → PagePlan.page_outline と MPBV から抽出した dialogue をフォント込みで合成)
- 縦書き日本語フォント・吹き出し座標推定・改行は別プロジェクト規模

各層は `MangaState → Result[MangaState, MangaError]` の純粋関数なので、間に層を挟むのはアーキ的に自由。

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
    image_path: Path | None   # PageRender 完了後にセット
    # 注: 旧 v0 設計では `beat: PageBeat` を持っていたが、PageBeat 層撤退と共に削除。
    # ページの意味論は `state.page_plan.page_outline[page_number-1]` から引く。
```

`MangaState` 上の `T | None` は「そのレイヤーがまだ実行されていない」という進捗表現に限る。外部 I/O の失敗や参照 ID の未発見は `None` で返さず、`Failure(MangaError)` に正規化する。各レイヤーは必要な前段 state を入口で検証し、欠けていれば typed error を返す。

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
├── state_09_page_render.json    # PageRender 完了後 (各 Page.image_path 入り、per-page checkpoint で逐次更新)
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
├── pages/                       # 最終ページ画像 (上書き禁止、inject 時は versioned 保存)
│   ├── page_001.png
│   ├── page_002.png
│   ├── page_002_v002.png        #   inject 後の再 render
│   └── ...
│
└── manga.pdf                    # エクスポート成果物 (A5, RTL)
```

### アセットの扱い

**Immutable な canonical artifacts**: `assets/`、`pages/` すべて上書きしない:

- **Pipeline は既存ファイルを上書きしない**: 通常の生成パイプラインは、一度作ったファイルを変更しない（変更すると以降のページでキャラ・絵がブレるため）
- **`--inject-*` は versioned path で保存**: `assets/characters/alice_v002.png`、`pages/page_005_v002.png` のような新パスに保存して state の参照先を更新する。既存ファイルは disk に残る
- リテイクしたい場合は、対応する `state_*.json` を削除して再生成する。既存ファイルが残っている場合は次の空き versioned filename に保存
- 旧版は run 内に残す。不要になった旧版の pruning は v2 候補

#### `save_bytes_strict` vs `save_bytes_versioned`

`src/mangaka/image/assets.py` は 2 つの save helper を出す:

- `save_bytes_strict(target, data)` — atomic `O_CREAT|O_EXCL`。target が既にあれば `IO_ERROR/FILE_EXISTS` で Failure。canonical pipeline (page_render, character, location) はこちらを使う。これにより、state JSON が「未レンダリング」と言っているのに disk に PNG がある（孤児）状態を **silent overwrite ではなく loud-fail で表面化**できる。並列 worker が同一 path に write レースしても syscall レベルで 1 つだけ成功する
- `save_bytes_versioned(target, data)` — 既存 file があれば `_vNNN` で逃げる。`--inject-*` CLI 専用。意図的な差し替えなので versioning が正しい挙動

`save_bytes` シンボルは互換のため `save_bytes_versioned` のエイリアスとして残しているが、新規コードは目的を明示する 2 つの名前のどちらかを使う。

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
    └─→ Location text ──┴─→ PagePlan → PageRender

Stylist text (narrative sections 1-3 + 4-6, 9)
    └─→ PageRender (LLM/画像生成時に直接読む)
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
| **Stylist (text)** | Character/Location の text + sheets, PagePlan, PageRender **全部** |
| **Stylist (style_ref 画像のみ)** | Character/Location の sheets, PageRender 全部（text は維持） |
| Character (text) | PagePlan, 該当キャラ登場の PageRender |
| Character (sheet 画像のみ) | 該当キャラ登場の PageRender のみ |
| Location (text) | PagePlan, 該当ロケ登場の PageRender |
| Location (sheet 画像のみ) | 該当ロケ登場の PageRender のみ |
| PagePlan | 全 PageRender |

text を差し替えると PagePlan の入力 (`mpbv + chars + locs`) が変わるため、PagePlan まで論理的に無効になる。**画像のみの差し替え**なら PageRender だけが影響を受ける。

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
# PagePlan, PageRender が全部論理的に無効になる。
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

- PageRender 層の完了条件が「`is_final_scene` flag」ではなく **「`page_outline` 配列を全部消化したら終了」** という明確な数値ベースになる
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

## 並列実行モデル

image 生成レイヤー (`page_render`, `character`, `location`) は `src/mangaka/image/parallel.py` の `run_image_jobs` を介して `ThreadPoolExecutor` で並列実行する。設計と段取りは `docs/plans/parallel_image_generation.md` が一次ソース。要点だけ:

### ImageJob と executor の不変条件

- 各 `ImageJob` は self-contained (prompt + refs → bytes → 決定的な `output_path`)
- worker は `MangaState` を触らない。state 反映は main thread の `on_complete` callback で逐次
- `on_complete` は **completion 順** で 1 つずつ呼ばれる。これにより state mutation はロック不要
- バッチ内の `output_path` は caller が distinct に保つ契約 (page_number / character.id / location.id は構造的に unique)。`save_bytes_strict` が atomic なので衝突しても overwrite ではなく loud-fail

### Drain protocol (`fail_fast=True` 時)

`ThreadPoolExecutor.cancel()` は実行中の future には効かないので、ナイーブに `break` すると in-flight worker が PNG を書いた直後に on_complete を skip して state-vs-disk 不整合になる。`run_image_jobs` は first failure 観測後も `as_completed` の iteration を継続し、各 in-flight worker の success 結果を on_complete で commit してから error を返す。**「PNG が disk に landed なら state は必ず追従している」**を保つ。

worker が exception を raise した場合 (= programmer bug)、および `on_complete` が exception を raise した場合 (= caller bug) も同じ drain path に巻き込む。`Exception` のみ catch する (`KeyboardInterrupt` / `SystemExit` は control flow として propagate する)。

### Resume と LLM-output caching (plan §3.8)

character / location レイヤーは LLM が stochastic なので、partial run 後の re-entry で text phase を再実行すると id が drift する → prefix-skip resume が壊れる。対策:

1. text phase 実行直後、image phase より**前に** `state.character_markdown` / `state.location_markdown` に raw markdown を入れて self-checkpoint
2. 再 entry 時は cached markdown を re-parse → 同じ `parsed_chars` / `parsed_locs`
3. `state.characters` / `state.locations` の既存 id を `already_done` set にして job 構築時に filter
4. on_complete は canonical (parsed) 順で append + sort (completion 順非依存)

page_render は LLM を使わないので caching 不要、image_path で skip するだけで idempotent。

### Worker 数と OpenAI Tier 5

`config.concurrency.image_workers` (default 16)。Tier 5 IPM=250 に対して `N × 60 / latency_sec` が定常 IPM。`latency≈50s` で N=16 は 19.2 IPM ≈ 7.7% 使用率、retry storm と並行 run のために 5x 以上の headroom。上限は soft 256 (アーキ的に >>32 は遊ぶ worker が出るだけ)。

### Retry jitter

`RetryHandler.calculate_delay` は ±25% uniform multiplicative jitter を持つ。N workers が同時 429 を観測したときに retry wave をばらつかせる。LLM 経路も jitter 通すが initial_delay 1s で ±0.25s なので debug 影響は無視できる。

### `include_prev_page_ref` との互換性

`image.include_prev_page_ref=True` (= ページ N の prompt に N-1 の rendered image を入れる) は並列前提とは構造的に不整合 (job batch を build する時点で N-1 はまだ rendered されていない)。`MangakaConfig` validator が `include_prev_page_ref=True && image_workers>1` を **load 時に拒否**する。連続性参照が必要なら `image_workers=1` に落とす。

### 撤退オプション (debug 用)

`image_workers=1` で全 image 層が serial 退行する。drain protocol / strict save / caching は同じコードで動く ので、並列固有のバグを疑った時は workers=1 で再現確認すれば良い。

### 関連設計判断

- **PageBeat 削除** (2026-05-24, `71f9119`): 並列化以前の話だが、page_render の入力を `PagePlan.page_outline.summary` 直接に変えたので「N-1 を見ないと N が描けない」依存が消えた → 並列化が embarrassingly parallel になった。`設計の進化` 節参照
- **Layer 単位の並列 (Character ∥ Location)**: 同じ MPBV + Stylist を消費し disjoint な state slice を produce するので fork-join できる。が、本セクションの per-sheet 並列で実用的速度は出ているので deferred。再検討条件は `docs/plans/parallel_image_generation.md` §9

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

mlsg2 と同じパターン。MPBV / Stylist / Character / Location / PagePlan のレビュー差し込みをサポート:

```bash
mangaka run --from runs/my_manga/ --inject-mpbv reviewed_mpbv.md
mangaka run --from runs/my_manga/ --inject-stylist style.md
mangaka run --from runs/my_manga/ --inject-character-sheet alice=alice_v2.png
mangaka run --from runs/my_manga/ --inject-location-sheet rooftop=rooftop_v2.png
mangaka run --from runs/my_manga/ --inject-page-plan page_plan.json
```

> ページ単位の差し込みは現状の設計には無い。ページごとのリテイクは `--inject-page-plan` で page_outline.summary を編集して該当ページの再生成、または該当ページの `state_09` を rm して `--from` で resume する。

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
| `--inject-page-plan path` | `state_07_page_plan.json` を差し替え、全 PageRender state を削除 + `state_final.json` 削除 → 全ページ再生成 |

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
│   ├── page_plan.py           # arc + page_outline を出力 (旧 chapter/timeline 統合)
│   └── page_render.py         # PagePlan.page_outline を直接 consume、画像生成のみ
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
└── (page_render の prompt はテンプレートではなく src/mangaka/image/prompts.py で動的合成)
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
fit = "contain"                 # v1 は contain 固定（cover は v2 候補）
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

```

---

## v1 スコープ外（明示的に含めない）

将来検討する余地はあるが、初版では複雑度が上がるため**含めない**:

| 項目 | 含めない理由 | 拡張時の影響 |
|---|---|---|
| **Lettering / Compose 層** | gpt-image-2 が「マンガ」の知識で吹き出し・手書き文字・SFX を込みで描いてくれる。PoC 2026-05-24 以降は `SpeechIntent.text` を verbatim で渡しているので「そこそこ正確」状態。完全精度には空バルーン検出・座標推定・縦書き日本語フォント配置という別プロジェクト規模が要る | PageRender の後に層を追加、既存の `SpeechIntent.text` を入力に使う (schema migration 不要) |
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

## 設計の進化 (撤退したレイヤー)

過去に実装したが運用検証 (PoC) で**撤退した設計**を、判断理由とともに記録する。再度同じ設計に戻りたくなった時の参照用。完全な history は `git log --grep "PageBeat"` などで辿れる。

### PageBeat layer (M2-M4 で実装、PoC 2026-05-24 で撤退)

**何だったか**: PagePlan と PageRender の間に挟まる、**ページごとのコマ割り構造化指示書**を生成するレイヤー。

- 入力: PagePlan.page_outline[N] + MPBV + Stylist
- 出力: `page_beats/page_beat_NNN.md` (YAML frontmatter + Markdown)
- 内容: panel ごとに `Visual` / `Camera` / `Emotion` / `Speech` (speaker_id, bubble_type, text, register) / `SFX` を構造化
- LLM: gpt-5.4-mini で生成、Phase 1 (tolerant parse) + Phase 2 (strict validation) でパース
- ドメイン型: `PageBeat`, `Panel`, `SpeechIntent`, `SFX` を `domain.py` に定義

**当初の動機**: 画像生成モデル (gpt-image-2) に「コマ番号・サイズ・カメラ・話者・セリフ」を明示的に指示することで、人間のネーム作家が設計するような細かい演出制御を実現する。schema を厳密にし、parser で検証することで「LLM の format drift で run 全体を壊さない」堅牢性を担保。

**何が問題だったか**:

1. **過剰な制御が表現を縛っていた** — gpt-image-2 は modern image model として **manga 構造の知識を内蔵**しており、コマ割り・カメラアングル・吹き出し配置を model 内部で十分に決められる。PageBeat の細分指示は、むしろ **物語の重みづけ・ナレーション・テンポ** といった manga craft 上の重要要素を画一化していた。

2. **情報密度のロス** — PagePlan の `page_outline.summary` (1-2 文の rich semantic beat) が PageBeat 層で `mood` (短文) + `continuity_note` + per-panel `Visual` に **lossy 圧縮**される。重要な「ページで読者に伝えるべきこと」が page_render に届かない構造。

3. **validation deadlock** — `PageBeat.character_ids ⊆ PagePlan.outline.character_ids` の strict subset 制約と panel-level speaker check の組み合わせで、LLM が cameo キャラを参照すると parse-retry が解決できない振動状態に陥る (PoC で複数回発生)。validator を緩和して回避したが、根本的には PageBeat 層の概念モデルと LLM 出力の自由度が噛み合っていなかった。

4. **アーキテクチャ複雑度** — schema (4 dataclasses) / parser (2-phase YAML + Markdown) / validator / .md persistence / state JSON 拡張 / inject CLI 設計 / 5+ tests を抱えていた。「画像生成までの距離を縮める」目的に対して overengineering。

**PoC 比較検証 (2026-05-24)**: vending_machine_kindness 7 ページの page 5 (時間 redo の中核シーン) を 2 通りで render:

- **Path A (PageBeat 経由)**: gpt-image-2 prompt 6337 chars。panel-level 指示通りに描画されるが、抽象的な mood / continuity_note のため「page 1 の見落としを page 5 で取り返す」物語構造が画から読み取れない。narration 枠が薄い。
- **Path B (PagePlan.page_outline 直結)**: gpt-image-2 prompt 3816 chars (40% 短い)。MPBV §1+§2 (logline / 異常性 / 世界ルール) + page_outline.summary を意味論の塊として渡し、コマ割り・カメラ・narration 配置を model に委任。結果、戸川を catch する瞬間が描かれ、ナレ枠で「同じ場所が戻る / 見落としが鮮になる / ここだ」と redo mechanic が明示され、page 1 へのコールバックが画として成立した。

Path B が **prompt 短く・コスト低く・物語性が高い** という三勝。

**撤退の代替策**: page_render layer が PagePlan.page_outline[N] を直接 consume する。gpt-image-2 prompt に渡す情報:
1. 物語の全貌 (MPBV §1+§2: logline / コアテーマ / 異常性 / 世界ルール)
2. このページの位置 (N/M、所属する arc.phase)
3. このページの骨格 (page_outline.summary)
4. 場所・登場キャラの visual reference
5. 絵柄と演出 (stylist sections)
6. 「あなたが決めること」 セクション (panel layout / camera / speech / narration / SFX をモデル判断に委任)

manga craft (コマ運び / 吹き出し / ナレ枠) は gpt-image-2 の内蔵知識に任せ、PageRender 層は **意味論を整理して渡す** 役割に純化。

**戻すべき条件**: もし将来「ページ単位の精緻な制御」「ネーム編集ワークフロー」が要件として戻ってきたら、PageBeat 相当を再導入する余地はある。ただし PoC データが「PageBeat なしの方が高品質」を示しているので、その時は **専用の `--scripted` mode** のような opt-in にすべき。デフォルト経路は PagePlan → PageRender のままで。

**git では**: `git log --grep "PageBeat"` で 2026-05 の修正 / 撤退コミットが追える。`src/mangaka/parse/page_beat.py` 等のコードは削除済みだが、git history には残る。

---

## 物語の明快さと絵柄のトーン適応 (プロンプト方針, 2026-06-01)

**背景**: mangaka の出力は初期から「漫画を読んでも難解」という課題があった。実ページ (vending_machine_kindness 等) を読み直し、root cause を上位プロンプト群に特定した。文字精度や page 数ではなく、**前提（設定）の作り込み過剰**が主因。

**難解化の連鎖**: 上位 3 プロンプトが直列で複雑さを増幅していた。

1. `01_master_plot.md` が「既存ジャンルを裏切る異常性」を**必須ノルマ**化 → 高コンセプト化
2. `02_backstory.md` が「固有名詞の確定」「ルールの厳格化（コスト定義）」でそれを**命名付きルール体系＋用語集＋年表**に膨張
3. `03_mpbv.md` が「情報量を落とすな・隠された真実は全保持」で**蒸留させず固定**

結果、7 ページの日常譚にも「管理局・通貨・5 つの相互ルール」が乗り、画像モデルが即興セリフで設定を説明しきれず難解化していた。

**方針 (マス受け = 一読で分かり感情が動く)**:

- **異常性ノルマを撤廃**。フックは多くて 1 つ、「もし〜だったら」を *1 回見れば分かる* 範囲に限定。ルールの体系化・管理組織・専門用語集・コスト計算は禁止。フック無しの純日常・コメディも歓迎。(`01`)
- **backstory は蒸留・最小化**。多くの短編は世界設定をほぼ必要としない。通貨/暦・政治/宗教・魔法システム・歴史年表・用語集は、現代日常/コメディ/現実ベースでは **「該当なし」を推奨**。ファンタジー/SF で本当に必要な分だけ。(`02`)
- **MPBV は一本の筋に蒸留**。余剰設定・脇ルール・隠し設定は捨てる。採用する「隠された真実」は最大 1 つ。(`03`)
- マス受けの正の基準を明示: 共感できる主人公＋一文で言える欲求 / 対立は目に見える外的出来事 / 読後感は 1 つ (笑う・沁みる・スカッと) / 絵だけで何が起きてるか分かる。
- **トーンのデフォルトは温かい・くすっと**だが、種が明確にシリアス/ホラー等ならそれを尊重。**種は多様 (ランダム 2 軸含む) を許容**できる ——「フック 1 つ」上限が重ジャンルでも 1 つの分かるフックに収束させるため。
- 旧 prompt の出力項目「異常性」は「**フック (任意・1 つ)**」に改称。

**絵柄のトーン適応**:

- Stylist は MPBV を入力に取るため、**上記で MPBV が軽くなれば絵柄も自動で軽くなる**（stylist は忠実に追従）。
- ただし gpt-image-2 は放置すると**写実・劇画に寄る**。よって stylist §1/§4 で「トーンから絵柄を決め切る」ことを要求し、トーン→絵柄の対応 (コメディ→デフォルメ強め・太線・非写実 / シリアス・SF・ホラー→劇画寄り＝従来の強みを温存 / 恋愛・エモ→繊細・大きい瞳) を明示。`04b_style_ref.md` でも非写実 (セル塗り/フラット) のベースラインを明示し、写実バイアスに引っ張られないようにする。
- **重い劇画はシリアス種用の 1 オプションとして温存**し、全体としては絵柄のレンジを広げる。

**壊さない不変条件**: MPBV §1/§2 と Stylist §1〜10 の `## N` 見出し骨格は維持（下流の `extract_sections` が依存。`SECTION_SETS` 参照）。改称・指示文の反転は見出し内のテキストに限る。

**この方針で変えないもの**: 画像上の文字精度（実測で概ね判読可、犠牲にしてよい方針は維持）/ ページ数はシードの濃さで決まる（`max_pages` は天井）—— いずれも本件と独立。

---

## 参照

- `/Users/dux/repos/mlsg2/ARCHITECTURE.md` — 流用元の設計詳細
- gpt-image-2 API: https://developers.openai.com/api/docs/models/gpt-image-2
- OpenAI Image generation guide (Limitations 節): https://platform.openai.com/docs/guides/image-generation
