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
