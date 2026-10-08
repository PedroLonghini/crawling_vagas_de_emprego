"""Associa empresas e cria vagas canônicas para anúncios já gravados no MongoDB.

Serve para quando a extração gravou os anúncios mas o lote foi interrompido
antes do pós-processamento. Sem ``--confirmar`` só mostra o que seria feito.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime

from observatorio_vagas.config import get_settings
from observatorio_vagas.extraction.resolucao_empresa import (
    CacheEmpresas,
    resolver_e_associar_empresas_em_lote,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioVagasMongoDB,
    preparar_banco,
)

if __package__:
    from . import criar_vagas_canonicas
else:
    import criar_vagas_canonicas

TAMANHO_LOTE = 1000


def _criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alvo-id",
        action="append",
        help="alvo a pós-processar (repita para vários); sem ele, todos os do período",
    )
    parser.add_argument(
        "--observados-desde",
        type=datetime.fromisoformat,
        help="só anúncios observados a partir deste instante (ISO 8601 com fuso)",
    )
    parser.add_argument("--confirmar", action="store_true", help="grava no MongoDB")
    return parser


def main() -> int:
    opcoes = _criar_parser().parse_args()
    desde = opcoes.observados_desde
    if desde is not None and desde.tzinfo is None:
        desde = desde.replace(tzinfo=UTC)

    try:
        with ConexaoMongoDB(get_settings()) as conexao:
            banco = conexao.banco
            repositorio_anuncios = RepositorioAnunciosMongoDB(banco)

            filtro: dict[str, object] = {"empresa_id": None}
            if opcoes.alvo_id:
                filtro["alvo_id"] = {"$in": opcoes.alvo_id}
            if desde is not None:
                filtro["ultima_observacao_em"] = {"$gte": desde}

            documentos = list(banco["anuncios"].find(filtro, {"alvo_id": 1}))
            por_alvo: dict[str, int] = {}
            for documento in documentos:
                por_alvo[documento["alvo_id"]] = por_alvo.get(documento["alvo_id"], 0) + 1
            print(f"Anúncios sem empresa associada: {len(documentos)} em {len(por_alvo)} alvo(s)")

            if not opcoes.confirmar:
                print("Nada foi gravado. Use --confirmar para pós-processar.")
                return 0

            preparar_banco(banco)
            repositorio_empresas = RepositorioEmpresasMongoDB(banco)
            repositorio_vagas = RepositorioVagasMongoDB(banco)
            cache = CacheEmpresas.vazio()

            total_vagas = total_falhas = feitos = 0
            inicio = time.perf_counter()
            for alvo_id in por_alvo:
                pendentes = [
                    anuncio
                    for anuncio in repositorio_anuncios.listar_por_alvo(alvo_id, limite=10000)
                    if anuncio.empresa_id is None
                    and (desde is None or anuncio.ultima_observacao_em >= desde)
                ]
                for posicao in range(0, len(pendentes), TAMANHO_LOTE):
                    lote = pendentes[posicao : posicao + TAMANHO_LOTE]
                    empresas = resolver_e_associar_empresas_em_lote(
                        lote,
                        repositorio_empresas=repositorio_empresas,
                        repositorio_anuncios=repositorio_anuncios,
                        cache=cache,
                    )
                    criadas, reutilizadas, falhas = (
                        criar_vagas_canonicas.processar_anuncios_em_lote(
                            empresas.anuncios, repositorio_vagas=repositorio_vagas
                        )
                    )
                    feitos += len(lote)
                    total_vagas += criadas + reutilizadas
                    total_falhas += len(empresas.falhas) + len(falhas)
                    print(
                        f"{feitos}/{len(documentos)} anúncios | {alvo_id} | "
                        f"empresas criadas: {empresas.empresas_criadas} | "
                        f"vagas: {criadas} novas, {reutilizadas} atualizadas | "
                        f"falhas: {len(empresas.falhas) + len(falhas)} | "
                        f"{time.perf_counter() - inicio:.0f}s",
                        flush=True,
                    )
            print(f"Concluído: {total_vagas} vagas, {total_falhas} falhas.")
            return 0 if total_falhas == 0 else 1
    except ErroConexaoMongoDB as erro:
        print(f"ERRO NO MONGODB: {erro}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
