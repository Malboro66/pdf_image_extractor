#!/usr/bin/env python3
"""Extrator de imagens de PDF com engine robusta opcional e relatório detalhado."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.DOTALL)
FILTER_NAME_RE = re.compile(rb"/([A-Za-z0-9]+)")
NUMBER_RE = re.compile(rb"/(Width|Height|BitsPerComponent)\s+(\d+)")
COLORSPACE_RE = re.compile(rb"/ColorSpace\s*/([A-Za-z0-9]+)")
DECODE_RE = re.compile(rb"/Decode\s*\[(.*?)\]", re.DOTALL)
FLOAT_RE = re.compile(rb"-?\d+(?:\.\d+)?")
SUBTYPE_IMAGE_RE = re.compile(rb"/Subtype\s*/Image")
LENGTH_RE = re.compile(rb"/Length\s+(\d+)")


@dataclass
class ExtractionRecord:
    input_file: str
    page: int | None
    image_index: int
    output_file: str | None
    filters: str
    width: int | None
    height: int | None
    bits_per_component: int | None
    color_space: str | None
    source_bytes: int
    output_bytes: int
    status: str
    error: str | None


class ExtractionError(RuntimeError):
    """Erro de extração."""


def _run_length_decode(data: bytes) -> bytes:
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


def _extract_metadata(dictionary_bytes: bytes) -> dict[str, Any]:
    values: dict[str, Any] = {
        "Width": None,
        "Height": None,
        "BitsPerComponent": None,
        "ColorSpace": None,
        "ImageMask": False,
        "Decode": None,
    }
    for key, raw in NUMBER_RE.findall(dictionary_bytes):
        values[key.decode()] = int(raw)
    cs = COLORSPACE_RE.search(dictionary_bytes)
    if cs:
        values["ColorSpace"] = cs.group(1).decode("ascii", errors="ignore")
    values["ImageMask"] = bool(re.search(rb"/ImageMask\s+(true|1)", dictionary_bytes))

    decode_match = DECODE_RE.search(dictionary_bytes)
    if decode_match:
        vals = [float(v.decode("ascii")) for v in FLOAT_RE.findall(decode_match.group(1))]
        values["Decode"] = vals if vals else None
    return values


def _decode_stream(data: bytes, filters: list[str]) -> bytes:
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
            decoded = _run_length_decode(decoded)
        elif f in {"DCTDecode", "DCT", "JPXDecode", "JPX", "CCITTFaxDecode", "CCF"}:
            continue
        else:
            raise ExtractionError(f"Filtro não suportado: {f}")
    return decoded


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    body = chunk_type + payload
    return len(payload).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")


def _channels_from_meta(meta: dict[str, Any]) -> int | None:
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


def _apply_decode_transform(decoded: bytes, meta: dict[str, Any]) -> bytes:
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

    channels = _channels_from_meta(meta)
    if not channels or channels <= 0:
        return decoded

    data = bytearray(decoded)
    for idx in range(len(data)):
        channel = idx % channels
        if channel < len(invert_flags) and invert_flags[channel]:
            data[idx] = 255 - data[idx]
    return bytes(data)


def _raw_to_png(data: bytes, width: int, height: int, color_space: str | None, bits: int | None) -> bytes | None:
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
    ihdr = _png_chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, color_type, 0, 0, 0]))
    idat = _png_chunk(b"IDAT", compressed)
    iend = _png_chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def _raw_to_tiff_gray(data: bytes, width: int, height: int, bits: int | None) -> bytes | None:
    if not width or not height or bits != 8 or len(data) < width * height:
        return None
    image = data[: width * height]

    def ent(tag: int, typ: int, count: int, value: int) -> bytes:
        return tag.to_bytes(2, "little") + typ.to_bytes(2, "little") + count.to_bytes(4, "little") + value.to_bytes(4, "little")

    header = b"II" + (42).to_bytes(2, "little") + (8).to_bytes(4, "little")
    image_offset = 8 + 2 + (9 * 12) + 4
    entries = [
        ent(256, 4, 1, width),
        ent(257, 4, 1, height),
        ent(258, 3, 1, 8),
        ent(259, 3, 1, 1),
        ent(262, 3, 1, 1),
        ent(273, 4, 1, image_offset),
        ent(277, 3, 1, 1),
        ent(278, 4, 1, height),
        ent(279, 4, 1, len(image)),
    ]
    ifd = len(entries).to_bytes(2, "little") + b"".join(entries) + (0).to_bytes(4, "little")
    return header + ifd + image


def _choose_output(decoded: bytes, filters: list[str], meta: dict[str, Any]) -> tuple[bytes, str]:
    if any(f in {"DCTDecode", "DCT"} for f in filters):
        return decoded, "jpg"
    if any(f in {"JPXDecode", "JPX"} for f in filters):
        return decoded, "jp2"
    if any(f in {"CCITTFaxDecode", "CCF"} for f in filters):
        return decoded, "tiff"

    normalized = _apply_decode_transform(decoded, meta)
    png = _raw_to_png(normalized, meta["Width"], meta["Height"], meta["ColorSpace"], meta["BitsPerComponent"])
    if png is not None:
        return png, "png"
    tiff = _raw_to_tiff_gray(normalized, meta["Width"], meta["Height"], meta["BitsPerComponent"])
    if tiff is not None:
        return tiff, "tiff"
    return normalized, "bin"


def _looks_like_text_artifact(meta: dict[str, Any], filters: list[str]) -> bool:
    if meta.get("ImageMask"):
        return True
    bits = meta.get("BitsPerComponent")
    width = meta.get("Width") or 0
    height = meta.get("Height") or 0
    area = width * height

    direct_image = any(f in {"DCTDecode", "DCT", "JPXDecode", "JPX", "CCITTFaxDecode", "CCF"} for f in filters)
    if direct_image:
        return False

    return bits == 1 and area <= 4096


def _extract_with_pypdf(pdf_path: Path) -> list[dict[str, Any]]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(pdf_path))
    images = []
    for page_idx, page in enumerate(reader.pages, start=1):
        for idx, image in enumerate(page.images, start=1):
            name_l = image.name.lower()
            if "smask" in name_l or "mask" in name_l:
                continue
            ext = image.name.split(".")[-1].lower() if "." in image.name else "bin"
            images.append({
                "page": page_idx,
                "index": idx,
                "raw": image.data,
                "decoded": image.data,
                "filters": [f"direct:{ext}"],
                "meta": {"Width": None, "Height": None, "BitsPerComponent": None, "ColorSpace": None, "ImageMask": False, "Decode": None},
                "preferred_ext": ext,
            })
    return images


def _extract_with_fallback(pdf_path: Path) -> list[dict[str, Any]]:
    pdf_bytes = pdf_path.read_bytes()
    images = []
    image_counter = 0

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

        dictionary = body[:stream_pos]
        length_match = LENGTH_RE.search(dictionary)
        if length_match:
            stream_len = int(length_match.group(1))
            data_end = data_start + stream_len
            if data_end > len(body):
                continue
            raw = body[data_start:data_end]
        else:
            endstream = body.find(b"endstream", data_start)
            if endstream == -1:
                continue
            raw = body[data_start:endstream].rstrip(b"\r\n")

        filters = _extract_filters(dictionary)
        meta = _extract_metadata(dictionary)

        if _looks_like_text_artifact(meta, filters):
            continue

        try:
            decoded = _decode_stream(raw, filters)
        except Exception:
            decoded = raw

        image_counter += 1
        images.append({
            "page": None,
            "index": image_counter,
            "raw": raw,
            "decoded": decoded,
            "filters": filters,
            "meta": meta,
            "preferred_ext": None,
        })
    return images


def extract_from_pdf(
    pdf_path: Path,
    output_dir: Path,
    prefix: str,
    only_format: set[str] | None,
    engine: str,
    continue_on_error: bool,
) -> tuple[list[ExtractionRecord], int]:
    records: list[ExtractionRecord] = []
    errors = 0

    try:
        use_pypdf = engine == "pypdf"
        if engine == "auto":
            try:
                import pypdf  # noqa: F401
                use_pypdf = True
            except Exception:
                use_pypdf = False

        images = _extract_with_pypdf(pdf_path) if use_pypdf else _extract_with_fallback(pdf_path)
    except Exception as exc:
        rec = ExtractionRecord(str(pdf_path), None, 0, None, "", None, None, None, None, 0, 0, "error", str(exc))
        return [rec], 1

    output_dir.mkdir(parents=True, exist_ok=True)

    for item in images:
        try:
            decoded, ext = (item["decoded"], item["preferred_ext"]) if item["preferred_ext"] else _choose_output(item["decoded"], item["filters"], item["meta"])
            if only_format and ext.lower() not in only_format:
                status = "skipped_format"
                out_file = None
                out_size = 0
            else:
                base_name = f"{prefix}_{item['index']:04d}"
                target = output_dir / f"{base_name}.{ext}"
                suffix = 1
                while target.exists():
                    target = output_dir / f"{base_name}_{suffix:03d}.{ext}"
                    suffix += 1
                target.write_bytes(decoded)
                status = "ok"
                out_file = str(target)
                out_size = len(decoded)

            records.append(
                ExtractionRecord(
                    input_file=str(pdf_path),
                    page=item["page"],
                    image_index=item["index"],
                    output_file=out_file,
                    filters="|".join(item["filters"]),
                    width=item["meta"]["Width"],
                    height=item["meta"]["Height"],
                    bits_per_component=item["meta"]["BitsPerComponent"],
                    color_space=item["meta"]["ColorSpace"],
                    source_bytes=len(item["raw"]),
                    output_bytes=out_size,
                    status=status,
                    error=None,
                )
            )
        except Exception as exc:
            errors += 1
            records.append(
                ExtractionRecord(
                    input_file=str(pdf_path),
                    page=item["page"],
                    image_index=item["index"],
                    output_file=None,
                    filters="|".join(item["filters"]),
                    width=item["meta"]["Width"],
                    height=item["meta"]["Height"],
                    bits_per_component=item["meta"]["BitsPerComponent"],
                    color_space=item["meta"]["ColorSpace"],
                    source_bytes=len(item["raw"]),
                    output_bytes=0,
                    status="error",
                    error=str(exc),
                )
            )
            if not continue_on_error:
                break
    return records, errors


def _collect_pdfs(path: Path, recursive: bool) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        pattern = "**/*.pdf" if recursive else "*.pdf"
        return sorted(path.glob(pattern))
    return []


def _write_report(records: list[ExtractionRecord], report_base: Path, formats: set[str]) -> None:
    if "json" in formats:
        report_base.with_suffix(".json").write_text(json.dumps([asdict(r) for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
    if "csv" in formats:
        fields = list(ExtractionRecord.__dataclass_fields__.keys())
        with report_base.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in records:
                writer.writerow(asdict(r))


def run_extraction_job(
    input_path: Path,
    output_dir: Path,
    prefix: str = "imagem",
    recursive: bool = False,
    fail_fast: bool = False,
    continue_on_error: bool = True,
    only_format: set[str] | None = None,
    report: Path = Path("relatorio_extracao"),
    report_formats: set[str] = frozenset({"json", "csv"}),
    engine: str = "auto",
    quiet: bool = False,
) -> tuple[list[ExtractionRecord], int]:
    pdfs = _collect_pdfs(input_path, recursive)
    if not pdfs:
        return [], 2

    effective_continue = continue_on_error or not fail_fast
    all_records: list[ExtractionRecord] = []
    total_errors = 0

    for idx, pdf in enumerate(pdfs, start=1):
        if not quiet:
            print(f"[{idx}/{len(pdfs)}] Processando: {pdf}")
        records, errors = extract_from_pdf(
            pdf,
            output_dir,
            prefix,
            only_format,
            engine,
            effective_continue,
        )
        all_records.extend(records)
        total_errors += errors
        if errors and fail_fast:
            break

    _write_report(all_records, report, report_formats)
    return all_records, (0 if total_errors == 0 else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extrai imagens de PDFs com relatório e opções para produção.")
    parser.add_argument("input", type=Path, help="Arquivo PDF ou diretório contendo PDFs.")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("imagens_extraidas"), help="Diretório de saída.")
    parser.add_argument("--prefix", default="imagem", help="Prefixo dos arquivos gerados.")
    parser.add_argument("--recursive", action="store_true", help="Busca PDFs recursivamente quando input for diretório.")
    parser.add_argument("--fail-fast", action="store_true", help="Interrompe no primeiro erro.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continua mesmo que um arquivo/imagem falhe.")
    parser.add_argument("--only-format", default="", help="Lista separada por vírgula (jpg,png,tiff,jp2,bin).")
    parser.add_argument("--report", type=Path, default=Path("relatorio_extracao"), help="Caminho base do relatório (sem extensão).")
    parser.add_argument("--report-format", default="json,csv", help="Formato(s) do relatório: json,csv")
    parser.add_argument("--engine", choices=["auto", "pypdf", "fallback"], default="auto", help="Engine de parsing PDF.")
    parser.add_argument("--quiet", action="store_true", help="Desativa progresso no terminal.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.fail_fast and args.continue_on_error:
        parser.error("Use apenas um entre --fail-fast e --continue-on-error.")

    only_format = {x.strip().lower() for x in args.only_format.split(",") if x.strip()} or None
    report_formats = {x.strip().lower() for x in args.report_format.split(",") if x.strip()}

    records, exit_code = run_extraction_job(
        input_path=args.input,
        output_dir=args.output_dir,
        prefix=args.prefix,
        recursive=args.recursive,
        fail_fast=args.fail_fast,
        continue_on_error=args.continue_on_error,
        only_format=only_format,
        report=args.report,
        report_formats=report_formats,
        engine=args.engine,
        quiet=args.quiet,
    )

    if exit_code == 2:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 2

    total_errors = sum(1 for r in records if r.status == "error")
    extracted = sum(1 for r in records if r.status == "ok")
    skipped = sum(1 for r in records if r.status.startswith("skipped"))
    print(f"Concluído. extraídas={extracted}, ignoradas={skipped}, erros={total_errors}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
