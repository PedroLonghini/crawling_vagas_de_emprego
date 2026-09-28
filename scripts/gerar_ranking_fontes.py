"""Gera um ranking operacional a partir do estado do crawler."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from observatorio_vagas.crawling.estado_incremental import EstadoIncrementalLocal


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera ranking de desempenho por fonte.")
    parser.add_argument(
        "--estado",
        type=Path,
        default=Path("config/.cache/fontes_incrementais.json"),
        help="arquivo de estado criado pelas coletas",
    )
    parser.add_argument(
        "--saida-json",
        type=Path,
        default=Path("outputs/relatorios/ranking_fontes.json"),
    )
    parser.add_argument(
        "--saida-csv",
        type=Path,
        default=Path("outputs/relatorios/ranking_fontes.csv"),
    )
    return parser


def main() -> int:
    args = criar_parser().parse_args()
    ranking = EstadoIncrementalLocal(args.estado).ranking()

    for caminho in (args.saida_json, args.saida_csv):
        caminho.parent.mkdir(parents=True, exist_ok=True)

    args.saida_json.write_text(
        json.dumps(ranking, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    colunas = (
        "alvo_id",
        "vagas_novas_historicas",
        "sucessos",
        "falhas",
        "taxa_sucesso",
        "latencia_media_segundos",
        "proxima_coleta",
        "coletas_sem_novidade",
    )
    with args.saida_csv.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(ranking)

    print(f"Ranking salvo em: {args.saida_json}")
    print(f"Planilha CSV salva em: {args.saida_csv}")
    print(f"Fontes com histórico: {len(ranking)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
