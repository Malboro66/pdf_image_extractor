from __future__ import annotations

import base64
import re
import zlib


class ExtractionError(RuntimeError):
    pass


def run_length_decode(data: bytes) -> bytes:
    output = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        i += 1
        if b == 128:
            break
        if b < 128:
            run_end = i + b + 1
            output.extend(data[i:run_end])
            i = run_end
        else:
            run_len = 257 - b
            if i >= len(data):
                break
            output.extend(data[i:i + 1] * run_len)
            i += 1
    return bytes(output)


def decode_stream(data: bytes, filters: list[str]) -> bytes:
    decoded = data
    for f in filters:
        if f in {"FlateDecode", "Fl"}:
            decoded = zlib.decompress(decoded)
        elif f in {"ASCIIHexDecode", "AHx"}:
            cleaned = re.sub(rb"\s+", b"", decoded).rstrip(b">")
            if len(cleaned) % 2 == 1:
                cleaned += b"0"
            decoded = bytes.fromhex(cleaned.decode("ascii"))
        elif f in {"ASCII85Decode", "A85"}:
            decoded = base64.a85decode(decoded, adobe=True)
        elif f in {"RunLengthDecode", "RL"}:
            decoded = run_length_decode(decoded)
        elif f in {"DCTDecode", "DCT", "JPXDecode", "JPX", "CCITTFaxDecode", "CCF"}:
            continue
        else:
            raise ExtractionError(f"Filtro não suportado: {f}")
    return decoded
