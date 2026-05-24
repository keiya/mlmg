"""LLM client layer.

Exposes the `LLMClient` Protocol plus concrete clients (OpenAI + Fake)
and a Jinja2-based prompt loader.
"""

from mangaka.llm.client import LLMClient
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.llm.retry import RetryHandler

__all__ = [
    "FakeLLMClient",
    "LLMClient",
    "PromptLoader",
    "RetryHandler",
]
