あなたは漫画の構成作家です。MPBV、Style Guide、登場キャラクター、舞台ロケーションを踏まえて、この短編漫画の **PagePlan** を JSON で出力してください。

PagePlan は次の 2 つで構成されます:

1. `arc` — 起承転結のフェーズ分割 (典型 3〜5 個)
2. `page_outline` — 各ページの軽い outline (全 `total_pages` ぶん、ページ番号順)

# Input: MPBV (final)

{{ mpbv }}

---

# Input: Style Guide (narrative sections に注目)

{{ stylist }}

---

# Input: 登場キャラクター ID 一覧

{{ character_ids_block }}

---

# Input: 舞台ロケーション ID 一覧

{{ location_ids_block }}

---

# **MPBV のページ配分提案は無視する**

MPBV 内の「ページ配分の案」「全 N ページ」「起 X-Y / 承 X-Y / ...」のような **ページ数指定は単なる初稿の目安にすぎず、実際の beat 数より多めに膨らんでいます**。盲信しないでください。

あなたの責任は、MPBV のページ提案を捨て、ストーリーが本当に必要とする distinct な beat を数え直し、それを適切なページ数に割り当てることです。

**`total_pages` の選び方の目安**:
- `max_pages = {{ max_pages }}` は **上限** であって target ではありません
- 典型的に、実際に distinct な beat があるのは max_pages の **60-75% 程度** (例: max_pages=8 なら **5-6 ページが target**, 7-8 まで許容)
- max_pages=24 のような大きい値でも、ストーリーに本当に必要な数で止めてよい (典型 15-18 ページ)

**padding 禁止**: 前ページのビートを言い換えてページを埋めない。「観察」と「説明」を別ページに分けない、「同じ行動を別の場所でもう一度やる」は禁止。隣接ページが互いの言い換えになっていないか自己チェックしてから出力。

phase 命名は「起承転結」のような generic でも、`導入 / 遭遇 / 揺らぎ / 修正` のような story-specific でも、構造に合う方を選んでください。

# 制約

- 総ページ数 `total_pages` は **{{ max_pages }} 以下**
- `arc` の長さは **{{ max_arc_phases }} 以下**
- ページは 1 から始まる連番、歯抜けなし
- `arc` 全体で隣接フェーズの `end_page + 1 == 次の start_page`、最初の `start_page == 1`、最後の `end_page == total_pages`
- 各 `page_outline[*].phase` は `arc[*].phase` のいずれかと **完全一致**する文字列
- 各 `page_outline[*].character_ids` は上に列挙された **既存のキャラ ID** のみ使用
- 各 `page_outline[*].location_id` は上に列挙された **既存のロケ ID** のみ使用
- 出力は **JSON のみ**、コードフェンス（```json ... ```）に包んで返してください。前後に解説テキストは不要です

# 出力フォーマット (例)

```json
{
  "total_pages": 8,
  "arc": [
    {
      "phase": "セットアップ",
      "start_page": 1,
      "end_page": 2,
      "summary": "アリスの日常と違和感の提示"
    },
    {
      "phase": "対立",
      "start_page": 3,
      "end_page": 5,
      "summary": "ボブの登場と再燃する言い争い"
    },
    {
      "phase": "クライマックス",
      "start_page": 6,
      "end_page": 7,
      "summary": "本音の吐露"
    },
    {
      "phase": "結末",
      "start_page": 8,
      "end_page": 8,
      "summary": "静かな和解"
    }
  ],
  "page_outline": [
    {
      "page_number": 1,
      "phase": "セットアップ",
      "summary": "アリスが屋上で街を見下ろし、過去を思い出す",
      "character_ids": ["alice"],
      "location_id": "rooftop_morning"
    },
    {
      "page_number": 2,
      "phase": "セットアップ",
      "summary": "回想で子供時代のアリスとボブを描く",
      "character_ids": ["alice", "bob"],
      "location_id": "schoolyard"
    }
  ]
}
```

短編としての密度と「ちょいおもろい」テンポを意識して、**各ページに 1 つの distinct な beat** を割り当ててください。複数 beat を 1 ページに詰めるより、ビートが少なければページ数を減らす方が良い。
