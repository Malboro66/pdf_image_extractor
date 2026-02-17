#!/usr/bin/env python3
"""Extrai imagens incorporadas em arquivos PDF sem dependências externas."""

from __future__ import annotations

import argparse
import base64
import re
import zlib
from pathlib import Path

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.DOTALL)
FILTER_NAME_RE = re.compile(rb"/([A-Za-z0-9]+)")


class PDFImageExtractorError(RuntimeError):
    """Erro de extração de imagens."""


def _extract_filters(dictionary_bytes: bytes) -> list[str]:
    filter_pos = dictionary_bytes.find(b"/Filter")
    if filter_pos == -1:
        return []

    after = dictionary_bytes[filter_pos + len(b"/Filter") :]
    if b"[" in after[:8]:
        start = after.find(b"[")
        end = after.find(b"]", start)
        if start == -1 or end == -1:
            return []
        names = FILTER_NAME_RE.findall(after[start : end + 1])
        return [name.decode("ascii", errors="ignore") for name in names]

    match = FILTER_NAME_RE.search(after)
    if not match:
        return []
    return [match.group(1).decode("ascii", errors="ignore")]


def _run_length_decode(data: bytes) -> bytes:
    output = bytearray()
    i = 0
    length = len(data)

    while i < length:
        length_byte = data[i]
        i += 1

        if length_byte == 128:
            break

        if length_byte < 128:
            run_end = i + length_byte + 1
            output.extend(data[i:run_end])
            i = run_end
        else:
            run_len = 257 - length_byte
            if i >= length:
                break
            output.extend(data[i:i + 1] * run_len)
            i += 1

    return bytes(output)


def _decode_stream(data: bytes, filters: list[str]) -> bytes:
    decoded = data
    for filter_name in filters:
        if filter_name in {"FlateDecode", "Fl"}:
            decoded = zlib.decompress(decoded)
        elif filter_name in {"ASCIIHexDecode", "AHx"}:
            cleaned = re.sub(rb"\s+", b"", decoded).rstrip(b">")
            if len(cleaned) % 2 == 1:
                cleaned += b"0"
            decoded = bytes.fromhex(cleaned.decode("ascii"))
        elif filter_name in {"ASCII85Decode", "A85"}:
            decoded = base64.a85decode(decoded, adobe=True)
        elif filter_name in {"RunLengthDecode", "RL"}:
            decoded = _run_length_decode(decoded)
        elif filter_name in {"DCTDecode", "DCT", "JPXDecode", "JPX", "CCITTFaxDecode", "CCF"}:
            # Esses formatos já estão em formato de imagem pronto ou formato específico.
            continue
        else:
            raise PDFImageExtractorError(f"Filtro não suportado: {filter_name}")
    return decoded


def _pick_extension(filters: list[str]) -> str:
    if any(f in {"DCTDecode", "DCT"} for f in filters):
        return "jpg"
    if any(f in {"JPXDecode", "JPX"} for f in filters):
        return "jp2"
    if any(f in {"CCITTFaxDecode", "CCF"} for f in filters):
        return "tiff"
    if any(f in {"FlateDecode", "Fl", "ASCIIHexDecode", "AHx", "ASCII85Decode", "A85", "RunLengthDecode", "RL"} for f in filters):
        return "bin"
    return "img"


def extract_images(pdf_path: Path, output_dir: Path, prefix: str = "imagem") -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = pdf_path.read_bytes()
    count = 0

    for obj_match in OBJ_RE.finditer(pdf_bytes):
        body = obj_match.group(3)
        if b"/Subtype /Image" not in body or b"stream" not in body:
            continue

        stream_pos = body.find(b"stream")
        if stream_pos == -1:
            continue

        data_start = stream_pos + len(b"stream")
        if body[data_start:data_start + 2] == b"\r\n":
            data_start += 2
        elif body[data_start:data_start + 1] in {b"\r", b"\n"}:
            data_start += 1

        endstream_pos = body.find(b"endstream", data_start)
        if endstream_pos == -1:
            continue

        raw_data = body[data_start:endstream_pos].rstrip(b"\r\n")
        dictionary = body[:stream_pos]
        filters = _extract_filters(dictionary)

        try:
            decoded_data = _decode_stream(raw_data, filters)
        except Exception:
            decoded_data = raw_data

        extension = _pick_extension(filters)
        file_name = f"{prefix}_{count + 1:03d}.{extension}"
        (output_dir / file_name).write_bytes(decoded_data)
        count += 1

    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lê um PDF e extrai imagens incorporadas.")
    parser.add_argument("pdf", type=Path, help="Arquivo PDF de entrada.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("imagens_extraidas"),
        help="Diretório de saída (padrão: ./imagens_extraidas).",
    )
    parser.add_argument("--prefix", default="imagem", help="Prefixo dos arquivos de saída.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.pdf.exists() or not args.pdf.is_file():
        parser.error(f"Arquivo PDF inválido: {args.pdf}")

    total = extract_images(args.pdf, args.output_dir, args.prefix)
    print(f"Extração concluída: {total} imagem(ns) salva(s) em {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
