from __future__ import annotations

import math
import re
from pathlib import Path

from pdf_image_extractor.adapters.engines.base import ParsedImage
from pdf_image_extractor.core.decoders import decode_stream

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.DOTALL)
FILTER_NAME_RE = re.compile(rb"/([A-Za-z0-9]+)")
NUMBER_RE = re.compile(rb"/(Width|Height|BitsPerComponent)\s+(\d+)")
COLORSPACE_RE = re.compile(rb"/ColorSpace\s*/([A-Za-z0-9]+)")
DECODE_RE = re.compile(rb"/Decode\s*\[(.*?)\]", re.DOTALL)
FLOAT_RE = re.compile(rb"-?\d+(?:\.\d+)?")
SUBTYPE_IMAGE_RE = re.compile(rb"/Subtype\s*/Image")


class FallbackEngine:
    name = "fallback"

    @staticmethod
    def _extract_filters(dictionary_bytes: bytes) -> list[str]:
        pos = dictionary_bytes.find(b"/Filter")
        if pos == -1:
            return []
        after = dictionary_bytes[pos + len(b"/Filter"):]
        if b"[" in after[:16]:
            start = after.find(b"[")
            end = after.find(b"]", start)
            if start != -1 and end != -1:
                return [x.decode("ascii", errors="ignore") for x in FILTER_NAME_RE.findall(after[start:end + 1])]
        match = FILTER_NAME_RE.search(after)
        return [match.group(1).decode("ascii", errors="ignore")] if match else []

    @staticmethod
    def _extract_metadata(dictionary_bytes: bytes):
        values = {"Width": None, "Height": None, "BitsPerComponent": None, "ColorSpace": None, "ImageMask": False, "Decode": None}
        for key, raw in NUMBER_RE.findall(dictionary_bytes):
            values[key.decode()] = int(raw)
        cs = COLORSPACE_RE.search(dictionary_bytes)
        if cs:
            values["ColorSpace"] = cs.group(1).decode("ascii", errors="ignore")
        values["ImageMask"] = bool(re.search(rb"/ImageMask\s+(true|1)", dictionary_bytes))
        dm = DECODE_RE.search(dictionary_bytes)
        if dm:
            vals = [float(v.decode("ascii")) for v in FLOAT_RE.findall(dm.group(1))]
            values["Decode"] = vals if vals else None
        return values

    @staticmethod
    def _bit_entropy(data: bytes) -> float:
        if not data:
            return 0.0
        ones = sum(bin(b).count("1") for b in data)
        total = len(data) * 8
        p1 = ones / total
        if p1 in (0.0, 1.0):
            return 0.0
        p0 = 1.0 - p1
        return -p0 * math.log2(p0) - p1 * math.log2(p1)

    @staticmethod
    def _repetition_ratio(data: bytes) -> float:
        if len(data) < 2:
            return 1.0
        repeats = sum(1 for i in range(1, len(data)) if data[i] == data[i - 1])
        return repeats / (len(data) - 1)

    @classmethod
    def _looks_like_text_artifact(cls, meta: dict, filters: list[str], decoded: bytes) -> bool:
        if meta.get("ImageMask"):
            return True

        bits = meta.get("BitsPerComponent")
        area = (meta.get("Width") or 0) * (meta.get("Height") or 0)
        direct = any(f in {"DCTDecode", "DCT", "JPXDecode", "JPX", "CCITTFaxDecode", "CCF"} for f in filters)
        if direct:
            return False

        if bits == 1 and area <= 4096:
            return True

        if bits == 1 and area <= 20000:
            entropy = cls._bit_entropy(decoded)
            repetition = cls._repetition_ratio(decoded)
            if entropy < 0.25 or repetition > 0.92:
                return True

        return False

    def extract(self, pdf_path: Path) -> list[ParsedImage]:
        pdf_bytes = pdf_path.read_bytes()
        images: list[ParsedImage] = []
        idx = 0
        for match in OBJ_RE.finditer(pdf_bytes):
            body = match.group(3)
            if not SUBTYPE_IMAGE_RE.search(body) or b"stream" not in body:
                continue
            stream_pos = body.find(b"stream")
            data_start = stream_pos + len(b"stream")
            if body[data_start:data_start + 2] == b"\r\n":
                data_start += 2
            elif body[data_start:data_start + 1] in {b"\r", b"\n"}:
                data_start += 1
            endstream = body.find(b"endstream", data_start)
            if endstream == -1:
                continue
            raw = body[data_start:endstream].rstrip(b"\r\n")
            dictionary = body[:stream_pos]
            filters = self._extract_filters(dictionary)
            meta = self._extract_metadata(dictionary)
            try:
                decoded = decode_stream(raw, filters)
            except Exception:
                decoded = raw
            if self._looks_like_text_artifact(meta, filters, decoded):
                continue
            idx += 1
            images.append(ParsedImage(None, idx, raw, decoded, filters, meta, None))
        return images
