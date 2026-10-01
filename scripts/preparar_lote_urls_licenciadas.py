"""Prepara um catálogo isolado para uma entrega grande de URLs licenciadas.

O arquivo de entrada pode ser TXT (uma URL por linha) ou CSV com a coluna
``url``. A saída contém o catálogo e a lista de autorizações lado a lado,
prontos para o ``processar_lote.py`` sem misturar o lote novo ao catálogo
operacional atual.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from observatorio_vagas.crawling.catalog import (
    ErroCatalogoFontes,
    _normalizar_url_simples,
    carregar_alvos_csv_tolerante,
)

NOME_CATALOGO = "catalogo_fontes.csv"
NOME_AUTORIZACOES = "fontes_autorizadas.csv"
NOME_RELATORIO = "relatorio_importacao.json"


@dataclass(frozen=True, slots=True)
class RejeicaoUrl:
    """Registra uma linha que não entrou no catálogo pronto."""

    linha: int
    url: str
    motivo: str


def criar_parser() -> argparse.ArgumentParser:
    """Configura o comando de preparação do lote."""

    parser = argparse.ArgumentParser(
        description=(
            "Valida URLs licenciadas e cria um catálogo isolado pronto para coleta em lote."
        )
    )
    parser.add_argument(
        "--entrada",
        type=Path,
        required=True,
        help="TXT com uma URL por linha ou CSV com a coluna url",
    )
    parser.add_argument(
        "--diretorio-saida",
        type=Path,
        default=Path("outputs/lotes_urls_licenciadas"),
        help=(
            "pasta para catalogo_fontes.csv, fontes_autorizadas.csv e relatório "
            "(padrão: outputs/lotes_urls_licenciadas)"
        ),
    )
    parser.add_argument(
        "--substituir",
        action="store_true",
        help="permite substituir somente os três arquivos gerados no diretório de saída",
    )
    return parser


def _ler_urls(entrada: Path) -> tuple[tuple[int, str], ...]:
    """Lê TXT ou CSV simples preservando o número original de cada linha."""

    try:
        linhas = entrada.read_text(encoding="utf-8-sig").splitlines()
    except OSError as erro:
        raise ValueError(f"não foi possível ler a entrada: {entrada}") from erro

    if entrada.suffix.casefold() == ".csv":
        leitor = csv.reader(linhas)
        resultado: list[tuple[int, str]] = []
        for numero, linha in enumerate(leitor, start=1):
            if not linha:
                continue
            valor = linha[0].strip()
            if numero == 1 and valor.casefold() == "url":
                continue
            resultado.append((numero, valor))
        return tuple(resultado)

    return tuple(
        (numero, linha.strip())
        for numero, linha in enumerate(linhas, start=1)
        if linha.strip()
    )


def _normalizar_e_deduplicar(
    linhas: tuple[tuple[int, str], ...],
) -> tuple[tuple[str, ...], tuple[RejeicaoUrl, ...]]:
    """Remove repetidas e isola URLs malformadas antes de gerar arquivos."""

    aceitas: list[str] = []
    rejeitadas: list[RejeicaoUrl] = []
    vistas: set[str] = set()

    for numero, valor in linhas:
        try:
            url = _normalizar_url_simples(valor)
        except ValueError as erro:
            rejeitadas.append(RejeicaoUrl(numero, valor, str(erro)))
            continue

        if url in vistas:
            rejeitadas.append(RejeicaoUrl(numero, valor, "URL duplicada no arquivo de entrada"))
            continue

        vistas.add(url)
        aceitas.append(url)

    return tuple(aceitas), tuple(rejeitadas)


def _escrever_urls(caminho: Path, urls: tuple[str, ...]) -> None:
    """Escreve um CSV simples usando UTF-8 e uma URL por linha."""

    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(("url",))
        escritor.writerows((url,) for url in urls)


def preparar_lote(
    *,
    entrada: Path,
    diretorio_saida: Path,
    substituir: bool,
) -> tuple[int, int, Path]:
    """Gera o lote pronto e retorna total de aceitas, rejeitadas e relatório."""

    linhas = _ler_urls(entrada)
    urls, rejeitadas_iniciais = _normalizar_e_deduplicar(linhas)
    diretorio_saida.mkdir(parents=True, exist_ok=True)

    catalogo = diretorio_saida / NOME_CATALOGO
    autorizacoes = diretorio_saida / NOME_AUTORIZACOES
    relatorio = diretorio_saida / NOME_RELATORIO
    destinos = (catalogo, autorizacoes, relatorio)

    if not substituir and any(destino.exists() for destino in destinos):
        raise ValueError(
            "já existem arquivos do lote no diretório de saída; use --substituir "
            "ou informe outro --diretorio-saida"
        )

    # A lista licenciada é escrita ao lado do catálogo antes da validação:
    # assim o carregador oficial aplica a política aprovada do projeto a cada URL.
    _escrever_urls(catalogo, urls)
    _escrever_urls(autorizacoes, urls)

    try:
        resultado = carregar_alvos_csv_tolerante(catalogo)
    except ErroCatalogoFontes as erro:
        raise ValueError(f"não foi possível validar o catálogo preparado: {erro}") from erro

    urls_aprovadas = tuple(alvo.url_inicial for alvo in resultado.alvos)
    rejeitadas_politica = tuple(
        RejeicaoUrl(falha.numero_linha, falha.alvo_id, falha.mensagem)
        for falha in resultado.falhas
    )
    rejeitadas = (*rejeitadas_iniciais, *rejeitadas_politica)

    # Mantém nos dois CSVs somente URLs que passaram pela política técnica.
    _escrever_urls(catalogo, urls_aprovadas)
    _escrever_urls(autorizacoes, urls_aprovadas)
    relatorio.write_text(
        json.dumps(
            {
                "entrada": str(entrada),
                "linhas_com_url": len(linhas),
                "urls_prontas_para_coleta_e_publicacao": len(urls_aprovadas),
                "urls_rejeitadas": len(rejeitadas),
                "rejeitadas": [asdict(rejeicao) for rejeicao in rejeitadas],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return len(urls_aprovadas), len(rejeitadas), relatorio


def executar(argumentos: list[str] | None = None) -> int:
    """Executa a preparação sem iniciar o crawler nem alterar o MongoDB."""

    opcoes = criar_parser().parse_args(argumentos)

    try:
        aceitas, rejeitadas, relatorio = preparar_lote(
            entrada=opcoes.entrada.resolve(),
            diretorio_saida=opcoes.diretorio_saida.resolve(),
            substituir=opcoes.substituir,
        )
    except ValueError as erro:
        print(f"ERRO: {erro}")
        return 2

    print("# LOTE DE URLs LICENCIADAS PREPARADO")
    print(f"URLs prontas: {aceitas}")
    print(f"URLs rejeitadas: {rejeitadas}")
    print(f"Catálogo: {relatorio.with_name(NOME_CATALOGO)}")
    print(f"Autorizações: {relatorio.with_name(NOME_AUTORIZACOES)}")
    print(f"Relatório: {relatorio}")
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
