from __future__ import annotations

import base64
import re
import zlib


MAX_FLATE_DECOMPRESSED_SIZE_MB = 64


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


def _safe_zlib_decompress(data: bytes, max_size_mb: int = MAX_FLATE_DECOMPRESSED_SIZE_MB) -> bytes:
    """Realiza decompressão zlib com limite rígido de saída.

    A função usa ``zlib.decompressobj`` para processar o stream de forma
    incremental e interromper imediatamente quando a saída ultrapassaria o
    limite permitido. Isso evita esgotamento de memória em payloads maliciosos
    (ex.: zip bomb com ``FlateDecode``).

    Args:
        data: Payload comprimido no formato zlib/deflate.
        max_size_mb: Limite máximo, em MB, para o conteúdo descomprimido.

    Returns:
        Bytes descomprimidos, respeitando o limite configurado.

    Raises:
        ExtractionError: Se o stream estiver inválido, truncado ou se a saída
            descomprimida exceder o limite estabelecido.
    """
    if max_size_mb <= 0:
        raise ExtractionError("Limite de descompressão inválido: max_size_mb deve ser > 0.")

    max_output_size = max_size_mb * 1024 * 1024
    decompressor = zlib.decompressobj()
    output = bytearray()

    chunk_size = 64 * 1024
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset:offset + chunk_size]
        remaining = max_output_size - len(output)
        if remaining <= 0:
            raise ExtractionError(
                f"Stream FlateDecode excede limite de {max_size_mb} MB de saída descomprimida."
            )
        try:
            produced = decompressor.decompress(chunk, remaining)
        except zlib.error as exc:
            raise ExtractionError(f"Falha ao descomprimir stream FlateDecode: {exc}") from exc

        output.extend(produced)

        if decompressor.unconsumed_tail:
            raise ExtractionError(
                f"Stream FlateDecode excede limite de {max_size_mb} MB de saída descomprimida."
            )

    if not decompressor.eof:
        raise ExtractionError("Stream FlateDecode truncado ou inválido (EOF não encontrado).")

    remaining = max_output_size - len(output)
    try:
        flushed = decompressor.flush(remaining)
    except zlib.error as exc:
        raise ExtractionError(f"Falha ao finalizar descompressão FlateDecode: {exc}") from exc
    output.extend(flushed)

    if decompressor.unconsumed_tail:
        raise ExtractionError(
            f"Stream FlateDecode excede limite de {max_size_mb} MB de saída descomprimida."
        )

    if len(output) >= max_output_size and decompressor.unused_data == b"":
        # Defesa extra para streams que atingem o limite exatamente sem espaço
        # para validar output residual.
        if data and not decompressor.eof:
            raise ExtractionError(
                f"Stream FlateDecode excede limite de {max_size_mb} MB de saída descomprimida."
            )

    return bytes(output)


def decode_stream(data: bytes, filters: list[str]) -> bytes:
    decoded = data
    for f in filters:
        if f in {"FlateDecode", "Fl"}:
            decoded = _safe_zlib_decompress(decoded)
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
