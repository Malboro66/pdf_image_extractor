from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractionRecord:
    schema_version: str
    input_file: str
    page: int | None
    image_index: int
    output_file: str | None
    filters: str
    width: int | None
    height: int | None
    bits_per_component: int | None
    color_space: str | None
    source_bytes: int
    output_bytes: int
    status: str
    error: str | None
    engine_used: str
    duration_ms: int
    correction_status: str


@dataclass
class ExtractionConfig:
    input_paths: list[Path]
    output_dir: Path
    prefix: str = "imagem"
    recursive: bool = False
    fail_fast: bool = False
    continue_on_error: bool = True
    only_format: set[str] | None = None
    report: Path = Path("relatorio_extracao")
    report_formats: set[str] = field(default_factory=lambda: {"json", "csv"})
    engine: str = "auto"
    quiet: bool = False
    schema_version: str = "1.1"
    max_workers: int = 4
    isolate_pdf_processing: bool = True
    pdf_timeout_seconds: int = 60
    worker_memory_limit_mb: int | None = 1024
    worker_cpu_time_limit_seconds: int | None = 120
    max_pdf_size_mb: int | None = 200
    max_pages_per_pdf: int | None = 500
    max_images_per_pdf: int | None = 2000
    max_output_bytes_per_pdf_mb: int | None = 256
    telemetry_log_path: Path | None = None
    metrics_output_path: Path | None = None
