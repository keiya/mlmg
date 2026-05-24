"""Pipeline layers.

Each layer is `(state, llm, [img,] config, ...) -> Result[MangaState, MangaError]`
and is pure aside from `llm` / `img` / file-system writes via `persistence.py`.
"""
