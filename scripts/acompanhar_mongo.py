"""Acompanha, somente leitura, quanto o lote já gravou no MongoDB."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from observatorio_vagas.config import get_settings
from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_ANUNCIOS,
    COLECAO_EMPRESAS,
    COLECAO_VAGAS_CANONICAS,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intervalo", type=int, default=30, help="segundos entre leituras")
    parser.add_argument("--uma-vez", action="store_true", help="mostra uma leitura e sai")
    opcoes = parser.parse_args()

    banco = ConexaoMongoDB(get_settings()).banco
    anterior: dict[str, int] = {}

    while True:
        agora = datetime.now(UTC)
        linha = [agora.astimezone().strftime("%H:%M:%S")]
        for nome in (COLECAO_ANUNCIOS, COLECAO_VAGAS_CANONICAS, COLECAO_EMPRESAS):
            total = banco[nome].estimated_document_count()
            variacao = total - anterior.get(nome, total)
            anterior[nome] = total
            linha.append(f"{nome}: {total} ({variacao:+d})")
        print(" | ".join(linha), flush=True)
        if opcoes.uma_vez:
            return 0
        time.sleep(opcoes.intervalo)


if __name__ == "__main__":
    raise SystemExit(main())
