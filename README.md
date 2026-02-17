# PDF Image Extractor

Aplicação de linha de comando para ler um arquivo PDF e extrair imagens incorporadas.

## Requisitos

- Python 3.9+

## Uso

```bash
python extract_images.py caminho/arquivo.pdf
```

Opções úteis:

- `-o, --output-dir`: diretório de saída das imagens (padrão: `imagens_extraidas`)
- `--prefix`: prefixo dos nomes dos arquivos de imagem gerados

Exemplo:

```bash
python extract_images.py documento.pdf -o saida_imagens --prefix pagina
```

Ao final, o script informa quantas imagens foram extraídas.

> Observação: o utilitário extrai imagens com mais confiabilidade quando o PDF contém imagens incorporadas diretamente no conteúdo (XObject `/Subtype /Image`).
