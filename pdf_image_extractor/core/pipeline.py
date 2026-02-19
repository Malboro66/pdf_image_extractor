from __future__ import annotations

import csv
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from pdf_image_extractor.adapters.engines.base import ExtractorEngine
from pdf_image_extractor.adapters.engines.fallback import FallbackEngine
from pdf_image_extractor.adapters.engines.pypdf_engine import PyPdfEngine
from pdf_image_extractor.core.models import ExtractionConfig, ExtractionRecord
from pdf_image_extractor.core.reconstruct import choose_output


def collect_pdfs(path: Path, recursive: bool) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        pattern = "**/*.pdf" if recursive else "*.pdf"
        return sorted(path.glob(pattern))
    return []


def collect_pdfs_from_inputs(paths: list[Path], recursive: bool) -> list[Path]:
    all_pdfs: list[Path] = []
    for p in paths:
        all_pdfs.extend(collect_pdfs(p, recursive))
    return sorted({p.resolve() for p in all_pdfs})


def resolve_engine(name: str) -> ExtractorEngine:
    if name == "fallback":
        return FallbackEngine()
    if name == "pypdf":
        return PyPdfEngine()
    try:
        import pypdf  # noqa: F401
        return PyPdfEngine()
    except Exception:
        return FallbackEngine()


def write_report(records: list[ExtractionRecord], report_base: Path, formats: set[str]) -> None:
    if "json" in formats:
        report_base.with_suffix(".json").write_text(json.dumps([asdict(r) for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
    if "csv" in formats:
        fields = list(ExtractionRecord.__dataclass_fields__.keys())
        with report_base.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in records:
                w.writerow(asdict(r))


def _build_output_name(config: ExtractionConfig, pdf_path: Path, page: int | None, index: int, ext: str, raw: bytes) -> str:
    fingerprint = hashlib.sha1(f"{pdf_path}|{page}|{index}".encode("utf-8") + raw[:2048]).hexdigest()[:10]
    return f"{config.prefix}_{index:04d}_{fingerprint}.{ext}"


def extract_from_pdf(pdf_path: Path, config: ExtractionConfig, engine: ExtractorEngine) -> tuple[list[ExtractionRecord], int]:
    records: list[ExtractionRecord] = []
    errors = 0
    try:
        images = engine.extract(pdf_path)
    except Exception as exc:
        return [ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", str(exc), engine.name, 0, "none")], 1

    config.output_dir.mkdir(parents=True, exist_ok=True)

    for item in images:
        started = time.perf_counter()
        try:
            decoded, ext, correction_status = (item.decoded, item.preferred_ext, "none") if item.preferred_ext else choose_output(item.decoded, item.filters, item.meta)
            if config.only_format and ext.lower() not in config.only_format:
                status, out_file, out_size = "skipped_format", None, 0
            else:
                name = _build_output_name(config, pdf_path, item.page, item.index, ext, item.raw)
                target = config.output_dir / name
                target.write_bytes(decoded)
                status, out_file, out_size = "ok", str(target), len(decoded)
            duration_ms = int((time.perf_counter() - started) * 1000)
            records.append(ExtractionRecord(config.schema_version, str(pdf_path), item.page, item.index, out_file, "|".join(item.filters), item.meta["Width"], item.meta["Height"], item.meta["BitsPerComponent"], item.meta["ColorSpace"], len(item.raw), out_size, status, None, engine.name, duration_ms, correction_status))
        except Exception as exc:
            errors += 1
            duration_ms = int((time.perf_counter() - started) * 1000)
            records.append(ExtractionRecord(config.schema_version, str(pdf_path), item.page, item.index, None, "|".join(item.filters), item.meta["Width"], item.meta["Height"], item.meta["BitsPerComponent"], item.meta["ColorSpace"], len(item.raw), 0, "error", str(exc), engine.name, duration_ms, "none"))
            if not config.continue_on_error:
                break
    return records, errors


def run_extraction_job(config: ExtractionConfig) -> tuple[list[ExtractionRecord], int]:
    pdfs = collect_pdfs_from_inputs(config.input_paths, config.recursive)
    if not pdfs:
        return [], 2

    engine = resolve_engine(config.engine)
    all_records: list[ExtractionRecord] = []
    total_errors = 0

    max_workers = max(1, min(config.max_workers, len(pdfs)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(extract_from_pdf, pdf, config, engine): pdf for pdf in pdfs}
        for i, fut in enumerate(as_completed(futures), start=1):
            pdf = futures[fut]
            if not config.quiet:
                print(f"[{i}/{len(pdfs)}] Processado: {pdf}")
            records, errors = fut.result()
            all_records.extend(records)
            total_errors += errors
            if errors and config.fail_fast:
                break

    write_report(all_records, config.report, config.report_formats)
    return all_records, (0 if total_errors == 0 else 1)
