from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ParsedImage:
    page: int | None
    index: int
    raw: bytes
    decoded: bytes
    filters: list[str]
    meta: dict[str, Any]
    preferred_ext: str | None


class ExtractorEngine(Protocol):
    name: str

    def extract(self, pdf_path: Path) -> list[ParsedImage]:
        ...
