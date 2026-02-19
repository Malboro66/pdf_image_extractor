from __future__ import annotations

import io
import zlib
from typing import Any


def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    body = chunk_type + payload
    return len(payload).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")


def channels_from_meta(meta: dict[str, Any]) -> int | None:
    color = meta.get("ColorSpace") or "DeviceRGB"
    if color in {"DeviceGray", "G"}:
        return 1
    if color in {"DeviceRGB", "RGB"}:
        return 3
    if color in {"DeviceCMYK", "CMYK"}:
        return 4
    decode = meta.get("Decode")
    if isinstance(decode, list) and len(decode) % 2 == 0 and decode:
        return len(decode) // 2
    return None


def apply_decode_transform(decoded: bytes, meta: dict[str, Any]) -> bytes:
    decode = meta.get("Decode")
    bits = meta.get("BitsPerComponent")
    if not decode or bits is None:
        return decoded

    invert_flags: list[bool] = []
    for i in range(0, len(decode), 2):
        low = decode[i]
        high = decode[i + 1] if i + 1 < len(decode) else low
        invert_flags.append(high < low)

    if not any(invert_flags):
        return decoded
    if bits == 1:
        return bytes((~b) & 0xFF for b in decoded)
    if bits != 8:
        return decoded

    channels = channels_from_meta(meta)
    if not channels:
        return decoded

    data = bytearray(decoded)
    for idx in range(len(data)):
        c = idx % channels
        if c < len(invert_flags) and invert_flags[c]:
            data[idx] = 255 - data[idx]
    return bytes(data)


def has_decode_inversion(meta: dict[str, Any]) -> bool:
    decode = meta.get("Decode")
    if not isinstance(decode, list):
        return False
    return any((decode[i + 1] if i + 1 < len(decode) else decode[i]) < decode[i] for i in range(0, len(decode), 2))


def invert_direct_image_bytes(data: bytes) -> bytes | None:
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.mode == "1":
                img = img.convert("L")
            elif img.mode in {"P", "CMYK", "RGBA", "LA"}:
                img = img.convert("RGB")
            inv = ImageOps.invert(img)
            out = io.BytesIO()
            inv.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return None


def raw_to_png(data: bytes, width: int, height: int, color_space: str | None, bits: int | None) -> bytes | None:
    if not width or not height or bits != 8:
        return None
    color = color_space or "DeviceRGB"
    if color in {"DeviceGray", "G"}:
        channels, color_type = 1, 0
    elif color in {"DeviceRGB", "RGB"}:
        channels, color_type = 3, 2
    else:
        return None
    row = width * channels
    expected = row * height
    if len(data) < expected:
        return None
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        start = y * row
        raw.extend(data[start:start + row])
    signature = b"\x89PNG\r\n\x1a\n"
    compressed = zlib.compress(bytes(raw))
    ihdr = png_chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, color_type, 0, 0, 0]))
    return signature + ihdr + png_chunk(b"IDAT", compressed) + png_chunk(b"IEND", b"")


def raw_to_tiff_gray(data: bytes, width: int, height: int, bits: int | None) -> bytes | None:
    if not width or not height or bits != 8 or len(data) < width * height:
        return None
    image = data[: width * height]

    def ent(tag: int, typ: int, count: int, value: int) -> bytes:
        return tag.to_bytes(2, "little") + typ.to_bytes(2, "little") + count.to_bytes(4, "little") + value.to_bytes(4, "little")

    header = b"II" + (42).to_bytes(2, "little") + (8).to_bytes(4, "little")
    image_offset = 8 + 2 + (9 * 12) + 4
    entries = [
        ent(256, 4, 1, width), ent(257, 4, 1, height), ent(258, 3, 1, 8), ent(259, 3, 1, 1), ent(262, 3, 1, 1),
        ent(273, 4, 1, image_offset), ent(277, 3, 1, 1), ent(278, 4, 1, height), ent(279, 4, 1, len(image)),
    ]
    ifd = len(entries).to_bytes(2, "little") + b"".join(entries) + (0).to_bytes(4, "little")
    return header + ifd + image


def choose_output(decoded: bytes, filters: list[str], meta: dict[str, Any]) -> tuple[bytes, str, str]:
    direct_dct = any(f in {"DCTDecode", "DCT"} for f in filters)
    direct_jpx = any(f in {"JPXDecode", "JPX"} for f in filters)
    if direct_dct or direct_jpx:
        if has_decode_inversion(meta):
            corrected = invert_direct_image_bytes(decoded)
            if corrected is not None:
                return corrected, "png", "direct_inversion_corrected"
            return decoded, ("jpg" if direct_dct else "jp2"), "direct_inversion_detected_unhandled"
        return (decoded, "jpg", "none") if direct_dct else (decoded, "jp2", "none")

    if any(f in {"CCITTFaxDecode", "CCF"} for f in filters):
        return decoded, "tiff", "none"

    normalized = apply_decode_transform(decoded, meta)
    correction = "raw_decode_inversion_corrected" if normalized != decoded else "none"
    png = raw_to_png(normalized, meta["Width"], meta["Height"], meta["ColorSpace"], meta["BitsPerComponent"])
    if png is not None:
        return png, "png", correction
    tiff = raw_to_tiff_gray(normalized, meta["Width"], meta["Height"], meta["BitsPerComponent"])
    if tiff is not None:
        return tiff, "tiff", correction
    return normalized, "bin", correction
