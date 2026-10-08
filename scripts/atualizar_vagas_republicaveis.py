"""Mantém a coleção vagas_republicaveis: só vagas liberadas para publicar.

Reaproveita a fila de preparação do Empregos (a mesma regra de elegibilidade
usada para gerar payloads) e grava no MongoDB somente as vagas ELEGÍVEIS, para
serem lidas no Compass. A coleção é derivada: pode ser apagada e recriada a
qualquer momento, e nenhuma outra coleção é alterada.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pymongo import ASCENDING, DESCENDING, IndexModel, ReplaceOne

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.integrations.empregos import (
    ItemFilaEmpregos,
    preparar_fila_empregos,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    RepositorioVagasMongoDB,
)

COLECAO_VAGAS_REPUBLICAVEIS = "vagas_republicaveis"
LIMITE_POR_ALVO = 10000


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogo",
        type=Path,
        default=Path("config/lote_10mil_triado/catalogo_fontes.csv"),
        help="catálogo de fontes e autorizações",
    )
    parser.add_argument("--alvo-id", help="avalia somente os anúncios deste alvo")
    parser.add_argument(
        "--simular",
        action="store_true",
        help="só conta as elegíveis; não grava nada no MongoDB",
    )
    return parser


def _documento(item: ItemFilaEmpregos, anuncio: Any, avaliado_em: datetime) -> dict[str, Any]:
    """Resume a vaga liberada e guarda o payload exato que seria enviado."""

    preparacao = item.preparacao
    payload = preparacao.payload if preparacao is not None else None
    if payload is None:
        raise ValueError("item elegível sem payload")

    empresa = payload.get("company") or {}
    local = payload.get("location") or {}
    site = (urlsplit(str(anuncio.url)).hostname or "").removeprefix("www.")

    return {
        "_id": str(item.anuncio_id),
        "anuncio_id": str(item.anuncio_id),
        "vaga_id": str(item.vaga_id),
        "empresa_id": str(item.empresa_id),
        "alvo_id": item.alvo_id,
        "titulo": item.titulo,
        "empresa": empresa.get("name"),
        "endereco": local.get("address"),
        "site": site,
        "fonte": anuncio.fonte.value,
        "url": str(anuncio.url),
        "publicado_em": str(anuncio.publicado_em) if anuncio.publicado_em else None,
        "expira_em": str(anuncio.expira_em) if anuncio.expira_em else None,
        "campos_preenchidos": item.campos_preenchidos,
        "total_campos": item.total_campos,
        "percentual_preenchimento": round(item.percentual_preenchimento, 2),
        "alertas": [{"campo": a.campo, "mensagem": a.mensagem} for a in item.alertas],
        "chave_idempotencia": item.chave_idempotencia,
        "payload": payload,
        "avaliado_em": avaliado_em,
    }


def executar(argumentos: list[str] | None = None) -> int:
    opcoes = criar_parser().parse_args(argumentos)
    avaliado_em = datetime.now(UTC)

    try:
        alvos = {alvo.alvo_id: alvo for alvo in carregar_alvos_csv(opcoes.catalogo)}
        ids_alvos = [opcoes.alvo_id] if opcoes.alvo_id else list(alvos)

        with ConexaoMongoDB(get_settings()) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)

            por_id: dict[object, Any] = {}
            for alvo_id in ids_alvos:
                encontrados = repositorio_anuncios.listar_por_alvo(alvo_id, limite=LIMITE_POR_ALVO)
                for anuncio in encontrados:
                    por_id[anuncio.id] = anuncio
            anuncios = tuple(por_id.values())

            resultado = preparar_fila_empregos(
                anuncios,
                alvos=alvos,
                repositorio_empresas=RepositorioEmpresasMongoDB(conexao.banco),
                repositorio_vagas=RepositorioVagasMongoDB(conexao.banco),
                repositorio_publicacoes=RepositorioPublicacoesEmpregosMongoDB(conexao.banco),
            )

            elegiveis = resultado.elegiveis
            print(f"Anúncios avaliados: {len(resultado.itens)}")
            print(f"Republicáveis: {len(elegiveis)}")
            print(f"Bloqueados: {len(resultado.bloqueadas)}")
            print(f"Já registrados no histórico: {len(resultado.ja_registradas)}")
            print(f"Duplicados entre fontes: {len(resultado.duplicadas)}")

            if opcoes.simular:
                print("Modo simulação: nada foi gravado.")
                return 0

            colecao = conexao.banco[COLECAO_VAGAS_REPUBLICAVEIS]
            colecao.create_indexes(
                [
                    IndexModel([("alvo_id", ASCENDING)], name="ix_republicaveis_alvo"),
                    IndexModel([("site", ASCENDING)], name="ix_republicaveis_site"),
                    IndexModel([("empresa", ASCENDING)], name="ix_republicaveis_empresa"),
                    IndexModel([("avaliado_em", DESCENDING)], name="ix_republicaveis_avaliado"),
                ]
            )

            documentos = [
                _documento(item, por_id[item.anuncio_id], avaliado_em) for item in elegiveis
            ]
            if documentos:
                colecao.bulk_write(
                    [ReplaceOne({"_id": doc["_id"]}, doc, upsert=True) for doc in documentos],
                    ordered=False,
                )

            # Quem foi avaliado agora e deixou de ser republicável sai da coleção.
            # Anúncios fora desta rodada (outro alvo) não são tocados.
            vigentes = {doc["_id"] for doc in documentos}
            avaliados = {str(item.anuncio_id) for item in resultado.itens}
            removidos = colecao.delete_many({"_id": {"$in": list(avaliados - vigentes)}})

            print(f"Gravadas em {COLECAO_VAGAS_REPUBLICAVEIS}: {len(documentos)}")
            print(f"Removidas por não serem mais republicáveis: {removidos.deleted_count}")
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 1

    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(executar())
