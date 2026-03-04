from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args


EngineName = Literal["auto", "pypdf", "fallback"]
ReportFormat = Literal["json", "csv"]


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
    continue_on_error: bool = False
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

    def __post_init__(self) -> None:
        if self.fail_fast and self.continue_on_error:
            raise ValueError("Configuração inválida: 'fail_fast' e 'continue_on_error' são mutuamente exclusivos.")

        if not isinstance(self.input_paths, list):
            raise ValueError("Configuração inválida: 'input_paths' deve ser uma lista de caminhos.")

        if self.max_workers <= 0:
            raise ValueError("Configuração inválida: 'max_workers' deve ser maior que zero.")

        if self.pdf_timeout_seconds < 0:
            raise ValueError("Configuração inválida: 'pdf_timeout_seconds' deve ser >= 0.")

        valid_engines = set(get_args(EngineName))
        if self.engine not in valid_engines:
            raise ValueError(f"Configuração inválida: 'engine' deve ser um de {sorted(valid_engines)}.")

        valid_report_formats = set(get_args(ReportFormat))
        if not set(self.report_formats).issubset(valid_report_formats):
            raise ValueError(f"Configuração inválida: 'report_formats' deve conter apenas {sorted(valid_report_formats)}.")

        for name in (
            "worker_memory_limit_mb",
            "worker_cpu_time_limit_seconds",
            "max_pdf_size_mb",
            "max_pages_per_pdf",
            "max_images_per_pdf",
            "max_output_bytes_per_pdf_mb",
        ):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"Configuração inválida: '{name}' deve ser > 0 quando definido.")
