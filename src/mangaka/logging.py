"""Structured logging + rich console helpers (ported from mlsg2)."""

from __future__ import annotations

import logging

import structlog
from rich.console import Console

console: Console = Console()
console_err: Console = Console(stderr=True)


def setup_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    """Configure structlog with sensible defaults.

    Default level INFO; `--verbose` enables DEBUG, `--quiet` suppresses to WARNING.
    """
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def print_success(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    console_err.print(f"[red]✗[/red] {message}")


def print_warning(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def print_info(message: str) -> None:
    console.print(f"[blue]i[/blue] {message}")


__all__ = [
    "console",
    "console_err",
    "get_logger",
    "print_error",
    "print_info",
    "print_success",
    "print_warning",
    "setup_logging",
]
