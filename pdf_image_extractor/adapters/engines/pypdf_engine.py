from __future__ import annotations

from pathlib import Path

from pdf_image_extractor.adapters.engines.base import ParsedImage


class PyPdfEngine:
    name = "pypdf"

    def extract(self, pdf_path: Path) -> list[ParsedImage]:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        images: list[ParsedImage] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            for idx, image in enumerate(page.images, start=1):
                name_l = image.name.lower()
                if "smask" in name_l or "mask" in name_l:
                    continue
                ext = image.name.split(".")[-1].lower() if "." in image.name else "bin"
                images.append(
                    ParsedImage(
                        page_idx,
                        idx,
                        image.data,
                        image.data,
                        [f"direct:{ext}"],
                        {"Width": None, "Height": None, "BitsPerComponent": None, "ColorSpace": None, "ImageMask": False, "Decode": None},
                        ext,
                    )
                )
        return images
