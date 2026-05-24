あなたは漫画のネーム作家です。これから示す情報をもとに、**1 ページ分の PageBeat** を Markdown + YAML frontmatter で書いてください。

PageBeat はページ画像を生成する直前の指示書です。**実際のセリフ文字列は書かず**、`speech_intent`（何を伝えたいか）と `register`（口調のトーン）のみを書きます。文字描画は画像生成モデルが担当します。

# Input: MPBV (final)

{{ mpbv }}

---

# Input: Style Guide (narrative sections に注目: 1, 2, 3, 9, 10)

{{ stylist_sections }}

---

# Input: PagePlan (このページの outline)

ページ {{ page_number }} / 全 {{ total_pages }}

- phase: {{ phase }}
- ページ要約: {{ page_summary }}
- 登場キャラ: {{ character_ids_block }}
- 場所: `{{ location_id }}`

---

# Input: 登場キャラクター（プロンプトに使う ID 一覧）

{{ known_character_ids_block }}

# Input: 舞台ロケーション

{{ known_location_ids_block }}

{% if previous_page_beats %}
# Input: 直前ページ（{{ previous_page_count }} ページ分の PageBeat）

連続性参考用。コマ割りやセリフを真似する必要はありませんが、感情の流れと時間連続性を踏まえてください。

{{ previous_page_beats }}
{% endif %}

---

# 制約

- 出力は **Markdown + YAML frontmatter** のみ（前後の解説テキスト不要）
- frontmatter の `page_number` は **{{ page_number }}** に一致させる
- `phase` は **`{{ phase }}`** をそのまま使う
- `location_id` は上のロケ ID から **1 つ選択**
- `character_ids` は上のキャラ ID から **主要度順**に選択（このページで実際に登場するキャラだけ。最低 1 人）
- panel 数は **1〜{{ max_panels_per_page }}**、`panel_no` は 1 から連番
- 各 Panel は `Visual`（必須）と `Emotion`（必須）を含むこと
- Speech 行のフォーマット: `- [speaker_id / bubble_type / register] intent`
  - `speaker_id`: Character ID または予約語 `narrator`
  - `bubble_type`: `dialogue` / `inner_monologue` / `narration` / `shout` のいずれか
  - `register`: 口調のトーン（例: 「怒り」「震え」「無感情」「ささやき」）
  - `intent`: 何を伝えたいかの意味記述。**実際のセリフ文字列ではない**
- SFX 行のフォーマット: `- text (role)`（例: `- ヒュウ (風)`）
- Speech / SFX が無い panel は `**Speech**: なし` / `**SFX**: なし` と書く

# 出力フォーマット

以下は**構造の参考例**です。`location_id` / `character_ids` の値は上の outline で指定された **このページの実 ID** を使ってください（例で見せている `{{ location_id }}` / `character_ids` の値はあくまでこのページに固有の値）。Panel の中身（Visual / Speech 等）は outline と上の指示を踏まえてあなたが新規に書きます。

```markdown
---
page_number: {{ page_number }}
phase: "{{ phase }}"
location_id: "{{ location_id }}"
character_ids: {{ character_ids_yaml }}
mood: （このページ全体の感情トーン、自由記述）
continuity_note: （前ページとの繋ぎ・時間・状況の説明、不要なら省略可）
---

## Panel 1 [size: regular]

**Visual**: （このコマで何を絵として描くか、1〜3 文で具体的に）

**Camera**: （アングル・寄り引き、例: ミドルショット、ローアングル）
**Emotion**: （このコマの感情トーン）

**Speech**:
- [{{ primary_character_id }} / inner_monologue / 静か] （話者の心情・意図を意味記述）

**SFX**:
- （擬音）（音の種類）

## Panel 2 [size: large]

**Visual**: （次のコマの絵的描写）

**Emotion**: （感情トーン）

**Speech**: なし

**SFX**: なし
```

短編としての密度を意識し、1 ページに 3〜6 コマ程度を目安に。コマ運びは右上から左下、日本のマンガ流の読み順を尊重してください。
