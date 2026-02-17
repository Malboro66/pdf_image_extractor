# PDF Image Extractor

Aplicação para extrair imagens de arquivos PDF com duas opções de uso:

- **CLI** (linha de comando) para automação e pipelines.
- **GUI** (interface gráfica moderna e minimalista) para uso desktop.

## Requisitos

- Python 3.9+
- (Opcional) `pypdf` para engine robusta:

```bash
pip install pypdf
```

## Interface gráfica (GUI)

Execute:

```bash
python extract_images_gui.py
```

Recursos da GUI:

- Seleção de PDF único ou diretório de PDFs.
- Configuração de pasta de saída, prefixo, engine e tamanho mínimo.
- Opções de processamento recursivo e continuidade em erro.
- Feedback de status em tempo real.

## Uso via CLI

```bash
python extract_images.py caminho/arquivo.pdf
```

## Principais recursos (CLI)

- Engine de parsing configurável: `--engine auto|pypdf|fallback`.
- Reconstrução de imagens raw para `PNG/TIFF` quando possível.
- Relatório detalhado em JSON/CSV com status por imagem.
- Operação em lote com diretório + `--recursive`.
- UX para produção: `--fail-fast`, `--continue-on-error`, `--min-size`, `--only-format`.

## Opções úteis

- `-o, --output-dir`: diretório de saída
- `--prefix`: prefixo dos arquivos
- `--recursive`: busca PDFs recursivamente se a entrada for diretório
- `--fail-fast`: para no primeiro erro
- `--continue-on-error`: continua em caso de erro
- `--min-size`: ignora saídas menores que N bytes
- `--only-format`: filtra formatos de saída (ex.: `jpg,png`)
- `--report`: caminho base do relatório
- `--report-format`: `json`, `csv` ou ambos (ex.: `json,csv`)
- `--engine`: seleciona engine de parsing
- `--quiet`: desativa logs de progresso

## Exemplos CLI

```bash
# Arquivo único
python extract_images.py documento.pdf -o saida_imagens

# Diretório inteiro (recursivo), apenas PNG e JPEG
python extract_images.py ./pdfs --recursive --only-format jpg,png -o saida

# Modo robusto com pypdf, parando no primeiro erro
python extract_images.py documento.pdf --engine pypdf --fail-fast
```
