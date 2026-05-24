# Plan: Parallel image generation

**Date**: 2026-05-24
**Scope**: PageRender + Character/Location sheet generation を thread pool で並列化する。
**Out of scope**: Character / Location レイヤー同士の fork-join (= 当初メモの #3) は遅延。最後に「将来展望」として残す。
**Assumed**: OpenAI Tier 5 (IPM=250, TPM=8M)。Tier 5 未満のレート制限は v1 で考慮しない。

---

## 1. ゴール

16-24 ページの run の wall-clock を **直列 10-25 分 → 並列 1-3 分** に短縮する。
副作用: 並列化したことで idempotency / resume / asset-immutability の不変条件が壊れない、を保証する設計にする。

### Non-goals

- asyncio への全面移行 (OpenAI SDK は blocking I/O、GIL 経由で OK)
- レイヤー単位の並列 (#3 は deferred)
- 投機実行 (失敗時の "念のため別バージョン" 等)
- API レートリミット動的検知 (Tier 5 の定常負荷では発火しない)

---

## 2. 同時実行数 (worker 数) の決定

### Tier 5 制限
- **TPM**: 8,000,000 — image prompt は ~5K token 程度なので全く効かない
- **IPM**: 250 images/min — これがバインディング

### 定常 IPM 計算
`steady_IPM = N_workers × 60 / latency_sec`

gpt-image-2 `quality=high` の実測 wall-clock は 30-60 秒/枚。worst-case 50 秒で計算:

| N workers | 定常 IPM | Tier 5 使用率 |
|-----------|----------|---------------|
| 8         | 9.6      | 3.8%          |
| **16**    | **19.2** | **7.7%**      |
| 24        | 28.8     | 11.5%         |
| 32        | 38.4     | 15.4%         |
| 64        | 76.8     | 30.7%         |

### 安全率の取り方

5x ヘッドルームを残す (= 使用率 <20%) と:
- リトライストーム時の余裕 (RetryHandler が複数 worker で同時発火しても捌ける)
- 並行 run (将来 CI とか) との衝突を吸収
- レイテンシ短縮 (medium quality, 1024x1024) で実効 IPM が跳ねても安全

→ **N=16 を既定値**にする。約 7.7% 使用率、5x 以上の余裕。

### 上限の現実的制約

1 つの run の総 image call 数は **page_render ~16-24 + character ~3-6 + location ~2-4 ≈ 25-35**。N>32 にしても遊ぶ worker が出るだけで、wall-clock は (page_render の) longest-tail に決まる。

config 経由で上げられるが、既定 16 が "そのまま使う設定" として最適。

### 設定ノブ

```toml
# config.toml — 新セクション
[concurrency]
# Worker count for parallel image generation (page_render, character sheets,
# location sheets). Defaults sized for OpenAI Tier 5 (IPM=250) with ~7.7%
# steady-state utilization, leaving headroom for retry storms and concurrent
# runs. Drop to 1 for serial debugging.
image_workers = 16
```

`ConcurrencyConfig` (Pydantic) を `MangakaConfig` に追加。

---

## 3. アーキテクチャ

### 3.1 設計原則 (CS hygiene)

| 不変条件 | どう守るか |
|---------|-----------|
| 各ジョブは pure (input prompt + refs → output bytes) | `ImageJob` を frozen dataclass で表現。worker は `MangaState` を読まない |
| State mutation は単一 thread のみ | worker は `Result[bytes, MangaError]` を返すだけ。state 反映は main thread |
| 既存ファイル上書きしない | **新規**: `save_bytes_strict` を導入 (atomic O_CREAT\|O_EXCL)。既存 `save_bytes` (=versioned) は `--inject-*` CLI 専用に格下げ。詳細 §3.7 |
| Resume 可能 | 各 job 完了直後に state checkpoint。次の job 開始前に永続化する。Character/Location は LLM 出力の raw markdown も状態に持つ (§3.8) |
| 有限時間で必ず終わる | `RetryHandler` がリトライ上限、ThreadPoolExecutor が worker 上限、ジョブ数有限 |
| Fail-fast でも完了済みは無駄にならない | **完了済みを失わない drain プロトコル**: 失敗を観測したら新規はキャンセル、ただし `as_completed` のループは継続して in-flight worker の成果に `on_complete` を流す。詳細 §3.6 |

### 3.2 新モジュール: `src/mangaka/image/parallel.py`

```python
"""Parallel image-job executor.

Stateless thread-pool wrapper around ImageClient. Each ImageJob is a
self-contained unit: prompt + refs → bytes → save to deterministic path.
The executor itself owns no MangaState — callers reduce the result list
into domain objects on the main thread.

Concurrency invariants:
- Workers never touch MangaState.
- Workers never share mutable data with each other.
- `on_complete` callbacks fire serially on the main thread in completion
  order — safe to use them for state checkpointing without locks.
- Distinct ImageJob.output_path per batch is the caller's responsibility
  (save_bytes is not atomic under concurrent same-target writes).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes
from mangaka.image.client import ImageClient
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


@dataclass(frozen=True)
class ImageJob:
    """A self-contained, deterministic image-generation unit.

    `id` is for logging only. `output_path` doubles as the idempotency key
    in the sense that the caller is responsible for not enqueuing a job
    whose target already exists in a way the caller wants to preserve.
    """
    id: str
    prompt: str
    refs: list[Path]
    output_path: Path
    size: str
    quality: str
    model: str
    # Opaque caller-supplied handle threaded through to ImageJobOutcome so
    # callbacks can recover the source domain object (PageOutline /
    # ParsedCharacter / ParsedLocation) without stringly-typed parsing of `id`.
    tag: object = None


@dataclass(frozen=True)
class ImageJobOutcome:
    job: ImageJob
    result: Result[Path, MangaError]  # saved path on success


def run_image_jobs(
    jobs: Sequence[ImageJob],
    img: ImageClient,
    *,
    max_workers: int,
    on_complete: Callable[[ImageJobOutcome], Result[None, MangaError]] | None = None,
    fail_fast: bool = True,
) -> Result[list[ImageJobOutcome], MangaError]:
    """Execute image jobs concurrently and aggregate outcomes. See §3.6
    for drain semantics under fail_fast."""
    ...
```

#### Submission order rule

Submit jobs in caller-provided order. Workers may complete in any order; `as_completed` yields in completion order. State mutation in `on_complete` is keyed by `tag` (the original domain object), not by completion order — so non-determinism in completion order does not leak into persisted state.

#### Why fail-fast as default

- gpt-image-2 calls cost \$0.21 each. If page 3 of 16 fails for a logic-bug reason (e.g. prompt > 20K chars), continuing to render pages 4-16 burns \$3 on a doomed run.
- The completed jobs are already on disk + state-checkpointed → `mangaka run --resume` (or `poc_continue.py`) skips them on retry.
- Transient failures (rate limit, network) are absorbed by `RetryHandler` inside `img.edit()` before they reach the executor → fail-fast only triggers on real terminal errors.

#### Why `on_complete` callback (not "collect all then commit")

- We want **per-job state checkpoint** (existing behavior: `page_render.py:157` saves state after each render). With parallel workers, the natural place is "in main thread after future completes."
- Serial `as_completed` iteration gives us this for free, no locking required.
- Callback signature returns `Result` so checkpoint failures propagate.

### 3.3 PageRender 改修

Before (`page_render.py`):
```python
for outline in outlines_sorted:
    # build prompt, call img.edit, save, update state, checkpoint
```

After:
```python
# 1. Build jobs upfront (skip already-rendered pages).
jobs: list[ImageJob] = []
for outline in outlines_sorted:
    if state has image_path for this page_number: continue
    refs = build_refs(...)
    prompt = build_page_prompt(...).unwrap_or_return()
    jobs.append(ImageJob(
        id=f"page_{outline.page_number:03d}",
        prompt=prompt,
        refs=[r.path for r in refs],
        output_path=run_dir / "pages" / f"page_{outline.page_number:03d}.png",
        size=config.image_provider.default_size,
        quality=config.image_provider.quality,
        model=config.image_provider.model,
        tag=outline,  # PageOutline — opaque handle threaded through to on_complete
    ))

# 2. Define on_complete: update state for the matching page_number, save checkpoint.
def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
    nonlocal current
    outline = cast(PageOutline, outcome.job.tag)  # no stringly-typed parsing
    idx = next(i for i, p in enumerate(current.pages) if p.page_number == outline.page_number)
    new_pages = [*current.pages]
    new_pages[idx] = replace(current.pages[idx], image_path=outcome.result.unwrap())
    current = replace(current, pages=new_pages)
    return save_state(current, checkpoint_path)

# 3. Execute.
exec_result = run_image_jobs(
    jobs, img,
    max_workers=config.concurrency.image_workers,
    on_complete=on_complete,
)
if isinstance(exec_result, Failure): return Failure(exec_result.failure())
```

**Idempotency**: pages with `image_path` set are filtered out in step 1. Re-run after partial failure picks up from `state_08_page_render.json`'s checkpoint — already-rendered pages skipped, only remaining jobs enqueued.

### 3.4 Character / Location 改修

Pattern parallels PageRender but with the **LLM-output caching** from §3.8 wrapping it. Layer flow:

1. **Cached LLM phase** (§3.8): if `state.character_layer.raw_markdown` set → reuse. Else: call LLM, persist `raw_markdown` to state checkpoint **before any image call**. Parse markdown into `parsed_chars`.
2. **Pre-flight** (existing): verify `### 外見` for each parsed char. Failure aborts before any image call.
3. **Resume filter**: `already_done = {c.id for c in state.character_layer.characters}`; `todo = [p for p in parsed_chars if p.id not in already_done]`.
4. **Job batch build**: one `ImageJob` per `todo` entry. `tag = parsed_char` (handle threaded through).
5. **Parallel execute**: `run_image_jobs(...)` with worker count from `config.concurrency.image_workers`.
6. **`on_complete`**: append the new `Character` to `state.character_layer.characters` in **canonical (parsed_chars) order**, not completion order. Each completion saves a state checkpoint.

**Output ordering**: `state.character_layer.characters` is sorted to match the order in `parsed_chars` (NOT completion order). The sort key is `parsed_chars.index(parsed_for_this_outcome)`. Race-free because sorting happens in `on_complete` (main thread only).

**Idempotency proof** (= §3.8 repeated for completeness):
- LLM call: once per layer lifetime. Cached via `raw_markdown`.
- Sheet renders: once per character id. `todo` filter via `already_done` set.
- Disk writes: `save_bytes_strict`, no version-up, fails loud if state-vs-disk drift.

Same shape for Location.

### 3.6 Drain protocol under fail-fast (critical correctness)

**Naive approach fails**: `ThreadPoolExecutor.cancel_futures=True` only cancels futures that have **not started executing**. Already-running workers keep running to completion. If we `break` out of the `as_completed` loop on first failure, in-flight workers will still write their PNGs to disk via `save_bytes_strict`, but `on_complete` never runs for them → state checkpoint doesn't include those pages → next resume sees orphan files on disk at canonical paths and the strict save fails immediately. Paid renders lost.

**Drain protocol**:

```python
with ThreadPoolExecutor(max_workers) as ex:
    futures = {ex.submit(_render_one, job, img): job for job in jobs}
    first_error: MangaError | None = None
    outcomes: list[ImageJobOutcome] = []

    for fut in as_completed(futures):
        job = futures[fut]
        # Cancelled future raises CancelledError; treat as a non-outcome
        # (the job never ran, no side-effect on disk).
        if fut.cancelled():
            continue
        # Convert to ImageJobOutcome, but DON'T re-raise exceptions —
        # workers always return Result, so .result() never throws unless
        # there's a programmer bug (which we want to surface).
        outcome = ImageJobOutcome(job=job, result=fut.result())
        outcomes.append(outcome)

        if isinstance(outcome.result, Success):
            if on_complete is not None:
                cb = on_complete(outcome)
                if isinstance(cb, Failure):
                    if first_error is None:
                        first_error = cb.failure()
                    if fail_fast:
                        _cancel_pending(futures)
                    # Continue draining: an on_complete failure on job N
                    # doesn't mean we should leak jobs N+1..M's bytes.
                    continue
        else:  # Failure
            if first_error is None:
                first_error = outcome.result.failure()
            if fail_fast:
                _cancel_pending(futures)
            # Still draining — see above

if first_error is not None and fail_fast:
    return Failure(first_error)
return Success(outcomes)
```

Where `_cancel_pending` calls `fut.cancel()` on every future not already done. Cancelled futures will surface from `as_completed` and get filtered by the `fut.cancelled()` guard.

**Guarantees**:
- Every successful image generation has either its `on_complete` called (committed to state) or its disk write skipped via cancel (never wrote bytes).
- No PNG is written to disk without state catching up to reference it.
- `first_error` is deterministic: the **earliest-completing** failure in `as_completed` order — which is what the user sees.
- KeyboardInterrupt during executor `__exit__` waits for currently-executing futures (this is Python ThreadPoolExecutor's default). For our latency profile (~50s/job × 16 workers) worst-case shutdown wait is one job's latency. Acceptable.

### 3.7 Strict save: `save_bytes_strict`

Current `save_bytes` calls `next_available_path` which silently versions up if `target` exists. Under parallelism + resume, this creates a TOCTOU race AND silently masks state-vs-disk drift.

**Refactor**:

```python
# src/mangaka/image/assets.py

def save_bytes_strict(target: Path, data: bytes) -> Result[Path, MangaError]:
    """Atomically write `data` to exactly `target`. Fails (no overwrite,
    no version-bump) if anything already exists at the path.

    Used by the canonical pipeline (page_render, character, location).
    A FILE_EXISTS failure here is a real signal: state expected `target`
    to be unwritten, but the filesystem disagrees. Either a prior failed
    run left an orphan (run state-recovery), or a programmer bug let two
    jobs target the same path (programmer error).
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # O_CREAT|O_EXCL: atomic test-and-create.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    except FileExistsError:
        return Failure(MangaError(
            kind=ErrorKind.IO_ERROR,
            message=f"refusing to overwrite existing asset at {target}",
            detail={"path": str(target)},
        ))
    except OSError as exc:
        return Failure(MangaError(
            kind=ErrorKind.IO_ERROR,
            message=f"failed to save asset: {exc}",
            detail={"path": str(target)},
        ))
    return Success(target)


def save_bytes_versioned(target: Path, data: bytes) -> Result[Path, MangaError]:
    """Versioned save for --inject-* CLI. Current `save_bytes` behavior."""
    ...  # existing implementation, renamed
```

`page_render`, `character`, `location` switch to `save_bytes_strict`. `--inject-*` CLI keeps `save_bytes_versioned`. Existing `save_bytes` symbol is removed (or kept as alias for `save_bytes_versioned` with a deprecation note for one cycle — TBD on impl).

**On `FILE_EXISTS`**: pipeline aborts with a clear error pointing the user at `fixup_page_render_state.py` (or equivalent for char/loc). This is the right loud-fail behavior — silent versioning hides bugs.

### 3.8 LLM-output caching for Character / Location resume

**Problem** (reviewer-flagged): `character` / `location` text-LLM calls are stochastic. On resume, re-running the LLM may produce different `id`s than the original markdown. If we did partial sheet rendering on the first run, the second LLM run's parsed IDs won't match the disk artifacts → prefix-skip breaks, orphan PNGs accumulate, the layer becomes non-idempotent.

**Fix**: persist the raw LLM markdown so resume is text-deterministic.

Schema additions:

```python
@dataclass(frozen=True, kw_only=True)
class CharacterLayerOutput:
    """Captured LLM output + parsed cast roster. State carries this so that
    re-entering the layer for the parallel image phase is deterministic."""
    raw_markdown: str
    # Empty list until at least one sheet is rendered; grows to len(parsed)
    # as parallel renders complete.
    characters: list[Character]

# state.characters is replaced by:
class MangaState:
    ...
    character_layer: CharacterLayerOutput | None = None
    location_layer: LocationLayerOutput | None = None
```

(or thread raw_markdown into existing fields without restructuring — exact shape TBD in impl. The invariant is "raw_markdown is persisted by the end of the LLM phase, before any image call.")

**Layer entry semantics**:

```python
def generate_character_layer(state, ...):
    if state.character_layer is not None and state.character_layer.raw_markdown:
        # Resume mode: reuse cached LLM output, re-parse, find unrendered chars
        parsed = parse_character_markdown(state.character_layer.raw_markdown).unwrap()
        already_done = {c.id for c in state.character_layer.characters}
        todo = [p for p in parsed if p.id not in already_done]
    else:
        # Fresh mode: LLM call + parse + persist raw_markdown immediately
        text = llm.complete(...).unwrap()
        parsed = parse_character_markdown(text).unwrap()
        state = state.with_character_layer(raw_markdown=text, characters=[])
        save_state(state, checkpoint_path)  # persist BEFORE any image call
        todo = parsed

    # Build ImageJobs for `todo`, run_image_jobs, on_complete appends to
    # state.character_layer.characters and saves checkpoint.
    ...
```

**Idempotency proof sketch**:
- After first LLM success: `raw_markdown` persisted. Subsequent re-entry skips LLM.
- After each sheet render: `characters` list grows by 1, checkpoint saved. `id` set is monotone.
- If resume after N/M sheets done: re-parse gives same `parsed` (deterministic from text). `todo` = parsed \ already_done. No orphans, no double work.
- LLM call costs once per layer, period.

Same shape for Location.

### 3.9 Failure semantics, surface area

| Scenario                                | Behavior                                                              |
|-----------------------------------------|-----------------------------------------------------------------------|
| 1 job rate-limited transiently          | RetryHandler retries inside img.edit. Other workers proceed.          |
| 1 job non-retryable error (prompt 400)  | Drain protocol §3.6: pending cancelled, in-flight drained through `on_complete`, first error returned. |
| `on_complete` Failure (e.g. disk full)  | Same drain protocol.                                                  |
| `save_bytes_strict` FILE_EXISTS         | Per-job Failure with clear message. Drain protocol applies.           |
| Worker thread raises unexpected Python  | `Future.result()` re-raises → contained in `run_image_jobs`, wrapped as `MangaError(INVALID_STATE, ..)`. Defense in depth — workers should always return Result. |
| KeyboardInterrupt during execution      | Executor `__exit__` waits for in-flight futures. Worst case ~1 job latency. State remains consistent (in-flight completions' on_complete still runs via drain). |

### 3.10 Retry storm mitigation (jitter)

Currently `RetryHandler.calculate_delay` has no jitter:
```python
delay = initial * (base ** attempt)
```

With N=16 workers all 429'ing simultaneously, all 16 sleep the exact same amount and slam the API again together. Add **±25% uniform jitter** so the herd disperses across the retry window.

**Scope**: jitter applies to all `RetryHandler` callers (LLM + Image). The reviewer flagged that LLM callers are currently serial so jitter is dead-weight for them — true today, but the per-call latency impact at ±25% on `initial_delay=1s` is ±0.25s per retry, negligible. Cross-cutting change is simpler than adding a `jitter: bool` flag to `RetryConfig`; accept the tiny cost.

---

## 4. 実装ステップ

順序は依存関係順。各ステップ後に `pytest` 通る状態を保つ。

| # | 範囲 | 変更内容 |
|---|------|---------|
| 1 | config | `ConcurrencyConfig` 追加 (`image_workers: int = 16`), `MangakaConfig.concurrency` 生やす |
| 2 | retry | `calculate_delay` に ±25% jitter (`random.uniform`) |
| 3 | assets | `save_bytes_strict` (atomic O_CREAT\|O_EXCL) を追加、既存 `save_bytes` を `save_bytes_versioned` に rename。`--inject-*` CLI 経路のみ `_versioned` を使う |
| 4 | tests | `tests/test_assets.py` — strict 版が既存 file で `FILE_EXISTS` Failure 返すこと、並列 thread で同一 path 書き込み合戦すると片方だけ成功すること |
| 5 | new module | `src/mangaka/image/parallel.py` 実装 (`ImageJob` w/ `tag`, `ImageJobOutcome`, `run_image_jobs` w/ §3.6 drain protocol) |
| 6 | tests | `tests/test_image_parallel.py` — fake ImageClient で完了順非依存、drain protocol (in-flight が失敗後も on_complete 通る)、fail-fast determinism、tag thread-through |
| 7 | domain/state | §3.8 LLM-output caching: `MangaState.character_layer / location_layer` 追加 or 既存フィールドへの raw_markdown 注入、persistence 更新 |
| 8 | page_render 改修 | 既存ループを §3.3 パターンに置き換え、`save_bytes_strict` 使用 |
| 9 | character 改修 | §3.4 + §3.8 適用、per-char checkpoint、LLM 出力キャッシュ |
| 10 | location 改修 | 同上 |
| 11 | tests | layer 統合テスト: 並列実行・部分失敗 → 残った成功の checkpoint 永続化 → resume で残りだけ走る、ことを全 3 layer で |
| 12 | docs | ARCHITECTURE.md「並列実行モデル」セクション追加、asset immutability の strict/versioned 区別を明記 |
| 13 | cross-review | このプラン + 実装 diff に対して |

実装は **commit を 5 つに分ける**:
- (a) infra-1: config + retry jitter + save_bytes_strict + そのテスト
- (b) infra-2: parallel.py + ImageJob + run_image_jobs + drain protocol テスト
- (c) state schema: LLM-output caching を MangaState に入れる + 既存 layer の挙動を保ったまま raw_markdown だけ persist
- (d) page_render 並列化
- (e) character + location 並列化 + ARCHITECTURE 更新

各 commit は独立してビルド可能 + テスト通過する。(c) と (d) の間で挙動が変わらないことを確認してから (d) に進む。

---

## 5. テスト戦略

### Unit (`pytest -m ""` 既定)

`tests/test_assets.py` (新規 or 拡張):
- `save_bytes_strict` が存在 file に書こうとすると `IO_ERROR/FILE_EXISTS` で Failure
- 16 thread から同一 path に同時書き込み → 1 つだけ Success、残りは全部 FILE_EXISTS Failure (atomic 性検証)
- `save_bytes_versioned` (旧 save_bytes) は従来通り `_vNNN` で逃げる

`tests/test_image_parallel.py` (新規):
- `FakeImageClient` を追加: `edit(prompt, refs, ...)` → 即 Success(deterministic bytes derived from prompt hash). Latency 注入 (`time.sleep(random)`) 可能オプション
- 順序非依存性: 5 jobs、入力 prompt をシャッフルしても全 outcome 揃う + state は parsed 順で並ぶ
- Drain protocol: 5 jobs、3 番目に同期的に Failure 注入 + 残り 2 つに sleep 注入 → 失敗観測後も sleep 中 2 つの on_complete が走り state に反映される、最後に first_error が返る
- Fail-fast determinism: 同じ seed で fixed Failure を注入 → first_error が常に同じ
- tag thread-through: `ImageJob.tag` に dict を入れたら `on_complete` で同じオブジェクトが取れる
- on_complete Failure 伝播: on_complete が Failure を返したケースでも drain は止まらず、最終的に first_error が返る

`tests/test_layers_page_render.py` の更新:
- 既存テスト: N=1 (image_workers=1) で挙動保たれる
- 新規: image_workers=4, 8 ページ、全成功 → state.pages 全部埋まる
- 新規: image_workers=4, 5 ページ目だけエラー → drain 後の state は 5 以外全部埋まっている (4 successful) かつ Failure 返る
- 新規: resume — 上の続きで再実行 → 残った 1 ページだけ処理、`save_bytes_strict` が他の page_NNN.png で FILE_EXISTS にならない (state ガード effective)
- 新規: state-vs-disk drift — 手で state を消して再実行 → `save_bytes_strict` が FILE_EXISTS で loud-fail

`tests/test_layers_character.py` / `test_layers_location.py`:
- LLM 出力キャッシュ: 1 度目で raw_markdown が persist、2 度目入った時 LLM 呼ばれない (FakeLLMClient の call_count で検証)
- 部分失敗 → resume: 5 chars 中 3 つ目で失敗 → state.character_layer.characters は 2 つ含む、再 entry で 4-5 番目だけ処理される

### Integration (`RUN_INTEGRATION=1 pytest -m integration`)

実 API は呼ばない (Tier 5 でも \$5+ のコスト)。並列化は unit で十分検証可能なので integration は smoke のみ:
- `tests/test_smoke.py` に `image_workers=2` で fake client パイプラインが完走することを足す

### Cost guard test

`pytest -m smoke` で `image_workers=1` 強制したい (実 API キーで CI が走ったときの fail-safe)。`.claude/hooks/stop-smoke-test.sh` でセットする環境変数で上書きする方が綺麗かも — 実装時に検討。

---

## 6. 観測性

並列化で wall-clock が短くなる代わりに「どの job がどれだけ詰まったか」が読みにくくなる。`logger.info("page_render_completed", ...)` に **submit/complete timestamps** を入れる:

```python
logger.info(
    "image_job_completed",
    job_id=outcome.job.id,
    duration_sec=...,
    queued_workers=...,  # alive worker count at completion
)
```

`structlog` (既存) が timestamp 自動付与するので追加コストはない。

---

## 7. 既知のリスクと緩和

| リスク | 緩和 |
|--------|-----|
| `save_bytes` の TOCTOU (resume 後の `_v002` 衝突含む) | §3.7 `save_bytes_strict` (atomic O_CREAT\|O_EXCL)。バッチ内 job は distinct path by construction、orphan 衝突は loud-fail で表面化 |
| fail-fast で in-flight worker の PNG が孤児化 | §3.6 drain protocol で in-flight も `on_complete` 経由で state commit してから error 返す |
| Character/Location resume で LLM 再呼びによる id ドリフト | §3.8 raw_markdown を state に persist、resume 時は cached markdown を再パース。LLM call は layer lifetime に 1 回 |
| Thread leak (worker が hang した時) | OpenAI SDK の httpx に default timeout がある (再確認)。RetryHandler max_retries 有限。Executor は `with` ブロックで強制 shutdown |
| KeyboardInterrupt 時の中断 | `ThreadPoolExecutor(...)` の context manager + `cancel_futures=True`。実行中 future は API call 完了まで待つが、drain protocol で state 整合性は保たれる |
| 16 worker × 16 refs × 数 MB の同時 upload で uplink 飽和 | 実 latency 増 → 実効 IPM 下がる → Tier 5 cap への余裕が増える方向。ヘッドルーム 5x は確保できる |
| httpx connection pool 飽和 | デフォルト 100 (>>16)。問題なし。1 行で risks 表に明記済み |
| structlog contextvars が worker thread に伝播しない | 現状 contextvars 未使用。導入時には `copy_context()` on submit が必要 (将来の落とし穴メモ) |
| structlog 基本の thread-safety | thread-safe (公式 docs)。問題なし |
| pyright `Future[T]` の型推論 | `as_completed` の戻り型注釈で対応 |
| 全 worker が同時 429 → API 凍結 | retry jitter (実装ステップ #2) で分散 |
| `on_complete` が遅い (state checkpoint が I/O 重い) | state JSON は 16 ページ run で 30-80 KB。SSD 上でミリ秒。問題にならない |

---

## 8. 受け入れ条件

- [ ] `pytest` (165+ 新規分) 全通過
- [ ] `pyright src/ tests/` clean (strict)
- [ ] `ruff check` clean
- [ ] PoC 1 本 (16 ページ) で wall-clock 比較: serial vs parallel(16) で **5x 以上**短縮を確認
- [ ] PoC 1 本で部分失敗 → resume が動くことを手動確認 (`poc_continue.py`)
- [ ] state-vs-disk drift シナリオ手動検証: `state_*.json` を削って再実行 → `save_bytes_strict` で loud-fail することを確認
- [ ] Character layer 再実行で LLM call が 1 回しか発生しないこと検証 (FakeLLMClient or 実 log)
- [ ] ARCHITECTURE.md 並列モデルセクション追加 + asset immutability の strict/versioned 区別を明記
- [ ] cross-review pass

---

## 9. 将来展望: レイヤー単位の並列 (deferred #3)

### 候補

Character レイヤーと Location レイヤーは **両方とも `mpbv + stylist` だけを消費し、disjoint な state slice (`state.characters` vs `state.locations`) に書き込む**。CS 的には commute する。

直列だと:
```
Stylist → Character (LLM + 3-5 image calls) → Location (LLM + 2-4 image calls) → PagePlan
```

Fork-join 化すると:
```
Stylist → [Character ∥ Location] → PagePlan
```

期待短縮: Character ~60s + Location ~40s → max(60, 40) = 60s。**~40% の wall-clock 短縮**。

### なぜ今やらないか

1. **本プラン (#1, #2) で character/location 内部のシート並列化は既に入る**。レイヤー単位で更に並列化しても、内部並列化済みの両層を more in parallel にするだけ。例えば character=5 sheets + location=3 sheets を **同時に** 8 worker 投げる方向のメリットは限定的 (どうせ 16 worker pool は飽きてる)
2. **State の合成が non-trivial**。両層は full `MangaState` を返すので、merge は "character のは state.characters を、location のは state.locations を採用、それ以外は同じであるべき" という invariant check が必要。StateDelta 抽象を導入するか、merge ヘルパに整合性 assert を入れるかになる。架構変更コスト > 効果
3. **#1+#2 後の実測がほしい**。レイヤー全体 wall-clock を測ってから「最遅レイヤーがどれか」を見て、本当に C/L 並列が効くかを判断する

### 着手条件 (= こうなったら #3 やる)

- PageRender 並列化後、Character + Location の **直列実行が wall-clock の 30% 以上**を占めるようになった
- StateDelta 抽象 (各レイヤーが diff を返す) を導入する別の動機 (例: dry-run, incremental re-run) ができた
- ↑ がどちらも当てはまる場合、`pipeline.py` に `_fork_join` を実装する。設計スケッチ:

```python
# Sketch only — not implementing in this plan.
def _fork_join(
    state: MangaState,
    layers: list[_LayerSpec],
    ...
) -> Result[MangaState, MangaError]:
    """Run layers concurrently. All layers must declare a disjoint write set
    (StateDelta). Merge deltas serially; conflict on overlapping fields is
    a programmer error (raises)."""
    with ThreadPoolExecutor(len(layers)) as ex:
        futures = {ex.submit(_run_layer, state, spec): spec for spec in layers}
        deltas = []
        for fut in as_completed(futures):
            outcome = fut.result()  # Result[StateDelta, MangaError]
            if isinstance(outcome, Failure):
                # cancel siblings, return
                ...
            deltas.append(outcome.unwrap())
    return Success(state.apply_all(deltas))
```

`StateDelta` 抽象を入れるかどうかが鍵。入れない場合は full-state を merge する関数を書くが、フィールド追加するたびに merge も追従する必要があり brittle。

### 雑記

LLM 呼び出し並列化 (Plot → Backstory → MPBV パイプの先頭部分) は **明確に直列依存**なので並列化不可。これは並列化の対象外。

---

## 10. 補遺: なぜ asyncio ではなく Thread Pool か

| 観点 | Thread Pool | asyncio |
|------|-------------|---------|
| OpenAI SDK | 既存の同期 `OpenAI()` がそのまま使える | `AsyncOpenAI()` への全面切替が必要 |
| 既存コード変更量 | layers 3 箇所 + 新 helper のみ | LLM/Image client 両方を async 化 → 全レイヤー async/await 化 |
| 並列度 | 16 worker = 16 thread。GIL は I/O 待ち中 release されるので問題なし | 1 event loop で 16 coroutine。同じ |
| GIL 競合 | bytes 受け取りと b64 decode は数 ms。GIL 取り合いは無視できる | n/a |
| デバッグ | Python debugger が thread を素直に扱える | asyncio スタックは読みにくい |
| Result discipline | 同期そのまま | `async def` シグネチャに侵略される (Result も `await` 越し) |
| 将来 streaming etc. | 必要時に asyncio 化 | 既に async |

→ Thread Pool 一択。
