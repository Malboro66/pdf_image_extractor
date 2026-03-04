import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf_image_extractor.core.models import ExtractionConfig, ExtractionRecord
from pdf_image_extractor.core.pipeline import _extract_in_subprocess


class _FakeQueue:
    def __init__(self, payload):
        self._payload = payload

    def get_nowait(self):
        return self._payload


class PipelineIsolationTests(unittest.TestCase):
    def test_moves_artifacts_from_isolated_temp_dir_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out_dir = tmp / "out"
            pdf = tmp / "doc.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

            isolated_dir = tmp / "isolated"
            isolated_dir.mkdir()
            tmp_output = isolated_dir / "imagem_0001_x.jpg"
            tmp_output.write_bytes(b"\xff\xd8\xff\xd9")

            record = ExtractionRecord(
                schema_version="1.1",
                input_file=str(pdf),
                page=None,
                image_index=1,
                output_file=str(tmp_output),
                filters="DCT",
                width=1,
                height=1,
                bits_per_component=8,
                color_space="DeviceRGB",
                source_bytes=4,
                output_bytes=4,
                status="ok",
                error=None,
                engine_used="fallback",
                duration_ms=1,
                correction_status="none",
            )

            class FakeProcess:
                def __init__(self, *args, **kwargs):
                    self.exitcode = 0

                def start(self):
                    return

                def join(self, timeout=None):
                    return

                def is_alive(self):
                    return False

            cfg = ExtractionConfig(input_paths=[pdf], output_dir=out_dir, isolate_pdf_processing=True)
            with mock.patch("pdf_image_extractor.core.pipeline.tempfile.mkdtemp", return_value=str(isolated_dir)), \
                 mock.patch("pdf_image_extractor.core.pipeline.multiprocessing.Process", side_effect=FakeProcess), \
                 mock.patch("pdf_image_extractor.core.pipeline.multiprocessing.Queue", return_value=_FakeQueue(([record], 0))):
                records, errors = _extract_in_subprocess(pdf, cfg)

            self.assertEqual(errors, 0)
            self.assertTrue((out_dir / tmp_output.name).exists())
            self.assertFalse(isolated_dir.exists())
            self.assertEqual(records[0].output_file, str(out_dir / tmp_output.name))

    def test_discards_isolated_temp_dir_on_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out_dir = tmp / "out"
            pdf = tmp / "doc.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

            isolated_dir = tmp / "isolated"
            isolated_dir.mkdir()
            (isolated_dir / "partial.jpg").write_bytes(b"partial")

            class FakeProcess:
                def __init__(self, *args, **kwargs):
                    self.exitcode = None

                def start(self):
                    return

                def join(self, timeout=None):
                    return

                def is_alive(self):
                    return True

                def terminate(self):
                    return

            cfg = ExtractionConfig(
                input_paths=[pdf],
                output_dir=out_dir,
                isolate_pdf_processing=True,
                pdf_timeout_seconds=1,
            )
            with mock.patch("pdf_image_extractor.core.pipeline.tempfile.mkdtemp", return_value=str(isolated_dir)), \
                 mock.patch("pdf_image_extractor.core.pipeline.multiprocessing.Process", side_effect=FakeProcess), \
                 mock.patch("pdf_image_extractor.core.pipeline.multiprocessing.Queue", return_value=_FakeQueue(([], 0))):
                records, errors = _extract_in_subprocess(pdf, cfg)

            self.assertEqual(errors, 1)
            self.assertEqual(records[0].status, "timeout")
            self.assertFalse(isolated_dir.exists())
            self.assertFalse(out_dir.exists())


if __name__ == "__main__":
    unittest.main()
