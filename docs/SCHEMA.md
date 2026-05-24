# mangaka Schema

各レイヤーの入出力フォーマットを詳細に定義する。`ARCHITECTURE.md` がアーキテクチャの「形」を扱うのに対し、本書は「データの中身」を扱う。

---

## 1. 設計原則

### フォーマット選択

各レイヤーの永続化フォーマットは用途で決める:

| レイヤー | フォーマット | 理由 |
|---|---|---|
| Plot, Backstory, MPBV | Markdown | 自由記述、人間レビュー可能 |
| Stylist | Markdown | 絵柄ガイド、自由記述 |
| Character, Location | Markdown | テキスト中心、人間レビュー可能 |
| PagePlan | JSON | 数値・ID 中心 (`total_pages`, `arc[]`, `page_outline[]`)、構造化処理 |
| **PageBeat** | **Markdown + YAML frontmatter** | 日本語自由記述が中心 (visual, speech text, sfx)、ID 部分のみ構造化 |
| PageRender | 画像 (PNG) | gpt-image-2 出力 |

**PageBeat だけ「両方の良いとこ取り」**: ID と数値は frontmatter で堅く、日本語の演出記述は Markdown で柔らかく。

### キー言語と値言語

- **JSON / YAML キー**: 英語 snake_case (`page_number`, `character_ids`, `bubble_type`)
  - コードから扱いやすい
  - LLM 出力の構造が安定する（英語キーは混入リスクが低い）
- **値**: 日本語（コンテンツ）
  - プロンプト全体が日本語で一貫
  - 翻訳層を持ち込まない

### パース姿勢

- **JSON**: pydantic / dataclass で**厳格に検証**。malformed は `max_parse_retries` 回まで LLM に再生成依頼
- **Markdown / frontmatter**: **構造パースは tolerant、必須フィールドのバリデーションは厳格**
  - パース時: 表記揺れ（余分な空白・bold 記号のバリエーション等）は許容、可能な限り抽出
  - PageRender 実行前: 各 Panel に必須フィールド（`Visual` / `Emotion`）が揃っているかバリデーション
  - 必須欠落があれば `max_parse_retries` 回まで LLM に再生成依頼。**panel スキップではなく retry**
  - 理由: 1 ページの中でコマ欠けは絵として目立つ。tolerant parse は「format violation の救済」用であって、欠落フィールドの救済ではない

### ID 規約 (概観、詳細は §2)

- すべての ID は **snake_case の安定 slug**
- LLM が最初に該当オブジェクトを定義した時点で ID を発行し、以降変更しない
- ID は英数字 + アンダースコアのみ（日本語 ID は使わない、parse 安定性のため）

---

## 2. ID 規約

### character_id

- フォーマット: `[a-z][a-z0-9_]*`（先頭は小文字、英数字 + アンダースコア）
- 長さ: 2〜32 文字
- 生成元: キャラクター名から
  - ラテン文字名がある: 小文字化 (`Alice` → `alice`)
  - 日本語名のみ: romaji slug (`山田太郎` → `yamada_taro`)
  - 同名の脇役が複数: 役割 suffix (`yamada_father`, `yamada_son`)
- 予約 ID:
  - `narrator` — **Character として登録は禁止**だが、PageBeat の Speech 行の `speaker_id` ではナレーション擬似話者として**使用可**。Character 層には登場しない特殊話者
  - `self`, `none`, `null` — 全用途で使用禁止
- 一度確定したら**変更不可**（アセット ファイル名・state JSON で参照されるため）

LLM プロンプト側で「最初のキャラクター層出力時に ID を確定し、以降の全レイヤー出力でこの ID を使うこと」を厳命する。

### location_id

- フォーマット: 同上 `[a-z][a-z0-9_]*`
- 時間帯バリエーションは ID にエンコードする
  - 例: `rooftop_morning`, `rooftop_night`, `classroom_3a`
  - 「同じ場所だが時間が違う」場合、別の location_id として扱う
  - 同じ背景設定画を流用したい場合は location 間で `base_location_id` を参照する（将来拡張）
- v1 では時間バリエーションを別 location として扱う割り切り

### 検証ルール

- `MangaState` 内で character_id / location_id の集合は一意であること
- PageBeat の `character_ids` / `location_id` は、必ず Character / Location 層で定義済みの ID を参照すること
- 未定義 ID 参照は parse error として LLM に再生成を依頼

---

## 3. Stylist

mlsg2 の Stylist (文体) と mangaka の絵柄ガイドを **1 つの層に統合**した。`narrative sections` (物語のタッチ) と `visual sections` (絵柄) を 1 つの Markdown に持ち、**利用箇所ごとに必要なセクションだけを抽出して使う**。raw_markdown 全文を全プロンプトに流す設計ではない。

### テキスト出力 (StyleGuide.md)

`runs/{name}/state_04_stylist.json` に `raw_markdown` フィールドで保持。

#### 必須セクション (10 個)

**Narrative sections (1〜3)**: PageBeat 生成時に使う。物語のトーン・テンポ・セリフ感を定める。

```markdown
# Style Guide

## 1. ジャンル・トーン
（少年漫画 / 少女漫画 / 4 コマ / 青年向け / etc.、コメディ寄りかシリアス寄りか、世界観のテンション）

## 2. テンポと演出密度
（1 ページのコマ数の傾向、間のとり方、見せ場の頻度、緩急の付け方）

## 3. セリフとモノローグの傾向
（軽口 vs 重い対話、ナレーション枠の多さ、心の声の比重、口調全体の指針。
本文の傾向（軽口 vs 重い、丁寧 vs 砕け）と、ナレーション枠 / 心の声 / 通常会話の使い分け指針を書く。PageBeat 層がこれを踏まえて **短い verbatim セリフ** を生成する）
```

**Visual sections (4〜9)**: style_ref / Character sheet / Location sheet / PageRender が用途に応じてサブセットを使う。

```markdown
## 4. 全体の絵柄方針
（少年漫画系 / 少女漫画系 / 劇画 / SF / かわいい系 etc. + 1〜2 段落の説明）

## 5. 線
（太さ・強弱、デジタル/アナログ感、輪郭の処理、書き込み量）

## 6. 配色とトーン
（v1 はフルカラー。カラーパレットの方向性、彩度・明度の傾向、コントラスト感）

## 7. キャラデザインの傾向
（瞳の大きさ、髪型・髪色のテンション、デフォルメ度、表情の振り幅、体型バランス）

## 8. 背景と空間
（密度、パース、書き込みの粒度、奥行きの表現、空気感）

## 9. コマと演出
（コマ割りの傾向、効果線、ベタ・フラッシュ、擬音文字の入れ方）
```

**Universal (10)**: すべてのプロンプトで使う。

```markdown
## 10. 禁止事項
（避けたい表現。AI 的悪癖の排除、写実すぎる影付け、3D っぽいレンダリング、変な指、トーンのブレ、等）
```

LLM プロンプト側で「上記 10 セクションをすべて埋めること」を要求。各セクションは 100〜400 字程度を目安。

### プロンプト組み立てでのセクション分配

`Stylist.raw_markdown` を**まるごと全プロンプトに流さない**。各下流ステージは必要なセクションだけ抽出して使う:

| 利用箇所 | 使うセクション |
|---|---|
| `generate_style_ref`（style.png 生成） | 4, 5, 6, **10** |
| `generate_character_sheet`（キャラ設定画） | 4, 5, 6, **7**, **10** |
| `generate_location_sheet`（ロケ設定画） | 4, 5, 6, **8**, **10** |
| `generate_page_beat`（PageBeat の LLM 生成） | **1, 2, 3**, 9, **10** |
| `build_page_prompt`（PageRender prompt 組み立て） | 4, 5, 6, 9, **10** |

`10. 禁止事項` は universal（全プロンプトで使う）。

実装:
```python
SECTION_SETS = {
    "style_ref":       [4, 5, 6, 10],
    "character_sheet": [4, 5, 6, 7, 10],
    "location_sheet":  [4, 5, 6, 8, 10],
    "page_beat":       [1, 2, 3, 9, 10],
    "page_render":     [4, 5, 6, 9, 10],
}

def extract_sections(stylist_md: str, section_nos: list[int]) -> str:
    """Stylist Markdown から指定番号の `## N` セクションだけを連結して返す"""
    ...
```

### style_ref 画像生成

Stylist の text を元に gpt-image-2 で参照画像を 1 枚生成。`assets/style.png` に保存。

#### 生成プロンプト template

```
{extract_sections(stylist.raw_markdown, SECTION_SETS["style_ref"])}

---

上記の絵柄ガイドラインを表現する見本画像を 1 枚描いてください。
- 内容: キャラクター 1〜2 人が立つだけの単純なシーン（背景もシンプル）
- 目的: 線・色・トーン・デフォルメ感の見本として、後続のページ生成で参照される
- 漫画ページではなく、絵柄の見本イラスト

（出力サイズ・アスペクト比は `ImageClient.generate()` の `size` パラメータで指定。プロンプトには書かない）
```

`ImageClient.generate()` を呼ぶ（refs なし、テキストのみ）。

---

## 4. Character

### テキスト出力 (キャラクターごと)

`runs/{name}/state_05_character.json` の `characters` 配列の各要素に `description` フィールドで保持。

#### 必須セクション

各キャラクターの description は以下の Markdown 構造で記述:

```markdown
## {character_name} ({character_id})

### 基本情報
- 年齢: ...
- 性別: ...
- 役割: 主人公 / 相手役 / 敵 / 脇役 etc.

### 外見 (Visual Identity)
- 髪型・髪色: ...
- 目・瞳の色: ...
- 顔の特徴: ...
- 体型・身長感: ...
- 標準衣装: ...
- 持ち物・小道具: ...
- 識別ポイント（このキャラを一目で識別する 1 ポイント）: ...

### 性格と口調
- 性格: ...
- 口調・話し方: ...
- 一人称・二人称: ...
- 口癖（あれば）: ...

### 物語上の役割
- このキャラがこの物語で果たす機能: ...
- 主人公との関係: ...
```

**「外見」セクション**は設定画生成プロンプトに直接流すので、視覚的に明確に書く（「優しそうな目」より「やや垂れ目、瞳に光が強い」のような具体表現を要求）。

### sheet 画像生成

#### 生成プロンプト template

```
{character.description の「外見」セクション}

---

{extract_sections(stylist.raw_markdown, SECTION_SETS["character_sheet"])}

---

上記の外見指定のキャラクター 1 人を、漫画キャラ設定画として描いてください。
- 白背景
- 立ち絵、正面、A ポーズ（両手を軽く広げた立ち姿）
- 全身が入る構図
- 表情は標準（無表情〜薄い笑み）
- スタイル: 参照画像 1 枚目（style.png）の絵柄と、上記の絵柄指示の両方に従う

このキャラクターを以降のページ生成で何度も参照するので、特徴がはっきり分かるように描いてください。
```

`ImageClient.edit()` を呼ぶ。`refs=[state.stylist.style_ref_path]`, `base=None`。

保存先: `assets/characters/{character_id}.png`（初回生成）。`--inject-character-sheet` 経由の差し替え時は `{character_id}_v002.png`, `{character_id}_v003.png` のように versioned 保存し、旧版は disk に残る（ARCHITECTURE.md「アセットの扱い」参照）。

---

## 5. Location

### テキスト出力 (ロケーションごと)

`runs/{name}/state_06_location.json` の `locations` 配列の各要素に `description` フィールドで保持。

#### 必須セクション

```markdown
## {location_name} ({location_id})

### 基本情報
- 種別: 屋内 / 屋外 / 自然 / 人工 / etc.
- 時間帯: 朝 / 昼 / 夕 / 夜 / 任意

### 視覚的特徴
- 全体の印象: ...
- 構造・広さ・天井の高さ: ...
- 主要なオブジェクト・家具・地形: ...
- 色味・光の方向: ...
- 識別ポイント（この場所を一目で識別する 1 ポイント）: ...

### 雰囲気
- 空気感: ...
- 音や匂いの想定: ...
- このロケーションが演出するムード: ...
```

### sheet 画像生成

#### 生成プロンプト template

```
{location.description の「視覚的特徴」セクション}

---

{extract_sections(stylist.raw_markdown, SECTION_SETS["location_sheet"])}

---

上記のロケーションを、漫画背景の設定画として描いてください。
- キャラクターは描かない（背景のみ）
- ロングショット〜アイレベルで、空間の全体像が把握できる構図
- スタイル: 参照画像 1 枚目（style.png）の絵柄と、上記の絵柄指示の両方に従う

このロケーションを以降のページ生成で何度も参照するので、空間の特徴がはっきり分かるように描いてください。
```

`ImageClient.edit()` を呼ぶ。`refs=[state.stylist.style_ref_path]`, `base=None`。

保存先: `assets/locations/{location_id}.png`（初回生成）。`--inject-location-sheet` 経由の差し替え時は `{location_id}_v002.png` のように versioned 保存。

---

## 6. PagePlan

PagePlan は **JSON** で持つ。20 ページ規模の短編漫画には章 (Chapter) 概念が重すぎるため、**1 作品 = 1 PagePlan = ページ配分表**として持つ。Timeline も短編では redundant (PageBeat 生成時に直前 1〜2 ページの Markdown を context で読めば連続性は保てる) なので、v1 では PagePlan に統合・吸収する。

### 永続化

PagePlan は 1 作品で 1 個。state ファイル名は `runs/{name}/state_07_page_plan.json` (Chapter 時代の `_NN` suffix は不要)。MangaState 全体のスナップショットとして書かれ、その中の `page_plan: PagePlan | None` フィールドに格納。

### スキーマ

```json
{
  "total_pages": 20,
  "arc": [
    {
      "phase": "セットアップ",
      "start_page": 1,
      "end_page": 5,
      "summary": "アリスが屋上で日々の違和感を抱える日常を見せる"
    },
    {
      "phase": "対立",
      "start_page": 6,
      "end_page": 11,
      "summary": "ボブが現れ、過去の言い争いが再燃する"
    },
    {
      "phase": "クライマックス",
      "start_page": 12,
      "end_page": 17,
      "summary": "アリスが本音を吐露し、二人が向き合う"
    },
    {
      "phase": "結末",
      "start_page": 18,
      "end_page": 20,
      "summary": "静かな和解と夕暮れ"
    }
  ],
  "page_outline": [
    {
      "page_number": 1,
      "phase": "セットアップ",
      "summary": "アリスが屋上の柵で街を見下ろし、ふと過去を思い出す",
      "character_ids": ["alice"],
      "location_id": "rooftop_morning"
    },
    {
      "page_number": 2,
      "phase": "セットアップ",
      "summary": "回想シーン、子供の頃のアリスとボブ",
      "character_ids": ["alice", "bob"],
      "location_id": "schoolyard"
    }
  ]
}
```

(`page_outline` は全 `total_pages` ぶん持つ。上は省略。)

### フィールド定義

#### PagePlan

| フィールド | 型 | 必須 | 説明 |
|---|---|:-:|---|
| `total_pages` | int | ✓ | 全話通算のページ数 (`limits.max_pages` 以下) |
| `arc` | list[ArcPhase] | ✓ | 起承転結のフェーズ分割 (典型 3〜5 個、`limits.max_arc_phases` 以下) |
| `page_outline` | list[PageOutline] | ✓ | 各ページの軽い outline。長さは `total_pages` と一致 |

#### ArcPhase

| フィールド | 型 | 必須 | 説明 |
|---|---|:-:|---|
| `phase` | str | ✓ | フェーズ名（例: "セットアップ" / "対立" / "クライマックス" / "結末"）。自由文字列、`page_outline[*].phase` の参照 |
| `start_page` | int | ✓ | このフェーズの開始ページ番号 (1 始まり) |
| `end_page` | int | ✓ | 終了ページ番号。`end_page >= start_page` |
| `summary` | str | ✓ | このフェーズで起きることの 1〜2 文要約 |

#### PageOutline

| フィールド | 型 | 必須 | 説明 |
|---|---|:-:|---|
| `page_number` | int | ✓ | 全話通算 (1 始まり) |
| `phase` | str | ✓ | 紐づく `arc[*].phase` の値 |
| `summary` | str | ✓ | このページで起きることの 1 文要約 (PageBeat 生成のシード) |
| `character_ids` | list[str] | ✓ | このページに登場するキャラ ID |
| `location_id` | str | ✓ | このページのメインロケ |

### バリデーション

- `total_pages <= limits.max_pages`
- `len(arc) <= limits.max_arc_phases`
- `arc` 全体で `start_page` 昇順、隣接 phase の `end_page + 1 == 次の start_page`、最初の `start_page == 1`、最後の `end_page == total_pages`
- `len(page_outline) == total_pages`、`page_outline[i].page_number == i + 1` (1 始まり連番、歯抜けなし)
- 各 `page_outline[*].phase` が `arc[*].phase` の値のいずれかに一致
- `page_outline[*].character_ids` / `location_id` が Character / Location 層で定義済みの ID を参照

違反時は LLM に再生成依頼（`max_parse_retries` 回まで）。

### Timeline は v1 では持たない

mlsg2 の TimelineSlice 構造 (キャラ別の時系列イベント) は v1 mangaka では**実装しない**。理由:

- 20 ページ短編なら、PageBeat 生成時に直前 1〜2 ページの Markdown を context で渡せば連続性は維持できる
- 衣装替え・怪我・持ち物などの continuity を細かく追う必要は短編にはほぼない
- レイヤーを 1 つ減らせる

将来長編に拡張する場合は `Continuity` 層 (mlsg2 TimelineSlice 相当) を PagePlan の横に追加する。ARCHITECTURE.md「v1 スコープ外」参照。

---

## 7. PageBeat

PageBeat はページ単位の「何を描くか」の指示書。**Markdown + YAML frontmatter** で書く。

### 永続化

PageBeat は **2 つのアセット**として保存される:

- `runs/{name}/page_beats/page_beat_NNN.md` — canonical な Markdown ファイル（人間レビュー対象、直接上書きはしない）
- `runs/{name}/state_08_page_beat_NN.json` — parsed 構造 + md_path 参照（state JSON は path と要約のみ持つ）

state JSON の例:
```json
{
  "page_number": 5,
  "phase": "対立",
  "location_id": "rooftop_morning",
  "character_ids": ["alice", "bob"],
  "mood": "緊迫から再会へ",
  "panels": [/* パース済み Panel 構造 */],
  "md_path": "page_beats/page_beat_005.md"
}
```

編集ワークフロー:
1. PageBeat 層が `.md` を生成 → parse → state JSON 保存
2. ユーザーは `page_beats/page_beat_NNN.md` をコピーして編集（canonical file は上書きしない）
3. `--inject-page-beat 5=path/to/edited.md` で再パースし、`page_beats/page_beat_NNN_vNNN.md` のような新パスに保存する
4. `state_08_page_beat_NN.json` の `md_path` を新パスへ更新
5. `state_09_page_NN.json` を削除 + `state_final.json` を削除（stale `image_path` 拾い防止）
6. pipeline 再走 → 該当ページの PageRender だけ実行、新 `pages/page_NNN_vNNN.png` に保存

これは ARCHITECTURE.md の「アセット永続化レイアウト」と「`--inject-*` の挙動」と整合する（画像と同じく path 参照モデル、すべて versioned）。

### ファイル形式

```markdown
---
page_number: 5
phase: 対立
location_id: rooftop_morning
character_ids: [alice, bob]
mood: 緊迫から再会へ
continuity_note: 前ページから時間連続、朝日が差してきた
---

## Panel 1 [size: regular]

**Visual**: アリスが屋上の柵を握りしめ、街を見下ろしている。風で髪が揺れる。

**Camera**: ミドルショット、背後から
**Emotion**: 決意

**Speech**:
- [alice / inner_monologue / 静か] 今日こそ、ちゃんと言う。

**SFX**:
- ヒュウ (風)

## Panel 2 [size: large]

**Visual**: ボブが屋上のドアを開ける。逆光で表情は影になっている。

**Camera**: アリス視点の正面・煽り
**Emotion**: 不意の登場

**Speech**:
- [bob / dialogue / 落ち着いた] アリス。

**SFX**:
- ガチャ (ドアが開く音)

## Panel 3 [size: regular]

**Visual**: アリスが振り返り、目を見開く。

**Camera**: バストアップ、正面
**Emotion**: 驚きと動揺

**Speech**:
- [alice / dialogue / 震え] ボ……ボブ、なんでここに。

**SFX**: なし
```

### Frontmatter スキーマ

| フィールド | 型 | 必須 | 説明 |
|---|---|:-:|---|
| `page_number` | int | ✓ | 全話通算のページ番号 (1 始まり) |
| `phase` | str | ✓ | PagePlan の `arc[*].phase` の値。任意文字列 (例: "セットアップ" / "対立" / "クライマックス" / "結末") |
| `location_id` | str | ✓ | このページのロケーション。Location 層で定義済みであること |
| `character_ids` | list[str] | ✓ | このページに登場するキャラ ID。Character 層で定義済み |
| `mood` | str | ✓ | ページ全体の感情トーン |
| `continuity_note` | str \| null | ✗ | 前ページとの繋ぎ・時間・状況の説明 |

`character_ids` の順序は「主要度順」とみなす。Ref Builder が ref 予算を超える場合、末尾から削る。

### Panel セクション

#### ヘッダ形式

```
## Panel {N} [size: {SIZE_VALUE}]
```

- `N`: 1 始まりの読み順 (右上 → 左下)
- `SIZE_VALUE`: `regular` | `large` | `wide`

#### サイズ enum

| 値 | 意味 |
|---|---|
| `regular` | 標準サイズのコマ。1 ページに 4〜6 個入る想定 |
| `large` | キメゴマ。ページの 1/3〜1/2 を占める |
| `wide` | 横長のコマ。ページ幅いっぱい、高さは半分以下 |

`wide` は v1 で 1 ページ内に収まる横長コマで、見開きは含まない（見開きは v1 スコープ外）。

#### 必須フィールド

| フィールド | 型 | 必須 | 説明 |
|---|---|:-:|---|
| `Visual` | 自由テキスト | ✓ | コマの絵的描写。1〜3 文、目安 80〜220 字 (8 コマ上限と合わせて 1 ページ分が肥大化しないように) |
| `Camera` | 自由テキスト | ✗ | アングル・寄り引き（例: ミドルショット、ローアングル） |
| `Emotion` | 自由テキスト | ✓ | コマの感情トーン |
| `Speech` | list 形式 | ✗ | 発話・モノローグ・ナレーション。詳細下記 |
| `SFX` | list 形式 \| `なし` | ✗ | 効果音・擬音 |

#### Speech 行のフォーマット

```
- [{speaker_id} / {bubble_type} / {register}] {text}
```

- `speaker_id`: Character 層で定義済みの ID、または `narrator`（ナレーション）
- `bubble_type`: enum (下記)
- `register`: 口調のトーン（自由テキスト、例: 「怒り」「震え」「無感情」「ささやき」）
- `text`: **吹き出しに描画される実セリフ文字列**。短く口語的に、30 文字以内。`"..."` や `「...」` で囲んでも可（parser が strip する）
- Speech 数に上限なし。コマの感情密度に応じて自由。`narration` は場面説明・時間経過・心情の exposition に使い、`dialogue` / `inner_monologue` と budget を分けて運用する

> **設計変更（PoC 2026-05-24）**: 当初は `intent`（意味記述）を渡して画像モデルにセリフを翻訳させる設計だったが、PoC で「意味記述まで律儀に画に書く」「短いセリフは正確に書ける」と分かったので **`text`（verbatim 短セリフ）方式に切り替えた**。詳細は `docs/PLAN.md` PoC ノート参照。

```
Speech がない panel:
**Speech**: なし
```

#### bubble_type enum

| 値 | 意味 |
|---|---|
| `dialogue` | 通常の会話バルーン |
| `inner_monologue` | 心の声・モノローグ（雲形・角バルーン） |
| `narration` | ナレーション枠（四角枠） |
| `shout` | 叫び・絶叫（爆発型・尖ったバルーン） |

LLM はこの enum 値を使って書く。gpt-image-2 が漫画知識でバルーン形状を描き分ける。

#### SFX 行のフォーマット

```
- {text} ({role})
```

- `text`: 実際に描かれる擬音（カタカナ・ひらがな等）
- `role`: 何の音か（自由テキスト）

SFX が無い panel:
```
**SFX**: なし
```

### パース挙動

パースとバリデーションは**2 フェーズ**に分かれる。「読み取りは tolerant、レンダー可能性判定は strict」。

#### Phase 1: パース (tolerant、極力保持)

- frontmatter は YAML としてパース。frontmatter 自体が malformed なら即 parse error
- Panel セクションは `## Panel N` ヘッダで分割
- **各 Panel の構造はベストエフォートで抽出**: 必須フィールドが欠落していてもとりあえずパースは通す（None / 欠落マーカーを入れて保持）
- Speech / SFX の行フォーマット違反は warning、その行だけスキップ
- panel 番号の歯抜けは warning として記録（例: Panel 1, Panel 3 がある時 Panel 2 欠如）

#### Phase 2: バリデーション (strict、レンダー前の関門)

PageRender 実行前に、パース結果を以下の基準でチェック:

- **frontmatter 必須フィールド** (`page_number`, `phase`, `location_id`, `character_ids`, `mood`) が揃っているか
- **各 Panel の必須フィールド** (`Visual`, `Emotion`) が揃っているか
- `character_ids` / `location_id` が Character / Location 層で定義済みの ID を参照しているか
- `speaker_id` が **定義済み Character ID または予約 ID `narrator`** であること（それ以外は失敗）
- `bubble_type` が enum 値（`dialogue` / `inner_monologue` / `narration` / `shout`）のいずれかであること
- panel 番号の歯抜けがないか（1 始まり連番）

**いずれかに違反 → validation 失敗**。`max_parse_retries` 回まで LLM に「以下の問題を修正して再出力」と依頼してリトライ。**panel スキップせず、必ず修正版を要求**。

理由: 漫画は 1 ページ中のコマ欠けが視覚的に致命的。tolerant parse の対象は「format violation の救済」（例: `**Visual**:` の前後の空白、`-` と `−` の混在）であって、欠落フィールドの救済ではない。

### 完全例（Panel 構造のフルセット）

```markdown
---
page_number: 8
phase: クライマックス
location_id: cafe_evening
character_ids: [alice, bob, mio]
mood: 三人の緊張がほどける
continuity_note: 前ページの言い争いの直後。沈黙の余韻
---

## Panel 1 [size: large]

**Visual**: アリスとボブがテーブルを挟んで向かい合い、ミオが少し離れた席で本を読んでいる。窓の外は夕暮れ。

**Camera**: 俯瞰、店内全体が見える広い構図
**Emotion**: 気まずい静寂

**Speech**:
- [narrator / narration / 静か] 言い争いから、もう一時間。

**SFX**:
- カチ……コチ (時計の音)

## Panel 2 [size: regular]

**Visual**: アリスがカップを両手で包んで俯いている。

**Camera**: バストアップ、正面
**Emotion**: 内省

**Speech**: なし

**SFX**: なし

## Panel 3 [size: regular]

**Visual**: ボブが意を決して顔を上げる。

**Camera**: バストアップ、正面
**Emotion**: 決意

**Speech**:
- [bob / dialogue / 静か] アリス、あのさ……。

**SFX**: なし

## Panel 4 [size: regular]

**Visual**: アリスがハッと顔を上げる。視線がぶつかる。

**Camera**: アリスの目元アップ
**Emotion**: 戸惑いと期待

**Speech**: なし

**SFX**:
- ドキ (心音)

## Panel 5 [size: regular]

**Visual**: ミオがちらりと二人を見て、本を閉じて静かに席を立つ。

**Camera**: ミオの背中越し、二人が遠くに見える
**Emotion**: 気遣い、退場の予感

**Speech**:
- [mio / inner_monologue / 優しい] ここは、いない方がいい。

**SFX**:
- スッ (本を閉じる)
```

---

## 8. PageRender Prompt

PageBeat (Markdown + frontmatter) を gpt-image-2 に投げる**日本語自然言語プロンプト 1 本**に変換する。

### プロンプト組み立てアルゴリズム

```python
def build_page_prompt(
    state,
    page_beat,
    labeled_refs: list[LabeledRef],   # 呼び出し側 (generate_page_render) で 1 回だけ計算して渡す
    config: MangakaConfig,            # image.max_* 系の上限値を参照する
) -> Result[str, MangaError]:
    parts = []

    # 1. ページ全体の指示
    # 出力サイズ・アスペクト比は ImageClient.edit() の size パラメータで制御。
    # プロンプトには書かない（モデルは出力サイズを決められないため）。
    parts.append("縦長の漫画ページを 1 枚描いてください。")
    parts.append("右上から左下の読み順、日本の漫画スタイル。")
    parts.append("")

    # 2. ロケーション
    loc = state.locations_by_id[page_beat.location_id]
    parts.append("【場所】")
    parts.append(extract_visual_summary(
        loc.description, max_chars=config.image.max_location_summary_chars))  # 視覚的特徴セクションを要約、上限あり
    parts.append("")

    # 3. 登場人物 (合計上限を厳守、超えたら末尾の脇役から削る)
    parts.append("【登場人物】")
    char_lines: list[str] = []
    used_chars = 0
    per_char_max = config.image.max_character_summary_chars
    total_max = config.image.max_character_summary_total_chars
    for char_id in page_beat.character_ids:   # character_ids 順 = 主要度順
        char = state.characters_by_id[char_id]
        summary = extract_visual_summary(char.description, max_chars=per_char_max)
        line = f"- {char.name}: {summary}"
        if used_chars + len(line) > total_max:
            break   # 合計超過、ここで打ち切り (Ref Builder と同じドロップ順)
        char_lines.append(line)
        used_chars += len(line)
    parts.extend(char_lines)
    parts.append("")

    # 4. ページ全体のムード
    parts.append("【このページの空気】")
    parts.append(page_beat.mood)
    if page_beat.continuity_note:
        parts.append(page_beat.continuity_note)
    parts.append("")

    # 5. コマ構成
    parts.append(f"【コマ構成】{len(page_beat.panels)} コマで構成。右上から左下の読み順:")
    parts.append("")
    for panel in page_beat.panels:
        parts.append(f"■ コマ {panel.panel_no} ({size_label(panel.size_hint)})")
        parts.append(f"  絵: {panel.visual}")
        if panel.camera:
            parts.append(f"  カメラ: {panel.camera}")
        parts.append(f"  感情: {panel.emotion}")
        for sp in panel.speech_intents:
            parts.append(f"  セリフ: {speaker_label(state, sp.speaker_id)} が{bubble_label(sp.bubble_type)}で発話 "
                         f"(口調: {sp.register})。文字:「{sp.text}」")
        for fx in panel.sfx:
            parts.append(f"  効果音: 「{fx.text}」（{fx.role}）")
        parts.append("")

    # 6. 参照画像の番号付き解説
    #    labeled_refs (Ref Builder の戻り値) を直接 iterate するので、
    #    label と実 refs の順序ズレが起きない (single source of truth)
    parts.append("【参照画像の構成】")
    for idx, ref in enumerate(labeled_refs, start=1):
        parts.append(f"- {idx} 枚目: {ref.label}")
    parts.append("")

    # 7. スタイル指示（visual + コマ演出 + 禁止事項のセクションのみ）
    parts.append("【絵柄と演出】")
    parts.append("参照画像のスタイル参照画の絵柄に従ってください。")
    parts.append(extract_sections(state.stylist.raw_markdown, SECTION_SETS["page_render"]))
    parts.append("")

    # 7. 文字について（重要な割り切り）
    parts.append("【文字について】")
    parts.append("吹き出し・ナレーション枠・効果音文字は、漫画として自然な"
                 "「雰囲気のある日本語」で描いてください。")
    parts.append("正確な文章の再現は不要です。"
                 "発話の意図と感情が伝わるような言葉を選んでください。")
    parts.append("読みやすさより、表情・構図・コマ割りのテンポを優先してください。")
    parts.append("")

    # 8. 禁止事項
    parts.append("【避けること】")
    parts.append("- 正確な文章再現にこだわって絵の質を落とすこと")
    parts.append("- コマ割りの読み順を破ること")
    parts.append("- 写実すぎる影付けや 3D っぽいレンダリング")

    prompt = "\n".join(parts)

    # 9. 長さガード (gpt-image-2 の prompt 上限 32,000 chars に対する安全マージン)
    n = len(prompt)
    if n > config.image.max_prompt_chars:
        return Failure(MangaError(
            kind=ErrorKind.PROMPT_TOO_LONG,
            message=f"PageRender prompt が {n} chars で上限 {config.image.max_prompt_chars} を超過。"
                    f"page_number={page_beat.page_number}。"
                    f"PageBeat の panel 長を縮める / config の summary 上限を絞る / max_prompt_chars 自体を見直すなど、上位での対処が必要",
        ))
    if n > config.image.warn_prompt_chars:
        logger.warning("page_render_prompt_large",
                       chars=n, page_number=page_beat.page_number,
                       warn_limit=config.image.warn_prompt_chars)
    return Success(prompt)
```

補助関数:

```python
def size_label(s: str) -> str:
    return {"regular": "標準サイズ", "large": "キメゴマ・大ゴマ",
            "wide": "横長の広いコマ"}[s]

def bubble_label(b: str) -> str:
    return {
        "dialogue": "通常の吹き出し",
        "inner_monologue": "心の声（雲形か角バルーン）",
        "narration": "ナレーション枠（四角枠）",
        "shout": "叫びの吹き出し（爆発型）",
    }[b]

def speaker_label(state, speaker_id: str) -> str:
    if speaker_id == "narrator":
        return "ナレーション"
    return state.characters_by_id[speaker_id].name  # 名前で参照
```

### 参照画像 (refs) の添付

プロンプト本体とは別に、Ref Builder (§9) が組み立てた画像群を `ImageClient.edit()` に渡す。`build_refs()` は **`generate_page_render` で 1 回だけ呼ぶ**。`build_page_prompt()` には引数で渡し、`ImageClient.edit()` には path のみ抽出して渡す:

```python
def generate_page_render(state, page_beat, img, config):
    labeled_refs = build_refs(
        state, page_beat,
        max_refs=config.image.max_refs_per_page,
        include_prev=config.image.include_prev_page_ref,
    )
    return build_page_prompt(state, page_beat, labeled_refs, config).bind(
        lambda prompt: img.edit(
            prompt=prompt,
            refs=[r.path for r in labeled_refs],
        )
    )
```

`LabeledRef` が prompt と refs の両方の **single source of truth**。プロンプト中の「N 枚目」と `refs[N-1]` が必ず一致する。順序ズレで gpt-image-2 が ref を取り違える事故を防ぐ設計。

---

## 9. Ref Builder

`src/mangaka/image/ref_builder.py` の優先度ロジック。

### 優先度ルール

ロケ・直前ページは「ページに 1 つしかない貴重なヒント」なので、**最初に枠を予約**してからキャラ枠に残りを割り当てる。

1. **style_ref**: 必須、絶対残す（1 枠）
2. **ロケ sheet**: 1 枠を予約
3. **直前ページ**: `include_prev_page_ref=true` の時のみ、1 枠を予約
4. **キャラ sheet**: 残り枠を `character_ids` 順（主要度順）で埋める。溢れた末尾は捨てる

つまり実効的な優先度は **style > loc > prev > キャラ (character_ids 順)** となる。アルゴリズム (§下記) と整合。

### 戻り値: `LabeledRef`

`build_refs()` は単なる `list[Path]` ではなく **`list[LabeledRef]`** を返す。各 ref に「これは何の画像か」のラベルを併せて持たせ、PageRender prompt builder が **同じリストを iterate して 1 つの真実 (single source of truth)** で番号付きに参照できるようにする。

これにより:
- canonical 順 (`style → loc → prev → chars`) が一箇所で決まる
- `character_sheets_per_char > 1` に将来拡張しても、ラベルと実 ref の対応がズレない
- prompt 側の「N 枚目は X」が refs 配列の index と必ず一致する

```python
@dataclass(frozen=True)
class LabeledRef:
    path: Path
    label: str   # 例: "スタイル参照画", "場所「rooftop_morning」の設定画",
                 #     "登場キャラ「alice」の設定画", "直前ページ"
```

### アルゴリズム

```python
def build_refs(
    state,
    page_beat,
    *,
    max_refs: int,
    include_prev: bool,  # config default flipped to False after 2026-05-24 PoC
) -> list[LabeledRef]:
    refs: list[LabeledRef] = []

    # 1. style_ref は絶対残す
    refs.append(LabeledRef(
        path=state.stylist.style_ref_path,
        label="スタイル参照画",
    ))

    # 2. ロケ sheet を予約
    loc = state.locations_by_id[page_beat.location_id]
    refs.append(LabeledRef(
        path=loc.sheet_path,
        label=f"場所「{loc.name}」の設定画",
    ))

    # 3. 直前ページを page_number - 1 で ID ルックアップ。
    #    state.pages[-1] では --inject-page-beat 時に間違ったページを引きかねない
    prev_page = state.pages_by_number.get(page_beat.page_number - 1)
    if include_prev and prev_page and prev_page.image_path:
        refs.append(LabeledRef(
            path=prev_page.image_path,
            label="直前ページの画像（連続性のため、コマ割りは真似しない）",
        ))

    # 4. キャラ sheets を char.sheet_paths 単位で展開、残り budget で truncate
    char_budget = max(0, max_refs - len(refs))
    char_refs: list[LabeledRef] = []
    for char_id in page_beat.character_ids:
        char = state.characters_by_id[char_id]
        for sheet_path in char.sheet_paths:
            char_refs.append(LabeledRef(
                path=sheet_path,
                label=f"登場キャラ「{char.name}」の設定画",
            ))
    refs.extend(char_refs[:char_budget])

    assert len(refs) <= max_refs
    return refs
```

ガード:
- `state.pages_by_number` は `dict[int, Page]` で `page_number` 引きを O(1) に
- **`image.max_refs_per_page >= 2` は config 読み込み時に検証** (style + loc の最低 2 枠は必須)。Result-first 方針に従い、`build_refs()` 内では再検査せず、config validation の責務として上位で弾く
- キャラの `sheet_paths` 単位でループするので `character_sheets_per_char > 1` の将来拡張に対応

### 枠が足りない時のドロップ順

枠 16 を超えた場合、上のアルゴリズムにより**末尾のキャラから削られる**。loc / style / prev は予約枠なので削られない。

これにより、集合シーンで脇役 N 人が登場しても、主要キャラ（`character_ids` の先頭側）と空間ヒント（loc, prev）の ref は確実に保たれる。

### v1 では起きにくいシナリオ

- v1 の上限値 `max_main_characters = 8` × `character_sheets_per_char = 1` = 8 枚
- + style 1 + loc 1 + prev 1 = 11 枚
- → 16 枠に十分収まる

集合シーンで主要キャラ 8 人 + 脇役多数が同時登場する時のみアルゴリズムが効く。短編の通常運用では発動しない。

### 直前ページ ref が作る後続 PageRender 依存 (v1 既知の制約)

`include_prev_page_ref = true` の場合、ページ N の PageRender はページ N-1 の画像を ref として消費する。これにより**依存グラフ上「ページ N-1 → ページ N」の継続性エッジ**が生まれる。

**v1 では cascade しない**:
- `--inject-page-beat N` 等の inject はページ N のみ再生成し、N+1 以降は古い prev ref のままで放置
- 直前ページ ref は soft constraint であり、stale でも character/location sheet が固定なので絵は大崩れしない
- 完成原稿レベルの continuity を求めない「ちょいおもろい」ゴール的に許容範囲

詳細は `docs/ARCHITECTURE.md`「直前ページ ref と inject の cascade 問題」参照。

---

## 10. 残る議論

スキーマレベルは固まったが、実装時に詰めるべき細部:

1. **frontmatter の YAML パーサ選定**: `python-frontmatter` か `pyyaml` 直叩きか
2. **Panel フィールドのパース regex**: `**Visual**:` の表記揺れをどこまで許容するか
3. **`extract_visual_summary` / `extract_sections` の実装**: Character/Location テキストから視覚情報を要約する関数と、Stylist Markdown から指定 `## N` セクションを抽出する関数の実装方針（regex 簡易抽出 vs markdown パーサ vs LLM 要約依頼）
4. **PageRender prompt 上限の実測微調整**: ARCH の `image.max_prompt_chars = 20000` / `warn_prompt_chars = 12000` を初期値として PoC で実測、必要なら調整

---

## 参照

- `/Users/dux/repos/mangaka/docs/ARCHITECTURE.md` — 上位設計
- `/Users/dux/repos/mlsg2/prompts/` — 流用元プロンプト群
