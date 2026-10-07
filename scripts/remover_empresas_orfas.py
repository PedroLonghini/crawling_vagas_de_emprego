"""Remove do MongoDB as empresas que nenhum anúncio e nenhuma vaga usam mais.

    python scripts/remover_empresas_orfas.py              # só conta e mostra exemplos
    python scripts/remover_empresas_orfas.py --confirmar  # backup e remoção

Depois de uma releitura, anúncios que antes apontavam para nomes falsos ("JUND
EMPREGOS", "Jobbrazil", "Empregador") passam a apontar para a empresa certa ou para
"confidential", e as empresas falsas ficam sem uso. Com --confirmar, grava antes um
backup (JSONL gzip em outputs/backup_mongo/) e só então apaga.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from datetime import UTC, datetime
from pathlib import Path

from bson import json_util
from bson.binary import UuidRepresentation

from observatorio_vagas.config import get_settings
from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB

LOTE = 5000
OPCOES_JSON = json_util.JSONOptions(
    json_mode=json_util.JSONMode.CANONICAL, uuid_representation=UuidRepresentation.STANDARD
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--confirmar", action="store_true", help="faz o backup e apaga")
    opcoes = parser.parse_args()

    banco = ConexaoMongoDB(get_settings()).banco
    usadas = set(banco["anuncios"].distinct("empresa_id")) | set(
        banco["vagas_canonicas"].distinct("empresa_id")
    )
    orfas = [e["_id"] for e in banco["empresas"].find({}, {"_id": 1}) if e["_id"] not in usadas]
    total = banco["empresas"].estimated_document_count()
    print(f"Empresas: {total} | sem anúncio e sem vaga: {len(orfas)}")
    for empresa in banco["empresas"].find({"_id": {"$in": orfas[:15]}}, {"nome_fantasia": 1}):
        print(f"  - {empresa.get('nome_fantasia')}")
    if not opcoes.confirmar or not orfas:
        print("Simulação: nada foi apagado. Use --confirmar para fazer o backup e apagar.")
        return 0

    momento = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destino = Path("outputs/backup_mongo") / f"empresas_orfas_{momento}.jsonl.gz"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destino, "wt", encoding="utf-8") as saida:
        for inicio in range(0, len(orfas), LOTE):
            for documento in banco["empresas"].find(
                {"_id": {"$in": orfas[inicio : inicio + LOTE]}}
            ):
                saida.write(json_util.dumps(documento, json_options=OPCOES_JSON) + "\n")
    print(f"Backup: {destino} ({destino.stat().st_size:,} bytes)")
    apagadas = sum(
        banco["empresas"].delete_many({"_id": {"$in": orfas[i : i + LOTE]}}).deleted_count
        for i in range(0, len(orfas), LOTE)
    )
    print(f"Empresas apagadas: {apagadas}")
    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
