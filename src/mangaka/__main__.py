"""Module entrypoint: `python -m mangaka` proxies to the CLI."""

from __future__ import annotations

from mangaka.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
