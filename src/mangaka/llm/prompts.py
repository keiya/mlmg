"""Jinja2 prompt template loader.

Templates live in `prompts/` at the project root. The loader renders a
template by name and returns the rendered text as a `Result`, never raising
for expected failures.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateError, TemplateNotFound

from mangaka.errors import ErrorKind, MangaError
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


def _default_prompts_dir() -> Path:
    """`prompts/` next to `src/`."""
    return Path(__file__).resolve().parents[3] / "prompts"


class PromptLoader:
    """Loads and renders Jinja2 prompt templates."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self.prompts_dir = prompts_dir or _default_prompts_dir()
        self.env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, /, **variables: object) -> Result[str, MangaError]:
        """Render `template_name` with the given variables."""
        try:
            template = self.env.get_template(template_name)
            rendered = template.render(**variables)
        except TemplateNotFound:
            return Failure(
                MangaError(
                    kind=ErrorKind.CONFIG_ERROR,
                    message=f"Prompt template not found: {template_name}",
                    detail={"prompts_dir": str(self.prompts_dir)},
                )
            )
        except TemplateError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"Failed to render template {template_name}: {exc}",
                )
            )

        logger.debug(
            "prompt_rendered",
            template=template_name,
            variables=list(variables.keys()),
            length=len(rendered),
        )
        return Success(rendered)


__all__ = ["PromptLoader"]
