#!/usr/bin/env python3
"""Entrypoint CLI e compatibilidade para o extrator de imagens."""

from __future__ import annotations

from pathlib import Path

from pdf_image_extractor.adapters.engines.fallback import FallbackEngine
from pdf_image_extractor.core.models import ExtractionConfig
from pdf_image_extractor.core.pipeline import run_extraction_job as _run_extraction_job
from pdf_image_extractor.core.reconstruct import apply_decode_transform as _apply_decode_transform
from pdf_image_extractor.core.reconstruct import raw_to_png as _raw_to_png
from pdf_image_extractor.interfaces.cli import main


# Compatibilidade com testes antigos

def extract_from_pdf(
    pdf_path: Path,
    output_dir: Path,
    prefix: str,
    only_format: set[str] | None,
    engine: str,
    continue_on_error: bool,
):
    from pdf_image_extractor.core.pipeline import extract_from_pdf as _extract

    cfg = ExtractionConfig(
        input_paths=[pdf_path],
        output_dir=output_dir,
        prefix=prefix,
        only_format=only_format,
        engine=engine,
        continue_on_error=continue_on_error,
    )
    from pdf_image_extractor.core.pipeline import resolve_engine
    engine_obj = resolve_engine(engine)
    return _extract(pdf_path, cfg, engine_obj)


def run_extraction_job(*, input_paths: list[Path], output_dir: Path, prefix: str = "imagem", recursive: bool = False, fail_fast: bool = False, continue_on_error: bool = True, only_format: set[str] | None = None, report: Path = Path("relatorio_extracao"), report_formats: set[str] = frozenset({"json", "csv"}), engine: str = "auto", quiet: bool = False, max_workers: int = 4):
    cfg = ExtractionConfig(
        input_paths=input_paths,
        output_dir=output_dir,
        prefix=prefix,
        recursive=recursive,
        fail_fast=fail_fast,
        continue_on_error=continue_on_error,
        only_format=only_format,
        report=report,
        report_formats=report_formats,
        engine=engine,
        quiet=quiet,
        max_workers=max_workers,
    )
    return _run_extraction_job(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
