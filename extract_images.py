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
        isolate_pdf_processing=False,
    )
    from pdf_image_extractor.core.pipeline import resolve_engine
    engine_obj = resolve_engine(engine)
    return _extract(pdf_path, cfg, engine_obj)


def run_extraction_job(*, input_paths: list[Path], output_dir: Path, prefix: str = "imagem", recursive: bool = False, fail_fast: bool = False, continue_on_error: bool = True, only_format: set[str] | None = None, report: Path = Path("relatorio_extracao"), report_formats: set[str] = frozenset({"json", "csv"}), engine: str = "auto", quiet: bool = False, max_workers: int = 4, isolate_pdf_processing: bool = False, pdf_timeout_seconds: int = 60, worker_memory_limit_mb: int | None = 1024, worker_cpu_time_limit_seconds: int | None = 120, max_pdf_size_mb: int | None = 200, max_pages_per_pdf: int | None = 500, max_images_per_pdf: int | None = 2000, max_output_bytes_per_pdf_mb: int | None = 256, telemetry_log_path: Path | None = None, metrics_output_path: Path | None = None):
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
        isolate_pdf_processing=isolate_pdf_processing,
        pdf_timeout_seconds=pdf_timeout_seconds,
        worker_memory_limit_mb=worker_memory_limit_mb,
        worker_cpu_time_limit_seconds=worker_cpu_time_limit_seconds,
        max_pdf_size_mb=max_pdf_size_mb,
        max_pages_per_pdf=max_pages_per_pdf,
        max_images_per_pdf=max_images_per_pdf,
        max_output_bytes_per_pdf_mb=max_output_bytes_per_pdf_mb,
        telemetry_log_path=telemetry_log_path,
        metrics_output_path=metrics_output_path,
    )
    return _run_extraction_job(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
