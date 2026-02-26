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
