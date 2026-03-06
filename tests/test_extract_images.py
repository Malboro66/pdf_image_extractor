import json
import tempfile
import time
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from pathlib import Path

from extract_images import _apply_decode_transform, _raw_to_png, extract_from_pdf, run_extraction_job
from pdf_image_extractor.adapters.engines.fallback import FallbackEngine
from pdf_image_extractor.core import pipeline
from pdf_image_extractor.core.models import ExtractionRecord


def _write_pdf_with_image(path: Path, image_payload: bytes, image_dict: bytes) -> None:
    data = b"%PDF-1.4\n" \
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n" \
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n" \
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /XObject <</Im0 4 0 R>> >> /Contents 5 0 R >>\nendobj\n" \
        b"4 0 obj\n" + image_dict + b"\nstream\n" + image_payload + b"\nendstream\nendobj\n" \
        b"5 0 obj\n<< /Length 35 >>\nstream\nq\n100 0 0 100 0 0 cm\n/Im0 Do\nQ\nendstream\nendobj\n" \
        b"xref\n0 6\n0000000000 65535 f \ntrailer << /Root 1 0 R /Size 6 >>\nstartxref\n0\n%%EOF\n"
    path.write_bytes(data)


class ExtractImagesTests(unittest.TestCase):
    def test_extract_dct_jpg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "a.pdf"
            out = tmp / "out"
            img = b"\xff\xd8\xff\xd9"
            image_dict = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>"
            _write_pdf_with_image(pdf, img, image_dict)

            records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)
            self.assertEqual(errors, 0)
            self.assertTrue(any(r.status == "ok" for r in records))
            self.assertEqual(len(list(out.glob("*.jpg"))), 1)
            self.assertTrue(all(hasattr(r, "correction_status") for r in records))

    def test_extract_flate_to_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "b.pdf"
            out = tmp / "out"
            raw = bytes([255, 0, 0])
            comp = zlib.compress(raw)
            image_dict = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length 20 >>"
            _write_pdf_with_image(pdf, comp, image_dict)

            records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)
            self.assertEqual(errors, 0)
            self.assertTrue(any(r.status == "ok" for r in records))
            pngs = list(out.glob("*.png"))
            self.assertEqual(len(pngs), 1)
            self.assertTrue(pngs[0].read_bytes().startswith(b"\x89PNG"))

    def test_extract_with_subtype_without_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "nospace.pdf"
            out = tmp / "out"
            raw = bytes([10, 20, 30])
            comp = zlib.compress(raw)
            image_dict = b"<< /Type /XObject /Subtype/Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length 20 >>"
            _write_pdf_with_image(pdf, comp, image_dict)

            records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)
            self.assertEqual(errors, 0)
            self.assertTrue(any(r.status == "ok" for r in records))
            self.assertEqual(len(list(out.glob("*.png"))), 1)

    def test_skip_text_like_image_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "mask.pdf"
            out = tmp / "out"
            raw = b"\x00\xff"
            image_dict = b"<< /Type /XObject /Subtype /Image /Width 8 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 1 /ImageMask true /Filter /FlateDecode /Length 2 >>"
            _write_pdf_with_image(pdf, raw, image_dict)

            records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)
            self.assertEqual(errors, 0)
            self.assertEqual(records, [])


    def test_fallback_skips_oversized_unclosed_object_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "oversized_unclosed.pdf"
            out = tmp / "out"
            img = b"\xff\xd8\xff\xd9"
            dct = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>"
            oversized = b"A" * 2048
            data = (
                b"%PDF-1.4\n"
                b"1 0 obj\n<< /Length 4096 >>\nstream\n" + oversized + b"\n"
                b"2 0 obj\n" + dct + b"\nstream\n" + img + b"\nendstream\nendobj\n"
                b"xref\n0 3\n0000000000 65535 f \ntrailer << /Root 1 0 R /Size 3 >>\nstartxref\n0\n%%EOF\n"
            )
            pdf.write_bytes(data)

            with mock.patch.object(FallbackEngine, "MAX_OBJECT_BYTES", 1024):
                records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)

            self.assertEqual(errors, 0)
            self.assertTrue(any(r.status == "ok" for r in records))
            self.assertGreaterEqual(len(list(out.glob("*"))), 1)


    def test_get_multiprocessing_context_cached_and_spawn(self) -> None:
        with mock.patch("pdf_image_extractor.core.pipeline._MP_CONTEXT", None):
            with mock.patch("pdf_image_extractor.core.pipeline.multiprocessing.get_context") as get_ctx:
                sentinel = object()
                get_ctx.return_value = sentinel

                ctx1 = pipeline._get_multiprocessing_context()
                ctx2 = pipeline._get_multiprocessing_context()

            self.assertIs(ctx1, sentinel)
            self.assertIs(ctx2, sentinel)
            get_ctx.assert_called_once_with("spawn")

    def test_get_multiprocessing_context_thread_safe_single_init(self) -> None:
        with mock.patch("pdf_image_extractor.core.pipeline._MP_CONTEXT", None):
            with mock.patch("pdf_image_extractor.core.pipeline.multiprocessing.get_context") as get_ctx:
                sentinel = object()
                get_ctx.return_value = sentinel

                with ThreadPoolExecutor(max_workers=8) as pool:
                    contexts = list(pool.map(lambda _: pipeline._get_multiprocessing_context(), range(32)))

            self.assertTrue(all(ctx is sentinel for ctx in contexts))
            get_ctx.assert_called_once_with("spawn")

    def test_extract_in_subprocess_uses_context_process_and_bounded_queue(self) -> None:
        cfg = pipeline.ExtractionConfig(input_paths=[], output_dir=Path("."), engine="fallback", isolate_pdf_processing=True)
        fake_queue = mock.Mock()
        fake_process = mock.Mock()
        fake_process.is_alive.return_value = False
        fake_process.exitcode = 0
        fake_queue.get_nowait.return_value = ([], 0)
        fake_ctx = mock.Mock()
        fake_ctx.Queue.return_value = fake_queue
        fake_ctx.Process.return_value = fake_process

        with mock.patch("pdf_image_extractor.core.pipeline._get_multiprocessing_context", return_value=fake_ctx):
            records, errors = pipeline._extract_in_subprocess(Path("dummy.pdf"), cfg)

        self.assertEqual(records, [])
        self.assertEqual(errors, 0)
        fake_ctx.Queue.assert_called_once_with(maxsize=1)
        fake_ctx.Process.assert_called_once()
        fake_process.start.assert_called_once()
        fake_process.join.assert_called()
        fake_queue.close.assert_called_once()
        fake_queue.join_thread.assert_called_once()

    def test_extract_in_subprocess_timeout_still_closes_queue(self) -> None:
        cfg = pipeline.ExtractionConfig(
            input_paths=[],
            output_dir=Path("."),
            engine="fallback",
            isolate_pdf_processing=True,
            pdf_timeout_seconds=1,
        )
        fake_queue = mock.Mock()
        fake_process = mock.Mock()
        fake_process.is_alive.return_value = True
        fake_process.exitcode = None
        fake_ctx = mock.Mock()
        fake_ctx.Queue.return_value = fake_queue
        fake_ctx.Process.return_value = fake_process

        with mock.patch("pdf_image_extractor.core.pipeline._get_multiprocessing_context", return_value=fake_ctx):
            records, errors = pipeline._extract_in_subprocess(Path("dummy.pdf"), cfg)

        self.assertEqual(errors, 1)
        self.assertEqual(records[0].status, "timeout")
        fake_queue.close.assert_called_once()
        fake_queue.join_thread.assert_called_once()

    def test_corrupted_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "bad.pdf"
            out = tmp / "out"
            pdf.write_bytes(b"not-a-valid-pdf")
            records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)
            self.assertEqual(errors, 1)
            self.assertTrue(any(r.status == "blocked_policy" for r in records))

    def test_decode_transform_avoids_negative(self) -> None:
        decoded = bytes([0])
        meta = {"BitsPerComponent": 8, "ColorSpace": "DeviceGray", "Decode": [1.0, 0.0]}
        transformed = _apply_decode_transform(decoded, meta)
        self.assertEqual(transformed, bytes([255]))

    def test_raw_to_png_guard(self) -> None:
        self.assertIsNone(_raw_to_png(b"\x00", width=1, height=1, color_space="DeviceCMYK", bits=8))

    def test_run_extraction_job_empty_input_returns_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            records, code = run_extraction_job(
                input_paths=[tmp / "missing.pdf"],
                output_dir=tmp / "out",
            )
            self.assertEqual(records, [])
            self.assertEqual(code, 2)

    def test_run_extraction_job_timeout_sets_explicit_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out = tmp / "out"
            pdf = tmp / "slow.pdf"
            img = b"\xff\xd8\xff\xd9"
            dct = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>"
            _write_pdf_with_image(pdf, img, dct)

            records, code = run_extraction_job(
                input_paths=[pdf],
                output_dir=out,
                engine="fallback",
                quiet=True,
                isolate_pdf_processing=True,
                pdf_timeout_seconds=0,
            )
            self.assertEqual(code, 1)
            self.assertTrue(any(r.status == "timeout" for r in records))

    def test_run_extraction_job_invalid_signature_blocked_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out = tmp / "out"
            fake = tmp / "fake.pdf"
            fake.write_bytes(b"NOTPDF")

            records, code = run_extraction_job(
                input_paths=[fake],
                output_dir=out,
                engine="fallback",
                quiet=True,
                isolate_pdf_processing=True,
            )
            self.assertEqual(code, 1)
            self.assertTrue(any(r.status == "blocked_policy" for r in records))

    def test_run_extraction_job_limit_images_blocked_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out = tmp / "out"
            pdf = tmp / "two_images.pdf"
            img1 = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>\nstream\n\xff\xd8\xff\xd9\nendstream\nendobj\n"
            img2 = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>\nstream\n\xff\xd8\xff\xd9\nendstream\nendobj\n"
            data = b"%PDF-1.4\n" \
                b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n" \
                b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n" \
                b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /XObject <</Im0 4 0 R /Im1 5 0 R>> >> /Contents 6 0 R >>\nendobj\n" \
                b"4 0 obj\n" + img1 + \
                b"5 0 obj\n" + img2 + \
                b"6 0 obj\n<< /Length 35 >>\nstream\nq\n100 0 0 100 0 0 cm\n/Im0 Do\n/Im1 Do\nQ\nendstream\nendobj\n" \
                b"xref\n0 7\n0000000000 65535 f \ntrailer << /Root 1 0 R /Size 7 >>\nstartxref\n0\n%%EOF\n"
            pdf.write_bytes(data)

            records, code = run_extraction_job(
                input_paths=[pdf],
                output_dir=out,
                engine="fallback",
                quiet=True,
                isolate_pdf_processing=True,
                max_images_per_pdf=1,
            )
            self.assertEqual(code, 1)
            self.assertTrue(any(r.status == "blocked_policy" for r in records))

    def test_fail_fast_cancels_pending_futures_and_marks_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out = tmp / "out"
            pdfs = []
            for i in range(6):
                pdf = tmp / f"{i:02d}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
                pdfs.append(pdf)

            def fake_worker(pdf_path, cfg):
                if pdf_path.name == "00.pdf":
                    rec = ExtractionRecord(cfg.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", "boom", "fake", 0, "none")
                    return [rec], 1
                time.sleep(0.35)
                rec = ExtractionRecord(cfg.schema_version, str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "ok", None, "fake", 0, "none")
                return [rec], 0

            class FakeProcessPoolExecutor:
                def __init__(self, max_workers, mp_context):
                    self._executor = ThreadPoolExecutor(max_workers=max_workers)

                def submit(self, fn, *args, **kwargs):
                    return self._executor.submit(fn, *args, **kwargs)

                def shutdown(self, wait=True, cancel_futures=False):
                    self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

            with mock.patch("pdf_image_extractor.core.pipeline._extract_from_pdf_nonisolated_worker", side_effect=fake_worker):
                with mock.patch("pdf_image_extractor.core.pipeline.ProcessPoolExecutor", FakeProcessPoolExecutor):
                    started = time.perf_counter()
                    records, code = run_extraction_job(
                        input_paths=pdfs,
                        output_dir=out,
                        engine="fallback",
                        quiet=True,
                        isolate_pdf_processing=False,
                        fail_fast=True,
                        max_workers=2,
                    )
                    elapsed = time.perf_counter() - started

            self.assertEqual(code, 1)
            self.assertLess(elapsed, 0.9)
            self.assertTrue(any(getattr(r, "status", None) == "interrupted" for r in records))

    def test_run_extraction_job_writes_structured_telemetry_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out = tmp / "out"
            pdf = tmp / "one.pdf"
            img = b"\xff\xd8\xff\xd9"
            dct = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>"
            _write_pdf_with_image(pdf, img, dct)
            telemetry = tmp / "telemetry.jsonl"
            metrics = tmp / "metrics.json"

            records, code = run_extraction_job(
                input_paths=[pdf],
                output_dir=out,
                engine="fallback",
                quiet=True,
                isolate_pdf_processing=False,
                telemetry_log_path=telemetry,
                metrics_output_path=metrics,
            )
            self.assertEqual(code, 0)
            self.assertTrue(telemetry.exists())
            self.assertTrue(metrics.exists())

            lines = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(any(item.get("event") == "job_started" for item in lines))
            self.assertTrue(any(item.get("event") == "pdf_finished" for item in lines))
            self.assertTrue(all("job_id" in item for item in lines))

            payload = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertIn("job_id", payload)
            self.assertIn("status_counts", payload)
            self.assertIn("duration_ms", payload)

    def test_run_extraction_job_multiple_inputs_unique_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out = tmp / "out"
            pdf1 = tmp / "a.pdf"
            pdf2 = tmp / "b.pdf"
            img = b"\xff\xd8\xff\xd9"
            dct = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>"
            _write_pdf_with_image(pdf1, img, dct)
            _write_pdf_with_image(pdf2, img, dct)

            records, code = run_extraction_job(
                input_paths=[pdf1, pdf2],
                output_dir=out,
                engine="fallback",
                quiet=True,
            )
            self.assertEqual(code, 0)
            ok_files = [r.output_file for r in records if r.status == "ok"]
            self.assertEqual(len(ok_files), 2)
            self.assertEqual(len(set(ok_files)), 2)


if __name__ == "__main__":
    unittest.main()
