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

- (Opcional) `Pillow` para corrigir melhor casos de imagens diretas (JPEG/JP2) invertidas por `/Decode`:

```bash
pip install pillow
```


## Arquitetura em camadas

A aplicação foi organizada em camadas/domínios para facilitar evolução:

- `pdf_image_extractor/core`: regras de domínio, pipeline por estágios e modelos (`ExtractionConfig`, `ExtractionRecord`).
- `pdf_image_extractor/adapters`: engines de extração (fallback, pypdf) com contrato comum.
- `pdf_image_extractor/interfaces`: interface CLI e integração com entrypoints.

Pipeline aplicado: `discover -> parse -> decode -> normalize -> persist -> report`.

## Interface gráfica (GUI)

Execute:

```bash
python extract_images_gui.py
```

Recursos da GUI:

- Fila visual de múltiplos arquivos/pastas com reordenação (arrastar na lista, mover ↑/↓ e remover).
- Tabela de resultados em tempo real com colunas de status por imagem.
- Preview rápido (PNG/GIF no Tk padrão) ao selecionar um item da tabela.
- Barra de progresso com percentual, contador e ETA.
- Configurações persistidas automaticamente (`~/.pdf_image_extractor_gui.json`).
- Botão para abrir relatório e mensagens de erro acionáveis (ex.: sugestão de Pillow/pypdf).

## Uso via CLI

```bash
python extract_images.py arquivo1.pdf arquivo2.pdf pasta_com_pdfs
```

## Principais recursos (CLI)

- Entrada com **um ou múltiplos arquivos/diretórios**.
- Engine de parsing configurável: `--engine auto|pypdf|fallback`.
- Reconstrução de imagens raw para `PNG/TIFF` quando possível.
- Correção de negativo por `/Decode` com status explícito no relatório (`correction_status`), inclusive tentativa para imagens diretas quando Pillow está disponível e fallback determinístico quando não estiver.
- Filtro de artefatos de texto/máscara (`/ImageMask`) para reduzir falsos positivos.
- Relatório detalhado em JSON/CSV com status por imagem.
- Operação em lote com diretório + `--recursive`.
- UX para produção: `--fail-fast`, `--continue-on-error`, `--only-format`.

## Opções úteis

- `-o, --output-dir`: diretório de saída
- `--prefix`: prefixo dos arquivos
- `--recursive`: busca PDFs recursivamente em diretórios
- `--fail-fast`: para no primeiro erro
- `--continue-on-error`: continua em caso de erro
- `--only-format`: filtra formatos de saída (ex.: `jpg,png`)
- `--report`: caminho base do relatório
- `--report-format`: `json`, `csv` ou ambos (ex.: `json,csv`)
- `--engine`: seleciona engine de parsing
- `--quiet`: desativa logs de progresso
- `--max-workers`: paralelismo por arquivo em lotes

## Exemplos CLI

```bash
# Dois arquivos e uma pasta
python extract_images.py a.pdf b.pdf ./pdfs -o saida_imagens

# Diretório inteiro (recursivo), apenas PNG e JPEG
python extract_images.py ./pdfs --recursive --only-format jpg,png -o saida

# Modo robusto com pypdf, parando no primeiro erro
python extract_images.py documento.pdf --engine pypdf --fail-fast
```
