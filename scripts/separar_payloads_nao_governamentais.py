"""Separa payloads elegíveis cuja URL de origem não é governamental."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from observatorio_vagas.crawling.filtro_conteudo import eh_concurso_publico


def criar_parser() -> argparse.ArgumentParser:
    """Define os arquivos de entrada e saída da separação local."""

    parser = argparse.ArgumentParser(
        description=(
            "Gera uma lista JSON de origens não governamentais, "
            "sem concursos ou processos seletivos públicos."
        )
    )
    parser.add_argument("--entrada", type=Path, required=True, help="lista JSON unificada do lote")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--somente-governamentais",
        action="store_true",
        help="mantém somente payloads cuja URL de origem pertence a domínio governamental",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        help="arquivo JSON filtrado; padrão: payloads_nao_governamentais.json ao lado da entrada",
    )
    return parser


def _origem_e_governamental(payload: dict[str, Any]) -> bool:
    """Identifica domínios governamentais pela URL de referência da vaga."""

    company = payload.get("company")
    url = company.get("applyUrl") if isinstance(company, dict) else None

    if not isinstance(url, str):
        return False

    dominio = (urlsplit(url).hostname or "").casefold()

    return ".gov." in dominio or dominio.endswith(".gov.br")


def _e_concurso_ou_processo_seletivo_publico(payload: dict[str, Any]) -> bool:
    """Aplica a mesma regra global de exclusão usada durante a coleta."""

    company = payload.get("company")
    url = company.get("applyUrl") if isinstance(company, dict) else ""
    conteudo = " ".join(
        valor
        for valor in (payload.get("title"), payload.get("description"))
        if isinstance(valor, str)
    )

    return eh_concurso_publico(
        url=url if isinstance(url, str) else "",
        conteudo=conteudo,
    )


def filtrar_payloads_nao_governamentais(
    payloads: Sequence[object],
) -> list[dict[str, Any]]:
    """Mantém somente corpos JSON válidos cuja origem não é governamental."""

    return [
        payload
        for payload in payloads
        if (
            isinstance(payload, dict)
            and not _origem_e_governamental(payload)
            and not _e_concurso_ou_processo_seletivo_publico(payload)
        )
    ]


def filtrar_payloads_governamentais(
    payloads: Sequence[object],
) -> list[dict[str, Any]]:
    """Mantém somente corpos JSON cuja URL de origem é governamental."""

    return [
        payload
        for payload in payloads
        if isinstance(payload, dict) and _origem_e_governamental(payload)
    ]


def executar(argumentos: list[str] | None = None) -> int:
    """Lê uma lista unificada e grava sua versão sem domínios governamentais."""

    opcoes = criar_parser().parse_args(argumentos)
    nome_padrao = (
        "payloads_governamentais.json"
        if opcoes.somente_governamentais
        else "payloads_nao_governamentais.json"
    )
    saida = opcoes.saida or (opcoes.entrada.parent / nome_padrao)

    try:
        conteudo = json.loads(opcoes.entrada.read_text(encoding="utf-8"))

        if not isinstance(conteudo, list):
            raise ValueError("o arquivo de entrada precisa ser uma lista JSON de payloads")

        payloads = (
            filtrar_payloads_governamentais(conteudo)
            if opcoes.somente_governamentais
            else filtrar_payloads_nao_governamentais(conteudo)
        )
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")

    except (OSError, TypeError, ValueError, json.JSONDecodeError) as erro:
        print(f"ERRO: {erro}")
        return 1

    print(f"Payloads analisados: {len(conteudo)}")
    categoria = "governamentais" if opcoes.somente_governamentais else "não governamentais"
    print(f"Payloads {categoria}: {len(payloads)}")
    print(f"Arquivo JSON salvo em: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
