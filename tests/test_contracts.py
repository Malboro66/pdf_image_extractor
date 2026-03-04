import io
import logging
import unittest
from pathlib import Path

from pdf_image_extractor.adapters.engines.base import ExtractorEngine
from pdf_image_extractor.adapters.engines.fallback import FallbackEngine
from pdf_image_extractor.core.models import ExtractionConfig
from pdf_image_extractor.core.pipeline import JobOrchestrator, NullProgressEmitter, ProgressEmitter, ReportWriter, collect_pdfs_from_inputs, resolve_engine


class ContractTests(unittest.TestCase):
    def test_resolve_engine_contract(self):
        engine = resolve_engine("fallback")
        self.assertIsInstance(engine, FallbackEngine)
        self.assertTrue(hasattr(engine, "extract"))
        self.assertTrue(hasattr(engine, "name"))

    def test_collect_pdfs_from_inputs_dedup(self):
        base = Path("tests/fixtures").resolve()
        pdf = base / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        pdfs = collect_pdfs_from_inputs([pdf, base], recursive=False)
        self.assertIn(pdf.resolve(), pdfs)
        self.assertEqual(len(pdfs), 1)
        pdf.unlink()

    def test_config_defaults(self):
        cfg = ExtractionConfig(input_paths=[Path("a.pdf")], output_dir=Path("out"))
        self.assertEqual(cfg.schema_version, "1.1")
        self.assertEqual(cfg.max_workers, 4)
        self.assertTrue(cfg.isolate_pdf_processing)
        self.assertEqual(cfg.pdf_timeout_seconds, 60)
        self.assertEqual(cfg.max_pdf_size_mb, 200)
        self.assertEqual(cfg.max_images_per_pdf, 2000)
        self.assertIsNone(cfg.telemetry_log_path)
        self.assertIsNone(cfg.metrics_output_path)


    def test_report_formats_default_is_mutable_and_not_shared(self):
        cfg_a = ExtractionConfig(input_paths=[Path("a.pdf")], output_dir=Path("out"))
        cfg_b = ExtractionConfig(input_paths=[Path("b.pdf")], output_dir=Path("out"))

        cfg_a.report_formats.add("xml")

        self.assertIn("xml", cfg_a.report_formats)
        self.assertNotIn("xml", cfg_b.report_formats)


    def test_quiet_mode_still_emits_error_logs_to_stderr(self):
        cfg = ExtractionConfig(input_paths=[Path("a.pdf")], output_dir=Path("out"), quiet=True)
        orchestrator = JobOrchestrator(cfg, progress_emitter=NullProgressEmitter(), report_writer=ReportWriter())

        logger = logging.getLogger("pdf_image_extractor")
        err_handler = next(h for h in logger.handlers if getattr(h, "level", 0) == logging.ERROR)
        original_stream = err_handler.stream
        capture = io.StringIO()
        err_handler.stream = capture
        try:
            orchestrator._log(level="INFO", event="info_hidden", payload={"k": 1})
            orchestrator._log(level="ERROR", event="error_visible", payload={"k": 2})
        finally:
            err_handler.stream = original_stream

        output = capture.getvalue()
        self.assertIn('"level": "ERROR"', output)
        self.assertIn('"event": "error_visible"', output)
        self.assertNotIn('"event": "info_hidden"', output)

    def test_job_orchestrator_uses_components(self):
        class DummyEmitter(NullProgressEmitter):
            def __init__(self):
                self.started = 0
                self.finished = 0

            def on_pdf_started(self, pdf, index, total):
                self.started += 1

            def on_pdf_finished(self, pdf, records, errors, index, total):
                self.finished += 1

        class DummyWriter(ReportWriter):
            def __init__(self):
                self.called = False

            def write(self, records, report_base, formats):
                self.called = True

        cfg = ExtractionConfig(input_paths=[Path('missing.pdf')], output_dir=Path('out'))
        emitter = DummyEmitter()
        writer = DummyWriter()
        records, code = JobOrchestrator(cfg, progress_emitter=emitter, report_writer=writer).run()
        self.assertEqual(records, [])
        self.assertEqual(code, 2)
        self.assertEqual(emitter.started, 0)
        self.assertEqual(emitter.finished, 0)
        self.assertFalse(writer.called)


if __name__ == "__main__":
    unittest.main()
