from pathlib import Path

import pytest

from pdf_image_extractor.core.models import ExtractionConfig


def _base_kwargs():
    return {
        "input_paths": [Path("in.pdf")],
        "output_dir": Path("out"),
    }


def test_extraction_config_valid_defaults() -> None:
    cfg = ExtractionConfig(**_base_kwargs())
    assert cfg.engine == "auto"
    assert cfg.max_workers == 4


def test_extraction_config_rejects_invalid_engine() -> None:
    with pytest.raises(ValueError, match="engine"):
        ExtractionConfig(**_base_kwargs(), engine="invalid")  # type: ignore[arg-type]


def test_extraction_config_rejects_invalid_report_format() -> None:
    with pytest.raises(ValueError, match="report_formats"):
        ExtractionConfig(**_base_kwargs(), report_formats={"json", "xml"})  # type: ignore[arg-type]


def test_extraction_config_rejects_non_positive_max_workers() -> None:
    with pytest.raises(ValueError, match="max_workers"):
        ExtractionConfig(**_base_kwargs(), max_workers=0)


def test_extraction_config_rejects_negative_timeout() -> None:
    with pytest.raises(ValueError, match="pdf_timeout_seconds"):
        ExtractionConfig(**_base_kwargs(), pdf_timeout_seconds=-1)


def test_extraction_config_rejects_mutually_exclusive_flags() -> None:
    with pytest.raises(ValueError, match="mutuamente exclusivos"):
        ExtractionConfig(**_base_kwargs(), fail_fast=True, continue_on_error=True)


@pytest.mark.parametrize(
    "field_name",
    [
        "worker_memory_limit_mb",
        "worker_cpu_time_limit_seconds",
        "max_pdf_size_mb",
        "max_pages_per_pdf",
        "max_images_per_pdf",
        "max_output_bytes_per_pdf_mb",
    ],
)
def test_extraction_config_rejects_non_positive_optional_limits(field_name: str) -> None:
    kwargs = _base_kwargs()
    kwargs[field_name] = 0
    with pytest.raises(ValueError, match=field_name):
        ExtractionConfig(**kwargs)
