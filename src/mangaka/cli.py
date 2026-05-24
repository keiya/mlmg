"""CLI entrypoint.

M1 scope: `mangaka --version`, `mangaka run "<seed>" --until {plot,backstory,mpbv}`.
Image-layer commands, `--inject-*`, `status`, `export` land in M2-M5.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from mangaka import __version__
from mangaka.config import MangakaConfig, RetryConfig, load_config
from mangaka.domain import MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.export.pdf import export_pdf
from mangaka.image.client_openai import OpenAIImageClient
from mangaka.llm.client_openai import OpenAILLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger, print_error, print_info, print_success, setup_logging
from mangaka.persistence import latest_state_path, load_state, save_state, state_path_for
from mangaka.pipeline import Until, run_pipeline
from mangaka.result import Failure, Result

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mangaka",
        description="Short manga generator with multi-layered prompts + gpt-image-2.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress INFO logging"
    )

    sub = parser.add_subparsers(dest="command", required=False)

    run = sub.add_parser("run", help="Run the generation pipeline for a seed")
    run.add_argument(
        "seed",
        nargs="?",
        default=None,
        help="Story seed text (use -f to read from a file)",
    )
    run.add_argument(
        "-f", "--seed-file", type=Path, default=None, help="Read seed from a file"
    )
    run.add_argument(
        "--until",
        choices=[u.value for u in Until],
        default=Until.MPBV.value,
        help="Stop after this layer (default: mpbv)",
    )
    run.add_argument(
        "--name", default=None, help="Run name (default: derived from seed)"
    )
    run.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config.toml (default: ./config.toml)",
    )
    run.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow writing into a non-empty run directory. Without this flag, "
            "an existing run is refused to prevent silently corrupting prior state "
            "or paying twice for already-rendered pages."
        ),
    )

    export = sub.add_parser("export", help="Export a finished run to PDF")
    export.add_argument(
        "run_dir",
        type=Path,
        help="Path to the run directory (e.g. runs/my_manga/)",
    )
    export.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output PDF path (default: <run_dir>/manga.pdf)",
    )
    export.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to config.toml. Default: use the run's own config.toml "
            "snapshot (recommended — keeps export consistent with the settings "
            "the run was generated with), or `./config.toml` if no snapshot exists."
        ),
    )

    return parser


def _slugify(text: str) -> str:
    """Derive a filesystem-friendly run name from arbitrary seed text."""
    slug = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return (slug[:40] or "run").lower()


def _validate_run_name(name: str) -> Result[str, MangaError]:
    """Reject `--name` values that would escape the runs directory.

    `runs_dir / run_name` should always resolve to a direct child of `runs_dir`.
    Path separators, `..`, absolute paths, and leading dots all break that
    invariant and could clobber arbitrary disk paths.
    """
    if not name:
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message="--name must not be empty",
            )
        )
    if "/" in name or "\\" in name:
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message=f"--name must not contain path separators: {name!r}",
            )
        )
    if name in (".", "..") or name.startswith("."):
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message=f"--name must not be a relative reference or hidden: {name!r}",
            )
        )
    if Path(name).is_absolute():
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message=f"--name must not be an absolute path: {name!r}",
            )
        )
    return _success(name)


def _load_seed(args: argparse.Namespace) -> Result[str, MangaError]:
    if args.seed_file is not None:
        path: Path = args.seed_file
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.IO_ERROR,
                    message=f"failed to read seed file: {exc}",
                    detail={"path": str(path)},
                )
            )
        except UnicodeDecodeError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.CONFIG_ERROR,
                    message=f"seed file is not valid UTF-8: {exc}",
                    detail={"path": str(path)},
                )
            )
        stripped = raw.strip()
        if not stripped:
            return Failure(
                MangaError(
                    kind=ErrorKind.CONFIG_ERROR,
                    message=f"seed file is empty: {path}",
                    detail={"path": str(path)},
                )
            )
        return _success(stripped)

    seed = args.seed
    stripped_seed = seed.strip() if seed else ""
    if not stripped_seed:
        return Failure(
            MangaError(
                kind=ErrorKind.CONFIG_ERROR,
                message=(
                    "no seed provided — pass a non-empty positional seed or --seed-file"
                ),
            )
        )
    return _success(stripped_seed)


def _success[T](value: T) -> Result[T, MangaError]:
    """Tiny shim so this module doesn't have to import Success directly."""
    from mangaka.result import Success

    return Success(value)


def _run_subcommand(args: argparse.Namespace) -> int:
    config_result = load_config(args.config)
    if isinstance(config_result, Failure):
        print_error(config_result.failure().message)
        return 1
    config: MangakaConfig = config_result.unwrap()

    seed_result = _load_seed(args)
    if isinstance(seed_result, Failure):
        print_error(seed_result.failure().message)
        return 2
    seed = seed_result.unwrap()

    if args.name is not None:
        name_result = _validate_run_name(args.name)
        if isinstance(name_result, Failure):
            print_error(name_result.failure().message)
            return 2
        run_name = name_result.unwrap()
    else:
        run_name = _slugify(seed)
    runs_dir = Path(config.general.runs_dir) / run_name
    print_info(f"run_name={run_name} runs_dir={runs_dir}")

    # Refuse to mix a fresh run into an existing run directory unless --force.
    # Otherwise: a successful text-layer rewrite destroys the only persisted
    # copy of the prior run's Plot/Backstory/MPBV, and `latest_state_path`
    # picks up old higher-numbered state files that don't match this seed.
    existing_state_files = list(runs_dir.glob("state_*.json")) if runs_dir.is_dir() else []
    if existing_state_files and not args.force:
        print_error(
            f"run directory already contains state files: {runs_dir}. "
            f"Pick a different --name, delete the directory, or pass --force."
        )
        return 1
    # On --force, clear EVERY old state_*.json so later layers cannot accept
    # a stale higher-numbered snapshot as the "latest" for this run. The
    # canonical artifacts (assets/, pages/) stay on disk — those
    # are immutable per ARCH and will be re-versioned when overwritten.
    for stale in existing_state_files:
        try:
            stale.unlink()
        except OSError as exc:
            print_error(f"failed to clear stale state file {stale}: {exc}")
            return 1

    initial_state = MangaState(seed_input=seed, run_name=run_name)
    init_path = state_path_for(runs_dir, "init")
    init_save = save_state(initial_state, init_path)
    if isinstance(init_save, Failure):
        print_error(init_save.failure().message)
        return 1

    # Persist a config snapshot inside the run so `mangaka export` can use
    # the exact settings the run was generated with — even if the user is
    # in a different cwd or has since edited the project's config.toml.
    try:
        snapshot_path = runs_dir / "config.toml"
        snapshot_path.write_bytes(args.config.read_bytes())
    except OSError as exc:
        print_error(f"failed to snapshot config into run dir: {exc}")
        return 1

    # Backoff shape (initial_delay / max_delay / exponential_base) comes
    # from `[retry]`; per-domain retry counts come from `[limits]`. Otherwise
    # the documented `limits.max_retries` / `limits.max_image_retries` knobs
    # are silently no-ops and tuning them has no effect on real runs.
    retry_cfg = RetryConfig(**config.retry.model_dump())
    llm_retry_cfg = retry_cfg.model_copy(
        update={"max_retries": config.limits.max_retries}
    )
    image_retry_cfg = retry_cfg.model_copy(
        update={"max_retries": config.limits.max_image_retries}
    )
    llm = OpenAILLMClient(
        default_model=config.models.default,
        retry_config=llm_retry_cfg,
    )
    img = OpenAIImageClient(retry_config=image_retry_cfg)
    prompt_loader = PromptLoader()

    result = run_pipeline(
        initial_state,
        llm,
        config,
        prompt_loader,
        until=Until(args.until),
        run_dir=runs_dir,
        img=img,
    )
    if isinstance(result, Failure):
        err = result.failure()
        print_error(f"[{err.kind.name}] {err.message}")
        return 1

    print_success(f"completed through {args.until}")
    return 0


def _export_subcommand(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        print_error(f"run_dir not found or not a directory: {run_dir}")
        return 1

    # Resolution order:
    #   1. explicit `--config <path>` (user knows best)
    #   2. the run's own snapshot at `runs/{name}/config.toml`
    #   3. `./config.toml` as a last-ditch fallback for legacy runs
    # `argparse` default is `None` so we can detect explicit user intent —
    # using a Path equality check against `Path("config.toml")` would treat
    # `--config config.toml` as the default, masking the override.
    snapshot = run_dir / "config.toml"
    if args.config is not None:
        config_path: Path = args.config
    elif snapshot.exists():
        config_path = snapshot
    else:
        config_path = Path("config.toml")

    config_result = load_config(config_path)
    if isinstance(config_result, Failure):
        print_error(config_result.failure().message)
        return 1
    config: MangakaConfig = config_result.unwrap()

    state_path_result = latest_state_path(run_dir)
    if isinstance(state_path_result, Failure):
        print_error(state_path_result.failure().message)
        return 1

    state_result = load_state(state_path_result.unwrap())
    if isinstance(state_result, Failure):
        print_error(state_result.failure().message)
        return 1
    state = state_result.unwrap()

    output_path: Path = args.output or (run_dir / "manga.pdf")
    pdf_result = export_pdf(state, output_path, config)
    if isinstance(pdf_result, Failure):
        err = pdf_result.failure()
        print_error(f"[{err.kind.name}] {err.message}")
        return 1

    print_success(f"PDF written to {pdf_result.unwrap()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose, quiet=args.quiet)

    if args.command == "run":
        return _run_subcommand(args)
    if args.command == "export":
        return _export_subcommand(args)

    # No subcommand → show help (M0 behavior preserved).
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["build_parser", "main"]
