from __future__ import annotations

import math
import re
from pathlib import Path

from pdf_image_extractor.adapters.engines.base import ParsedImage
from pdf_image_extractor.core.decoders import decode_stream

OBJ_HEADER_RE = re.compile(rb"\d+\s+\d+\s+obj")
FILTER_NAME_RE = re.compile(rb"/([A-Za-z0-9]+)")
NUMBER_RE = re.compile(rb"/(Width|Height|BitsPerComponent)\s+(\d+)")
COLORSPACE_RE = re.compile(rb"/ColorSpace\s*/([A-Za-z0-9]+)")
DECODE_RE = re.compile(rb"/Decode\s*\[(.*?)\]", re.DOTALL)
FLOAT_RE = re.compile(rb"-?\d+(?:\.\d+)?")
SUBTYPE_IMAGE_RE = re.compile(rb"/Subtype\s*/Image")


class FallbackEngine:
    name = "fallback"
    CHUNK_SIZE = 1024 * 1024
    MAX_OBJECT_BYTES = 64 * 1024 * 1024

    @classmethod
    def _find_object_header(cls, buf: bytearray, start: int) -> tuple[int, int] | None:
        pos = start
        while True:
            marker = buf.find(b" obj", pos)
            if marker == -1:
                return None
            line_start = max(buf.rfind(b"\n", 0, marker), buf.rfind(b"\r", 0, marker)) + 1
            candidate = bytes(buf[line_start:marker + len(b" obj")]).strip()
            if OBJ_HEADER_RE.fullmatch(candidate):
                return line_start, marker + len(b" obj")
            pos = marker + len(b" obj")

    @classmethod
    def _iter_object_bodies(cls, pdf_path: Path):
        with pdf_path.open("rb") as f:
            buf = bytearray()
            cursor = 0
            eof = False
            while True:
                if not eof and len(buf) - cursor < cls.CHUNK_SIZE:
                    chunk = f.read(cls.CHUNK_SIZE)
                    if chunk:
                        buf.extend(chunk)
                    else:
                        eof = True

                progressed = False
                while True:
                    header = cls._find_object_header(buf, cursor)
                    if header is None:
                        break

                    obj_start, body_start = header
                    endobj = buf.find(b"endobj", body_start)
                    if endobj == -1:
                        if len(buf) - obj_start > cls.MAX_OBJECT_BYTES:
                            cursor = body_start
                            progressed = True
                            continue
                        if obj_start > 0:
                            del buf[:obj_start]
                            cursor = 0
                            progressed = True
                        break

                    yield bytes(buf[body_start:endobj])
                    cursor = endobj + len(b"endobj")
                    progressed = True

                if cursor > 0 and (cursor > cls.CHUNK_SIZE or (eof and cursor == len(buf))):
                    del buf[:cursor]
                    cursor = 0

                if eof:
                    if not progressed:
                        break
                    if not buf:
                        break

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
        images: list[ParsedImage] = []
        idx = 0
        for body in self._iter_object_bodies(pdf_path):
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
