"""Remove do MongoDB tudo o que veio de um domínio: anúncios, vagas e empresas órfãs.

    python scripts/remover_fonte_do_mongo.py --dominio empregandobrasil.com.br
    python scripts/remover_fonte_do_mongo.py --dominio empregandobrasil.com.br --confirmar

Sem --confirmar só conta. Com --confirmar, grava antes um backup (JSONL gzip em
outputs/backup_mongo/) e depois apaga:

- anúncios cuja URL é do domínio (ou de um subdomínio);
- as vagas canônicas desses anúncios (o id da vaga é derivado do id do anúncio);
- as empresas que só esses anúncios usavam (empresa com anúncio ou vaga de outra
  fonte fica).

Publicações já feitas no Empregos fazem o script parar: tirar do ar é outro passo.
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from bson import json_util
from bson.binary import UuidRepresentation

from observatorio_vagas.config import get_settings
from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB

LOTE = 5000
OPCOES_JSON = json_util.JSONOptions(
    json_mode=json_util.JSONMode.CANONICAL, uuid_representation=UuidRepresentation.STANDARD
)


def _id_da_vaga(anuncio_id) -> object:
    """Mesmo cálculo de ``normalizacao_vaga``: a vaga nasce do anúncio."""

    return uuid5(NAMESPACE_URL, f"https://observatorio-vagas.local/vagas/anuncios/{anuncio_id}")


def _em_lotes(itens: list) -> list[list]:
    return [itens[i : i + LOTE] for i in range(0, len(itens), LOTE)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--dominio", required=True, help="ex.: empregandobrasil.com.br")
    parser.add_argument("--confirmar", action="store_true", help="faz o backup e apaga")
    opcoes = parser.parse_args()

    dominio = opcoes.dominio.strip().casefold().removeprefix("www.")
    banco = ConexaoMongoDB(get_settings()).banco
    filtro = {
        "url": {
            "$regex": rf"^https?://([a-z0-9-]+\.)*{re.escape(dominio)}(/|:|$|\?)",
            "$options": "i",
        }
    }

    anuncios = list(banco["anuncios"].find(filtro, {"_id": 1, "empresa_id": 1}))
    ids_anuncios = [a["_id"] for a in anuncios]
    ids_vagas = [_id_da_vaga(i) for i in ids_anuncios]
    vagas_existentes = []
    for lote in _em_lotes(ids_vagas):
        vagas_existentes += [
            v["_id"] for v in banco["vagas_canonicas"].find({"_id": {"$in": lote}}, {"_id": 1})
        ]

    publicadas = 0
    for lote in _em_lotes(ids_anuncios):
        publicadas += banco["publicacoes_empregos"].count_documents({"anuncio_id": {"$in": lote}})

    empresas = {a.get("empresa_id") for a in anuncios} - {None}
    empresas_orfas = []
    for empresa_id in empresas:
        outros_anuncios = (
            banco["anuncios"].count_documents(
                {"empresa_id": empresa_id, "_id": {"$nin": ids_anuncios}}, limit=1
            )
            if len(ids_anuncios) < 50000
            else 1
        )
        outras_vagas = banco["vagas_canonicas"].count_documents(
            {"empresa_id": empresa_id, "_id": {"$nin": vagas_existentes}}, limit=1
        )
        if not outros_anuncios and not outras_vagas:
            empresas_orfas.append(empresa_id)

    print(f"Domínio: {dominio}")
    print(
        f"Anúncios: {len(ids_anuncios)} | vagas: {len(vagas_existentes)} | "
        f"empresas só desta fonte: {len(empresas_orfas)} (de {len(empresas)})"
    )
    print(f"Publicações já feitas no Empregos: {publicadas}")
    if publicadas:
        print("PARADO: há vagas publicadas; tire-as do ar antes de apagar.")
        return 2
    if not opcoes.confirmar:
        print("Simulação: nada foi apagado. Use --confirmar para fazer o backup e apagar.")
        return 0

    momento = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destino = Path("outputs/backup_mongo") / f"remocao_{dominio}_{momento}.jsonl.gz"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destino, "wt", encoding="utf-8") as saida:
        for colecao, ids in (
            ("anuncios", ids_anuncios),
            ("vagas_canonicas", vagas_existentes),
            ("empresas", empresas_orfas),
        ):
            for lote in _em_lotes(ids):
                for documento in banco[colecao].find({"_id": {"$in": lote}}):
                    saida.write(
                        json_util.dumps(
                            {"colecao": colecao, "documento": documento}, json_options=OPCOES_JSON
                        )
                        + "\n"
                    )
    print(f"Backup: {destino} ({destino.stat().st_size:,} bytes)")

    for colecao, ids in (
        ("vagas_canonicas", vagas_existentes),
        ("anuncios", ids_anuncios),
        ("empresas", empresas_orfas),
    ):
        apagados = sum(
            banco[colecao].delete_many({"_id": {"$in": lote}}).deleted_count
            for lote in _em_lotes(ids)
        )
        print(f"Apagados em {colecao}: {apagados}")
    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
