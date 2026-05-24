"""Generic subsection extractor for layer Markdown.

`extract_subsection(md, "外見")` returns the body of the `### 外見 ...`
section (anything between that header and the next `### ` or top-level `## `
header). Returns the empty string if the section isn't found.
"""

from __future__ import annotations

import re


def extract_subsection(md: str, name: str) -> str:
    """Return the content of `### {name} ...` (case-insensitive, prefix-match).

    Stops at the next `### ` or `## ` header. Trailing whitespace is trimmed.
    """
    # Tolerant header match: `### 外見`, `### 外見 (Visual)`, etc.
    pattern = re.compile(
        rf"^###\s*\*{{0,2}}\s*{re.escape(name)}\b[^\n]*\n(?P<body>.*?)(?=^##|^###|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md)
    if m is None:
        return ""
    return m.group("body").rstrip()


__all__ = ["extract_subsection"]
