"""Mede a velocidade de leitura de páginas do data/raw (antivírus e Spotlight pesam aqui).

    python scripts/medir_disco.py            # 200 páginas aleatórias
    python scripts/medir_disco.py --paginas 500

Compara a primeira leitura (fria) com a segunda (em cache). Se a primeira for
dezenas de vezes mais lenta que a segunda, algo verifica cada arquivo novo
(antivírus, Spotlight): exclua a pasta data/ dessa verificação.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _ler(caminhos: list[Path]) -> tuple[float, list[float]]:
    latencias = []
    inicio = time.perf_counter()
    for caminho in caminhos:
        t = time.perf_counter()
        caminho.read_bytes()
        latencias.append(time.perf_counter() - t)
    return time.perf_counter() - inicio, latencias


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paginas", type=int, default=200)
    parser.add_argument("--pasta", type=Path, default=RAIZ / "data" / "raw" / "corpos")
    args = parser.parse_args()

    arquivos = [p for p in args.pasta.glob("*/*") if p.is_file()]
    if len(arquivos) < args.paginas:
        print(f"Só {len(arquivos)} arquivos em {args.pasta}; precisa de pelo menos {args.paginas}.")
        return 1
    amostra = random.sample(arquivos, args.paginas)
    comprimidos = sum(1 for p in amostra if p.suffix == ".gz")
    print(
        f"{len(arquivos)} arquivos no total; amostra: {comprimidos} comprimidos (.gz), "
        f"{args.paginas - comprimidos} em texto (.bin)"
    )

    for rotulo in ("1ª leitura (fria)", "2ª leitura (em cache)"):
        total, latencias = _ler(amostra)
        print(
            f"{rotulo:24} {len(amostra) / total:8.1f} arquivos/s | mediana "
            f"{statistics.median(latencias) * 1000:7.1f} ms | "
            f"máximo {max(latencias) * 1000:7.1f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
