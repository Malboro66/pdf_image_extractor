import unittest
import zlib
from unittest import mock

from pdf_image_extractor.core.decoders import ExtractionError, _safe_zlib_decompress


class SafeZlibDecompressTests(unittest.TestCase):
    def test_decompresses_valid_payload_within_limit(self) -> None:
        raw = b"abc" * 1024
        compressed = zlib.compress(raw)

        decoded = _safe_zlib_decompress(compressed, max_size_mb=1)

        self.assertEqual(decoded, raw)

    def test_raises_when_output_exceeds_limit(self) -> None:
        raw = b"\x00" * (2 * 1024 * 1024)
        compressed = zlib.compress(raw, level=9)

        with self.assertRaises(ExtractionError):
            _safe_zlib_decompress(compressed, max_size_mb=1)

    def test_stops_immediately_when_unconsumed_tail_is_detected(self) -> None:
        class FakeDecompressor:
            def __init__(self) -> None:
                self.unconsumed_tail = b""
                self.eof = True
                self.unused_data = b""
                self.calls = 0

            def decompress(self, _chunk: bytes, max_length: int) -> bytes:
                self.calls += 1
                self.unconsumed_tail = b"excess"
                return b"a" * max_length

            def flush(self, _max_length: int) -> bytes:
                return b""

        fake = FakeDecompressor()
        payload = b"x" * (128 * 1024)

        with mock.patch("pdf_image_extractor.core.decoders.zlib.decompressobj", return_value=fake):
            with self.assertRaises(ExtractionError):
                _safe_zlib_decompress(payload, max_size_mb=1)

        self.assertEqual(fake.calls, 1)


if __name__ == "__main__":
    unittest.main()
