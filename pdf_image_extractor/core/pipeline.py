from __future__ import annotations

import csv
import hashlib
import json
import logging
import multiprocessing
import shutil
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from pdf_image_extractor.adapters.engines.base import ExtractorEngine
from pdf_image_extractor.adapters.engines.fallback import FallbackEngine
from pdf_image_extractor.adapters.engines.pypdf_engine import PyPdfEngine
from pdf_image_extractor.core.models import ExtractionConfig, ExtractionRecord
from pdf_image_extractor.core.reconstruct import choose_output

LOGGER_NAME = "pdf_image_extractor"


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "payload", {})
        row = {
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "job_id": getattr(record, "job_id", "-"),
            **payload,
        }
        return json.dumps(row, ensure_ascii=False)


def _get_structured_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_pdf_image_extractor_configured", False):
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = JsonFormatter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_MaxLevelFilter(logging.WARNING))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger._pdf_image_extractor_configured = True
    return logger


LOGGER = _get_structured_logger()



_MP_START_METHOD = "spawn"
_MP_CONTEXT_LOCK = threading.Lock()
_MP_CONTEXT: multiprocessing.context.BaseContext | None = None


def _get_multiprocessing_context() -> multiprocessing.context.BaseContext:
    global _MP_CONTEXT
    with _MP_CONTEXT_LOCK:
        if _MP_CONTEXT is None:
            _MP_CONTEXT = multiprocessing.get_context(_MP_START_METHOD)
        return _MP_CONTEXT


class ProgressEmitter(Protocol):
    def on_pdf_started(self, pdf: Path, index: int, total: int) -> None:
        ...

    def on_pdf_finished(self, pdf: Path, records: list[ExtractionRecord], errors: int, index: int, total: int) -> None:
        ...

    def on_error(self, pdf: Path | None, error: Exception | str) -> None:
        ...


class NullProgressEmitter:
    def on_pdf_started(self, pdf: Path, index: int, total: int) -> None:
        return

    def on_pdf_finished(self, pdf: Path, records: list[ExtractionRecord], errors: int, index: int, total: int) -> None:
        return

    def on_error(self, pdf: Path | None, error: Exception | str) -> None:
        return


class StdoutProgressEmitter(NullProgressEmitter):
    def __init__(self, job_id: str = "-") -> None:
        self.job_id = job_id

    def on_pdf_started(self, pdf: Path, index: int, total: int) -> None:
        LOGGER.info(
            "pdf_started",
            extra={
                "event": "pdf_started",
                "job_id": self.job_id,
                "payload": {"pdf_path": str(pdf), "index": index, "total": total},
            },
        )


class ReportWriter:
    def write(self, records: list[ExtractionRecord], report_base: Path, formats: set[str]) -> None:
        if "json" in formats:
            report_base.with_suffix(".json").write_text(json.dumps([asdict(r) for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
        if "csv" in formats:
            fields = list(ExtractionRecord.__dataclass_fields__.keys())
            with report_base.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for r in records:
                    w.writerow(asdict(r))


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
    ReportWriter().write(records, report_base, formats)


def _build_output_name(config: ExtractionConfig, pdf_path: Path, page: int | None, index: int, ext: str, raw: bytes) -> str:
    fingerprint = hashlib.sha1(f"{pdf_path}|{page}|{index}".encode("utf-8") + raw[:2048]).hexdigest()[:10]
    return f"{config.prefix}_{index:04d}_{fingerprint}.{ext}"


def _policy_record(config: ExtractionConfig, pdf_path: Path, reason: str, duration_ms: int = 0) -> ExtractionRecord:
    return ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "blocked_policy", reason, f"{config.engine}:policy", duration_ms, "none")


def _interrupted_record(config: ExtractionConfig, reason: str) -> ExtractionRecord:
    return ExtractionRecord(config.schema_version, "<job>", None, 0, None, "", None, None, None, None, 0, 0, "interrupted", reason, "orchestrator", 0, "none")


def _preflight_pdf(pdf_path: Path, config: ExtractionConfig) -> str | None:
    if not pdf_path.exists() or not pdf_path.is_file():
        return "Arquivo inexistente ou inválido."

    if config.max_pdf_size_mb:
        max_size = int(config.max_pdf_size_mb) * 1024 * 1024
        size = pdf_path.stat().st_size
        if size > max_size:
            return f"PDF bloqueado por política: tamanho {size} bytes acima do limite {max_size} bytes."

    try:
        header = pdf_path.open("rb").read(5)
    except Exception as exc:
        return f"Falha ao ler cabeçalho do PDF: {exc}"

    if not header.startswith(b"%PDF"):
        return "PDF bloqueado por política: assinatura inválida (esperado %PDF)."

    return None


def _set_resource_limits(config: ExtractionConfig) -> None:
    try:
        import resource
    except Exception:
        return

    if config.worker_memory_limit_mb:
        bytes_limit = int(config.worker_memory_limit_mb) * 1024 * 1024
        for key in ("RLIMIT_AS", "RLIMIT_DATA"):
            limit_type = getattr(resource, key, None)
            if limit_type is None:
                continue
            try:
                resource.setrlimit(limit_type, (bytes_limit, bytes_limit))
            except Exception:
                pass

    if config.worker_cpu_time_limit_seconds:
        cpu_limit = int(config.worker_cpu_time_limit_seconds)
        limit_type = getattr(resource, "RLIMIT_CPU", None)
        if limit_type is not None:
            try:
                resource.setrlimit(limit_type, (cpu_limit, cpu_limit))
            except Exception:
                pass


def _extract_impl(
    pdf_path: Path,
    config: ExtractionConfig,
    engine: ExtractorEngine,
    output_dir: Path | None = None,
) -> tuple[list[ExtractionRecord], int]:
    records: list[ExtractionRecord] = []
    errors = 0

    blocked_reason = _preflight_pdf(pdf_path, config)
    if blocked_reason:
        return [_policy_record(config, pdf_path, blocked_reason)], 1

    try:
        images = engine.extract(pdf_path)
    except Exception as exc:
        return [ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", str(exc), engine.name, 0, "none")], 1

    effective_output_dir = output_dir or config.output_dir
    effective_output_dir.mkdir(parents=True, exist_ok=True)
    seen_pages: set[int] = set()
    processed_images = 0
    output_bytes_total = 0
    output_limit_bytes = int(config.max_output_bytes_per_pdf_mb) * 1024 * 1024 if config.max_output_bytes_per_pdf_mb else None

    for item in images:
        if item.page is not None:
            seen_pages.add(item.page)
            if config.max_pages_per_pdf and len(seen_pages) > config.max_pages_per_pdf:
                records.append(_policy_record(config, pdf_path, f"PDF bloqueado por política: páginas acima do limite ({config.max_pages_per_pdf})."))
                errors += 1
                break

        processed_images += 1
        if config.max_images_per_pdf and processed_images > config.max_images_per_pdf:
            records.append(_policy_record(config, pdf_path, f"PDF bloqueado por política: imagens acima do limite ({config.max_images_per_pdf})."))
            errors += 1
            break

        started = time.perf_counter()
        try:
            decoded, ext, correction_status = (item.decoded, item.preferred_ext, "none") if item.preferred_ext else choose_output(item.decoded, item.filters, item.meta)
            if config.only_format and ext.lower() not in config.only_format:
                status, out_file, out_size = "skipped_format", None, 0
            else:
                if output_limit_bytes and (output_bytes_total + len(decoded)) > output_limit_bytes:
                    records.append(_policy_record(config, pdf_path, f"PDF bloqueado por política: bytes de saída acima do limite ({output_limit_bytes})."))
                    errors += 1
                    break
                name = _build_output_name(config, pdf_path, item.page, item.index, ext, item.raw)
                target = effective_output_dir / name
                target.write_bytes(decoded)
                status, out_file, out_size = "ok", str(target), len(decoded)
                output_bytes_total += out_size
            duration_ms = int((time.perf_counter() - started) * 1000)
            records.append(ExtractionRecord(config.schema_version, str(pdf_path), item.page, item.index, out_file, "|".join(item.filters), item.meta["Width"], item.meta["Height"], item.meta["BitsPerComponent"], item.meta["ColorSpace"], len(item.raw), out_size, status, None, engine.name, duration_ms, correction_status))
        except Exception as exc:
            errors += 1
            duration_ms = int((time.perf_counter() - started) * 1000)
            records.append(ExtractionRecord(config.schema_version, str(pdf_path), item.page, item.index, None, "|".join(item.filters), item.meta["Width"], item.meta["Height"], item.meta["BitsPerComponent"], item.meta["ColorSpace"], len(item.raw), 0, "error", str(exc), engine.name, duration_ms, "none"))
            if not config.continue_on_error:
                break
    return records, errors


def _extract_worker(
    pdf_path: Path,
    config: ExtractionConfig,
    queue: multiprocessing.Queue,
    isolated_output_dir: str | None = None,
) -> None:
    try:
        _set_resource_limits(config)
        engine = resolve_engine(config.engine)
        output_dir = Path(isolated_output_dir) if isolated_output_dir else None
        queue.put(_extract_impl(pdf_path, config, engine, output_dir=output_dir))
    except Exception as exc:
        queue.put(([ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", f"Worker exception: {exc}", f"{config.engine}:isolated", 0, "none")], 1))


def _extract_in_subprocess(pdf_path: Path, config: ExtractionConfig) -> tuple[list[ExtractionRecord], int]:
    queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=1)
    isolated_tmp_dir = Path(tempfile.mkdtemp(prefix="pdf-image-extractor-"))
    process = multiprocessing.Process(
        target=_extract_worker,
        args=(pdf_path, config, queue, str(isolated_tmp_dir)),
    )
    started = time.perf_counter()
    process.start()
    process.join(timeout=config.pdf_timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join()
        shutil.rmtree(isolated_tmp_dir, ignore_errors=True)
        timeout_ms = int((time.perf_counter() - started) * 1000)
        timeout_error = f"Timeout ao processar PDF (> {config.pdf_timeout_seconds}s)"
        record = ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "timeout", timeout_error, f"{config.engine}:isolated", timeout_ms, "none")
        return [record], 1

    if process.exitcode and process.exitcode != 0:
        shutil.rmtree(isolated_tmp_dir, ignore_errors=True)
        err = f"Worker finalizou com código {process.exitcode}"
        record = ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", err, f"{config.engine}:isolated", 0, "none")
        return [record], 1

    try:
        records, errors = queue.get_nowait()
    except Exception:
        shutil.rmtree(isolated_tmp_dir, ignore_errors=True)
        err = "Worker finalizou sem retornar resultado"
        record = ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", err, f"{config.engine}:isolated", 0, "none")
        return [record], 1

    moved_paths: dict[str, str] = {}
    try:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        for src in isolated_tmp_dir.iterdir():
            target = config.output_dir / src.name
            moved = Path(shutil.move(str(src), target))
            moved_paths[str(src)] = str(moved)
    except Exception as exc:
        shutil.rmtree(isolated_tmp_dir, ignore_errors=True)
        err = f"Falha ao consolidar artefatos isolados: {exc}"
        record = ExtractionRecord(config.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", err, f"{config.engine}:isolated", 0, "none")
        return [record], 1

    shutil.rmtree(isolated_tmp_dir, ignore_errors=True)

    normalized_records: list[ExtractionRecord] = []
    for r in records:
        normalized_records.append(
            ExtractionRecord(
                r.schema_version,
                r.input_file,
                r.page,
                r.image_index,
                moved_paths.get(r.output_file, r.output_file) if r.output_file else None,
                r.filters,
                r.width,
                r.height,
                r.bits_per_component,
                r.color_space,
                r.source_bytes,
                r.output_bytes,
                r.status,
                r.error,
                r.engine_used,
                r.duration_ms,
                r.correction_status,
            )
        )
    return normalized_records, errors


def extract_from_pdf(pdf_path: Path, config: ExtractionConfig, engine: ExtractorEngine | None = None) -> tuple[list[ExtractionRecord], int]:
    if config.isolate_pdf_processing:
        return _extract_in_subprocess(pdf_path, config)
    worker_engine = engine or resolve_engine(config.engine)
    return _extract_impl(pdf_path, config, worker_engine)


def _extract_from_pdf_nonisolated_worker(pdf_path: Path, config: ExtractionConfig) -> tuple[list[ExtractionRecord], int]:
    # Worker top-level para ser serializável no ProcessPoolExecutor (spawn).
    worker_engine = resolve_engine(config.engine)
    return _extract_impl(pdf_path, config, worker_engine)


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return float(ordered[idx])


class JobOrchestrator:
    def __init__(self, config: ExtractionConfig, *, progress_emitter: ProgressEmitter | None = None, report_writer: ReportWriter | None = None) -> None:
        self.config = config
        self.job_id = uuid.uuid4().hex[:12]
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter() if config.quiet else StdoutProgressEmitter(self.job_id)
        self.progress = progress_emitter
        self.report_writer = report_writer or ReportWriter()

    def _log(self, *, level: str, event: str, payload: dict) -> None:
        row = {"level": level, "event": event, "job_id": self.job_id, **payload}
        level_no = getattr(logging, level.upper(), logging.INFO)
        if (not self.config.quiet) or level_no >= logging.ERROR:
            LOGGER.log(
                level_no,
                event,
                extra={"event": event, "job_id": self.job_id, "payload": payload},
            )
        if self.config.telemetry_log_path:
            self.config.telemetry_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config.telemetry_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_metrics(self, records: list[ExtractionRecord], total_pdfs: int) -> None:
        if not self.config.metrics_output_path:
            return
        counts = Counter(r.status for r in records)
        durations = [int(r.duration_ms) for r in records if r.duration_ms is not None]
        per_status = defaultdict(list)
        per_engine = defaultdict(list)
        for r in records:
            per_status[r.status].append(int(r.duration_ms))
            per_engine[r.engine_used].append(int(r.duration_ms))

        payload = {
            "job_id": self.job_id,
            "pdf_total": total_pdfs,
            "records_total": len(records),
            "status_counts": dict(counts),
            "duration_ms": {
                "p50": _percentile(durations, 50),
                "p90": _percentile(durations, 90),
                "p99": _percentile(durations, 99),
            },
            "duration_ms_by_status": {k: {"p50": _percentile(v, 50), "p90": _percentile(v, 90), "p99": _percentile(v, 99)} for k, v in per_status.items()},
            "duration_ms_by_engine": {k: {"p50": _percentile(v, 50), "p90": _percentile(v, 90), "p99": _percentile(v, 99)} for k, v in per_engine.items()},
        }
        self.config.metrics_output_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.metrics_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(self) -> tuple[list[ExtractionRecord], int]:
        pdfs = collect_pdfs_from_inputs(self.config.input_paths, self.config.recursive)
        if not pdfs:
            return [], 2

        engine = None if self.config.isolate_pdf_processing else resolve_engine(self.config.engine)
        all_records: list[ExtractionRecord] = []
        total_errors = 0
        start_by_pdf: dict[Path, float] = {}
        self._log(level="INFO", event="job_started", payload={"pdf_total": len(pdfs), "engine": self.config.engine})

        if self.config.isolate_pdf_processing:
            for i, pdf in enumerate(pdfs, start=1):
                start_by_pdf[pdf] = time.perf_counter()
                self.progress.on_pdf_started(pdf, i, len(pdfs))
                try:
                    records, errors = extract_from_pdf(pdf, self.config, engine)
                except Exception as exc:
                    self.progress.on_error(pdf, exc)
                    self._log(level="ERROR", event="pdf_failed", payload={"pdf_path": str(pdf), "error": str(exc)})
                    raise
                self.progress.on_pdf_finished(pdf, records, errors, i, len(pdfs))
                duration_ms = int((time.perf_counter() - start_by_pdf[pdf]) * 1000)
                main_status = records[0].status if records else ("error" if errors else "ok")
                self._log(level="INFO", event="pdf_finished", payload={"pdf_path": str(pdf), "engine": self.config.engine, "status": main_status, "duration_ms": duration_ms})
                all_records.extend(records)
                total_errors += errors
                if errors and self.config.fail_fast:
                    all_records.append(_interrupted_record(self.config, "Execução interrompida por fail_fast após primeiro erro."))
                    self._log(level="WARNING", event="job_interrupted", payload={"reason": "fail_fast"})
                    break
        else:
            max_workers = max(1, min(self.config.max_workers, len(pdfs)))
            mp_ctx = _get_multiprocessing_context()
            executor = ProcessPoolExecutor(max_workers=max_workers, mp_context=mp_ctx)
            interrupted = False
            try:
                futures = {}
                for i, pdf in enumerate(pdfs, start=1):
                    start_by_pdf[pdf] = time.perf_counter()
                    self.progress.on_pdf_started(pdf, i, len(pdfs))
                    futures[executor.submit(_extract_from_pdf_nonisolated_worker, pdf, self.config)] = (pdf, i)

                for fut in as_completed(futures):
                    pdf, i = futures[fut]
                    try:
                        records, errors = fut.result()
                    except Exception as exc:
                        self.progress.on_error(pdf, exc)
                        self._log(level="ERROR", event="pdf_failed", payload={"pdf_path": str(pdf), "error": str(exc)})
                        raise
                    self.progress.on_pdf_finished(pdf, records, errors, i, len(pdfs))
                    duration_ms = int((time.perf_counter() - start_by_pdf[pdf]) * 1000)
                    main_status = records[0].status if records else ("error" if errors else "ok")
                    self._log(level="INFO", event="pdf_finished", payload={"pdf_path": str(pdf), "engine": self.config.engine, "status": main_status, "duration_ms": duration_ms})
                    all_records.extend(records)
                    total_errors += errors
                    if errors and self.config.fail_fast:
                        interrupted = True
                        for pending in futures:
                            if pending is fut:
                                continue
                            pending.cancel()
                        break
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

            if interrupted:
                all_records.append(_interrupted_record(self.config, "Execução interrompida por fail_fast após primeiro erro."))
                self._log(level="WARNING", event="job_interrupted", payload={"reason": "fail_fast"})

        self.report_writer.write(all_records, self.config.report, self.config.report_formats)
        self._write_metrics(all_records, len(pdfs))
        self._log(level="INFO", event="job_finished", payload={"records_total": len(all_records), "errors_total": total_errors})
        return all_records, (0 if total_errors == 0 else 1)


def run_extraction_job(config: ExtractionConfig) -> tuple[list[ExtractionRecord], int]:
    return JobOrchestrator(config).run()
