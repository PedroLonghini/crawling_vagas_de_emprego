"""Coleta páginas de uma API licenciada, sempre com confirmação explícita."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.agregadores_api import (
    ClienteAdzunaApi,
    ClienteJoobleApi,
    ConfiguracaoAgregadorInvalida,
    ErroColetaAgregador,
)
from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.crawling.raw_storage import ArmazenamentoBrutoLocal
from observatorio_vagas.domain.enums import Fonte


def criar_parser() -> argparse.ArgumentParser:
    """Define limites conservadores para uma primeira coleta por API."""

    parser = argparse.ArgumentParser(description="Coleta páginas de uma API licenciada de vagas.")
    parser.add_argument(
        "--catalogo",
        type=Path,
        required=True,
        help="catálogo aprovado da API, informado explicitamente para cada execução",
    )
    parser.add_argument("--alvo-id", required=True)
    parser.add_argument("--consulta", default="")
    parser.add_argument("--paginas", type=int, default=1)
    parser.add_argument("--diretorio-raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--confirmar-coleta", action="store_true")
    return parser


def executar(argumentos: list[str] | None = None) -> int:
    """Mostra um plano ou grava respostas brutas quando autorizado."""

    opcoes = criar_parser().parse_args(argumentos)
    if not 1 <= opcoes.paginas <= 10:
        print("ERRO: paginas deve estar entre 1 e 10")
        return 2
    try:
        alvo = next(
            alvo for alvo in carregar_alvos_csv(opcoes.catalogo) if alvo.alvo_id == opcoes.alvo_id
        )
    except StopIteration:
        print("ERRO: alvo-id não foi encontrado no catálogo")
        return 2
    if alvo.fonte not in {Fonte.ADZUNA, Fonte.JOOBLE}:
        print("ERRO: o alvo precisa usar fonte adzuna ou jooble")
        return 2
    print(f"Fonte: {alvo.empresa_nome}")
    print(f"Páginas solicitadas: {opcoes.paginas}")
    print(f"Consulta: {opcoes.consulta or 'todas as vagas disponibilizadas pela API'}")
    if not opcoes.confirmar_coleta:
        print("SIMULAÇÃO: nenhuma chamada HTTP ou gravação será feita.")
        return 0
    if not alvo.habilitado_para_coleta:
        print("ERRO: o alvo está inativo ou ainda não possui política de coleta aprovada")
        return 2
    cliente = (
        ClienteAdzunaApi(get_settings())
        if alvo.fonte is Fonte.ADZUNA
        else ClienteJoobleApi(get_settings())
    )
    armazenamento = ArmazenamentoBrutoLocal(opcoes.diretorio_raw)
    try:
        for pagina in range(1, opcoes.paginas + 1):
            resposta = cliente.coletar(pagina=pagina, consulta=opcoes.consulta)
            resposta = replace(
                resposta,
                alvo_id=alvo.alvo_id,
                empresa_nome=alvo.empresa_nome,
            )
            resultado = armazenamento.salvar(resposta)
            print(f"Página {pagina}: HTTP {resposta.status_http} | {resultado.referencia}")
    except (ConfiguracaoAgregadorInvalida, ErroColetaAgregador, OSError, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
