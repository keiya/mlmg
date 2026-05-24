"""Image generation client layer (gpt-image-2 + Fake) + asset helpers."""

from mangaka.image.assets import (
    next_available_path,
    save_bytes,
    save_bytes_strict,
    save_bytes_versioned,
)
from mangaka.image.client import ImageClient
from mangaka.image.client_fake import FakeImageClient
from mangaka.image.parallel import ImageJob, ImageJobOutcome, run_image_jobs
from mangaka.image.prompts import build_page_prompt, extract_visual_summary
from mangaka.image.ref_builder import LabeledRef, build_refs
from mangaka.image.sections import SECTION_SETS, extract_sections

__all__ = [
    "SECTION_SETS",
    "FakeImageClient",
    "ImageClient",
    "ImageJob",
    "ImageJobOutcome",
    "LabeledRef",
    "build_page_prompt",
    "build_refs",
    "extract_sections",
    "extract_visual_summary",
    "next_available_path",
    "run_image_jobs",
    "save_bytes",
    "save_bytes_strict",
    "save_bytes_versioned",
]
