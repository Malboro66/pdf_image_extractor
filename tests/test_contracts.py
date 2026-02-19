import unittest
from pathlib import Path

from pdf_image_extractor.adapters.engines.base import ExtractorEngine
from pdf_image_extractor.adapters.engines.fallback import FallbackEngine
from pdf_image_extractor.core.models import ExtractionConfig
from pdf_image_extractor.core.pipeline import collect_pdfs_from_inputs, resolve_engine


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


if __name__ == "__main__":
    unittest.main()
