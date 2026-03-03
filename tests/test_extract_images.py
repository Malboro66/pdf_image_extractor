import tempfile
import unittest
import zlib
from pathlib import Path

from extract_images import _apply_decode_transform, _raw_to_png, extract_from_pdf, run_extraction_job


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
    def test_output_filenames_are_unique_across_multiple_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            source = tmp / "pdfs"
            source.mkdir()
            out = tmp / "out"

            image_dict = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length 4 >>"
            _write_pdf_with_image(source / "a.pdf", b"\xff\xd8\xff\xd9", image_dict)
            _write_pdf_with_image(source / "b.pdf", b"\xff\xd8\xff\xe0", image_dict)

            records, code = run_extraction_job(
                input_path=source,
                output_dir=out,
                prefix="img",
                engine="fallback",
                report=tmp / "report",
            )

            self.assertEqual(code, 0)
            self.assertEqual(len([r for r in records if r.status == "ok"]), 2)
            files = sorted(out.glob("*.jpg"))
            self.assertEqual(len(files), 2)
            self.assertEqual(len({f.name for f in files}), 2)

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

    def test_extract_with_endstream_inside_binary_uses_declared_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "length.pdf"
            out = tmp / "out"
            marker = b"endstream"
            raw = b"\x00\x01" + marker + b"\x02\x03"
            image_dict = b"<< /Type /XObject /Subtype /Image /Length " + str(len(raw)).encode("ascii") + b" >>"
            encoded = raw
            _write_pdf_with_image(pdf, encoded, image_dict)

            records, errors = extract_from_pdf(pdf, out, "img", {"bin"}, "fallback", True)
            self.assertEqual(errors, 0)
            ok_records = [r for r in records if r.status == "ok"]
            self.assertEqual(len(ok_records), 1)
            output = Path(ok_records[0].output_file or "")
            self.assertTrue(output.exists())
            self.assertEqual(output.read_bytes(), raw)

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
            if out.exists():
                self.assertEqual(list(out.glob("*")), [])

    def test_corrupted_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            pdf = tmp / "bad.pdf"
            out = tmp / "out"
            pdf.write_bytes(b"not-a-valid-pdf")
            records, errors = extract_from_pdf(pdf, out, "img", None, "fallback", True)
            self.assertEqual(errors, 0)
            self.assertEqual(records, [])

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
                input_path=tmp / "missing.pdf",
                output_dir=tmp / "out",
            )
            self.assertEqual(records, [])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
