import types
import unittest
from pathlib import Path
from unittest import mock

from pdf_image_extractor.adapters.engines.pypdf_engine import PyPdfEngine


class _FakeImage:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self.data = data


class _FakePage:
    def __init__(self, images):
        self.images = images


class _FakeReader:
    def __init__(self, pages):
        self.pages = pages


class PyPdfEngineTests(unittest.TestCase):
    def test_extract_skips_masks_and_maps_metadata(self) -> None:
        pages = [
            _FakePage([
                _FakeImage("Im0.jpg", b"\xff\xd8\xff\xd9"),
                _FakeImage("Im0.smask", b"mask"),
                _FakeImage("alphaMask", b"mask2"),
                _FakeImage("NoExt", b"raw"),
            ])
        ]

        def fake_reader(path: str):
            self.assertTrue(path.endswith("doc.pdf"))
            return _FakeReader(pages)

        fake_module = types.SimpleNamespace(PdfReader=fake_reader)
        with mock.patch.dict("sys.modules", {"pypdf": fake_module}):
            images = PyPdfEngine().extract(Path("doc.pdf"))

        self.assertEqual(len(images), 2)
        first = images[0]
        self.assertEqual(first.page, 1)
        self.assertEqual(first.index, 1)
        self.assertEqual(first.preferred_ext, "jpg")
        self.assertEqual(first.filters, ["direct:jpg"])
        self.assertEqual(first.decoded, b"\xff\xd8\xff\xd9")

        second = images[1]
        self.assertEqual(second.preferred_ext, "bin")
        self.assertEqual(second.filters, ["direct:bin"])

    def test_extract_handles_pages_without_images(self) -> None:
        fake_module = types.SimpleNamespace(PdfReader=lambda _path: _FakeReader([_FakePage([]), _FakePage([])]))
        with mock.patch.dict("sys.modules", {"pypdf": fake_module}):
            images = PyPdfEngine().extract(Path("empty.pdf"))

        self.assertEqual(images, [])


if __name__ == "__main__":
    unittest.main()
