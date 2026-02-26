from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pdf_image_extractor.core.models import ExtractionConfig
from pdf_image_extractor.core.pipeline import run_extraction_job


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extrai imagens de PDFs com relatório e opções para produção.")
    p.add_argument("inputs", nargs="+", type=Path, help="Um ou mais arquivos PDF e/ou diretórios contendo PDFs.")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("imagens_extraidas"), help="Diretório de saída.")
    p.add_argument("--prefix", default="imagem", help="Prefixo dos arquivos gerados.")
    p.add_argument("--recursive", action="store_true", help="Busca PDFs recursivamente quando input for diretório.")
    p.add_argument("--fail-fast", action="store_true", help="Interrompe no primeiro erro.")
    p.add_argument("--continue-on-error", action="store_true", help="Continua mesmo que um arquivo/imagem falhe.")
    p.add_argument("--only-format", default="", help="Lista separada por vírgula (jpg,png,tiff,jp2,bin).")
    p.add_argument("--report", type=Path, default=Path("relatorio_extracao"), help="Caminho base do relatório (sem extensão).")
    p.add_argument("--report-format", default="json,csv", help="Formato(s) do relatório: json,csv")
    p.add_argument("--engine", choices=["auto", "pypdf", "fallback"], default="auto", help="Engine de parsing PDF.")
    p.add_argument("--quiet", action="store_true", help="Desativa progresso no terminal.")
    p.add_argument("--max-workers", type=int, default=4, help="Número máximo de workers para processamento concorrente.")
    p.add_argument("--no-isolation", action="store_true", help="Desativa isolamento por processo (não recomendado para entradas não confiáveis).")
    p.add_argument("--pdf-timeout", type=int, default=60, help="Timeout por PDF em segundos (isolamento habilitado).")
    p.add_argument("--worker-memory-mb", type=int, default=1024, help="Limite de memória por worker em MB (Linux/resource).")
    p.add_argument("--worker-cpu-seconds", type=int, default=120, help="Limite de CPU por worker em segundos (Linux/resource).")
    p.add_argument("--max-pdf-size-mb", type=int, default=200, help="Tamanho máximo permitido por PDF em MB (0 desativa).")
    p.add_argument("--max-pages-per-pdf", type=int, default=500, help="Máximo de páginas processadas por PDF (0 desativa).")
    p.add_argument("--max-images-per-pdf", type=int, default=2000, help="Máximo de imagens processadas por PDF (0 desativa).")
    p.add_argument("--max-output-mb-per-pdf", type=int, default=256, help="Máximo de bytes de saída gerados por PDF em MB (0 desativa).")
    p.add_argument("--telemetry-log", type=Path, default=None, help="Arquivo JSONL para logs estruturados do job.")
    p.add_argument("--metrics-output", type=Path, default=None, help="Arquivo JSON com métricas agregadas do job.")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.fail_fast and args.continue_on_error:
        parser.error("Use apenas um entre --fail-fast e --continue-on-error.")

    cfg = ExtractionConfig(
        input_paths=args.inputs,
        output_dir=args.output_dir,
        prefix=args.prefix,
        recursive=args.recursive,
        fail_fast=args.fail_fast,
        continue_on_error=args.continue_on_error,
        only_format={x.strip().lower() for x in args.only_format.split(",") if x.strip()} or None,
        report=args.report,
        report_formats={x.strip().lower() for x in args.report_format.split(",") if x.strip()},
        engine=args.engine,
        quiet=args.quiet,
        max_workers=args.max_workers,
        isolate_pdf_processing=not args.no_isolation,
        pdf_timeout_seconds=max(1, args.pdf_timeout),
        worker_memory_limit_mb=(args.worker_memory_mb if args.worker_memory_mb > 0 else None),
        worker_cpu_time_limit_seconds=(args.worker_cpu_seconds if args.worker_cpu_seconds > 0 else None),
        max_pdf_size_mb=(args.max_pdf_size_mb if args.max_pdf_size_mb > 0 else None),
        max_pages_per_pdf=(args.max_pages_per_pdf if args.max_pages_per_pdf > 0 else None),
        max_images_per_pdf=(args.max_images_per_pdf if args.max_images_per_pdf > 0 else None),
        max_output_bytes_per_pdf_mb=(args.max_output_mb_per_pdf if args.max_output_mb_per_pdf > 0 else None),
        telemetry_log_path=args.telemetry_log,
        metrics_output_path=args.metrics_output,
    )
    records, exit_code = run_extraction_job(cfg)
    if exit_code == 2:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 2
    total_errors = sum(1 for r in records if r.status == "error")
    extracted = sum(1 for r in records if r.status == "ok")
    skipped = sum(1 for r in records if r.status.startswith("skipped"))
    print(f"Concluído. extraídas={extracted}, ignoradas={skipped}, erros={total_errors}")
    return exit_code
