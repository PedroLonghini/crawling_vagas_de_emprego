"""Compara a análise de páginas reais antes e depois de mudar um extrator.

Uso:

    # 1. Antes da mudança: grava a referência.
    python scripts/comparar_extracao.py gravar --desde 2026-09-30T00:00:00+00:00 \
        --saida outputs/medicao/referencia.pkl

    # 2. Depois da mudança: compara com a referência.
    python scripts/comparar_extracao.py comparar --desde 2026-09-30T00:00:00+00:00 \
        --referencia outputs/medicao/referencia.pkl

Lê o inventário bruto e passa cada página pelos extratores, em paralelo.
Nada é gravado no MongoDB.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from observatorio_vagas.crawling.inventario import (
    RegistroInventarioBruto,
    carregar_inventario_bruto_desde,
)
from observatorio_vagas.extraction.processador import (
    _analisar_pagina,
    _pagina_pode_ser_processada,
)


def _normalizar(valor: object) -> object:
    """Forma comparável e estável de um resultado de extrator."""

    if is_dataclass(valor) and not isinstance(valor, type):
        return _normalizar(asdict(valor))

    if isinstance(valor, dict):
        return {str(chave): _normalizar(item) for chave, item in sorted(valor.items(), key=str)}

    if isinstance(valor, (list, tuple)):
        return [_normalizar(item) for item in valor]

    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor

    return repr(valor)


def _analisar(argumentos: tuple[Path, list[RegistroInventarioBruto]]) -> list[tuple]:
    base, registros = argumentos
    resultados = []

    for registro in registros:
        inicio = time.perf_counter()

        try:
            analise = _analisar_pagina(base, registro)
            saida = json.dumps(_normalizar(analise), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as erro:
            saida = f"ERRO {type(erro).__name__}: {erro}"

        resultados.append((registro.referencia, saida, time.perf_counter() - inicio))

    return resultados


def _executar(diretorio_raw: Path, desde: datetime, processos: int) -> dict[str, tuple[str, float]]:
    base = diretorio_raw.resolve()
    registros = [
        registro
        for registro in carregar_inventario_bruto_desde(base, desde=desde)
        if registro.coletado_em >= desde and _pagina_pode_ser_processada(registro)
    ]
    print(f"Páginas analisáveis: {len(registros)}", flush=True)

    pedacos = [registros[inicio : inicio + 50] for inicio in range(0, len(registros), 50)]
    resultados: dict[str, tuple[str, float]] = {}
    inicio = time.perf_counter()

    with ProcessPoolExecutor(processos, mp_context=multiprocessing.get_context("spawn")) as ex:
        for lote in ex.map(_analisar, [(base, pedaco) for pedaco in pedacos]):
            for referencia, saida, segundos in lote:
                resultados[referencia] = (saida, segundos)

    total = sum(segundos for _, segundos in resultados.values())
    print(
        f"Tempo total: {time.perf_counter() - inicio:.0f}s com {processos} processo(s); "
        f"CPU somada {total:.0f}s = {total / max(len(resultados), 1) * 1000:.0f} ms/página",
        flush=True,
    )
    return resultados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acao", choices=("gravar", "comparar"))
    parser.add_argument("--desde", type=datetime.fromisoformat, required=True)
    parser.add_argument("--diretorio-raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--processos", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--referencia", type=Path)
    parser.add_argument("--mostrar", type=int, default=5, help="diferenças exibidas")
    opcoes = parser.parse_args()

    if opcoes.desde.tzinfo is None:
        parser.error("--desde precisa de fuso horário, ex.: 2026-09-30T00:00:00+00:00")
    if opcoes.acao == "gravar" and opcoes.saida is None:
        parser.error("gravar exige --saida")
    if opcoes.acao == "comparar" and opcoes.referencia is None:
        parser.error("comparar exige --referencia")

    resultados = _executar(opcoes.diretorio_raw, opcoes.desde, opcoes.processos)

    if opcoes.acao == "gravar":
        opcoes.saida.parent.mkdir(parents=True, exist_ok=True)
        opcoes.saida.write_bytes(pickle.dumps(resultados))
        print(f"Referência gravada: {opcoes.saida}")
        return 0

    referencia: dict[str, tuple[str, float]] = pickle.loads(opcoes.referencia.read_bytes())
    diferentes = [
        ref for ref in referencia if ref in resultados and referencia[ref][0] != resultados[ref][0]
    ]
    faltando = [ref for ref in referencia if ref not in resultados]

    antes = sum(s for _, s in referencia.values())
    depois = sum(resultados[ref][1] for ref in referencia if ref in resultados)
    print(
        f"CPU somada: antes {antes:.0f}s, depois {depois:.0f}s ({antes / max(depois, 1e-9):.2f}x)"
    )
    print(
        f"Páginas iguais: {len(referencia) - len(diferentes) - len(faltando)} de {len(referencia)}"
    )
    print(f"Páginas diferentes: {len(diferentes)}; ausentes: {len(faltando)}")

    for ref in diferentes[: opcoes.mostrar]:
        print(
            f"\n--- {ref}\nANTES:  {referencia[ref][0][:600]}\nDEPOIS: {resultados[ref][0][:600]}"
        )

    return 1 if diferentes or faltando else 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
