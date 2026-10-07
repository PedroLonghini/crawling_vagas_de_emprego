"""Prévia do lote de publicação sem MongoDB: mesma leitura e mesmas regras da fila oficial.

    python scripts/previa_lote_sem_mongo.py
    python scripts/previa_lote_sem_mongo.py --cadernos data/raw/cadernos/lote_20261006T185159844188Z

Lê as páginas já baixadas de uma coleta (os cadernos ficam em data/raw/cadernos), extrai
com o código atual, monta empresas e vagas em memória e passa tudo pela fila oficial do
Empregos (``preparar_fila_empregos``): blacklist, Brasil, campos obrigatórios,
"confidential", CNPJ zerado. Não grava no MongoDB e não chama a API.

Gera em ``--saida-dir``:

    payloads_unificados.json  vagas prontas para publicar (o mesmo formato da fila oficial)
    relatorio.json            resumo e, por vaga, por que foi barrada
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from observatorio_vagas.crawling.catalog import carregar_alvos_csv_tolerante
from observatorio_vagas.crawling.inventario import carregar_inventario_bruto_de_cadernos
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.vaga import VagaCanonica
from observatorio_vagas.extraction.normalizacao_vaga import converter_anuncio_em_vaga_canonica
from observatorio_vagas.extraction.processador import combinar_parciais, extrair_parcial
from observatorio_vagas.extraction.resolucao_empresa import (
    ErroResolucaoEmpresa,
    extrair_empresa_do_anuncio,
)
from observatorio_vagas.integrations.empregos import preparar_fila_empregos

RAIZ = Path(__file__).resolve().parents[1]


class _EmpresasEmMemoria:
    def __init__(self) -> None:
        self.por_id: dict[UUID, Empresa] = {}

    def buscar_por_id(self, empresa_id: UUID) -> Empresa | None:
        return self.por_id.get(empresa_id)


class _VagasEmMemoria:
    def __init__(self) -> None:
        self.por_id: dict[UUID, VagaCanonica] = {}

    def buscar_por_id(self, vaga_id: UUID) -> VagaCanonica | None:
        return self.por_id.get(vaga_id)


class _SemPublicacoes:
    """Nada foi publicado ainda: toda vaga elegível é nova."""

    def buscar_por_chave(self, chave: str) -> None:
        return None


def _extrair_alvo(diretorio_raw: str, registros: list, alvo_id: str, cache: str | None):
    return extrair_parcial(
        diretorio_raw, registros, alvo_id=alvo_id, cache_paginas=Path(cache) if cache else None
    )


def _cadernos_mais_recentes(diretorio_raw: Path) -> Path:
    pastas = sorted((diretorio_raw / "cadernos").glob("lote_*"), key=lambda p: p.stat().st_mtime)
    if not pastas:
        raise FileNotFoundError("nenhum caderno de coleta em data/raw/cadernos")
    return pastas[-1]


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--catalogo", type=Path, default=RAIZ / "config/lote_10mil_triado/catalogo_fontes.csv"
    )
    parser.add_argument("--diretorio-raw", type=Path, default=RAIZ / "data/raw")
    parser.add_argument(
        "--cadernos", type=Path, help="pasta de cadernos da coleta (padrão: a mais recente)"
    )
    parser.add_argument("--processos", type=int, default=10)
    parser.add_argument("--saida-dir", type=Path)
    return parser


def executar(argumentos: list[str] | None = None) -> int:
    opcoes = criar_parser().parse_args(argumentos)
    inicio = time.perf_counter()
    cadernos = opcoes.cadernos or _cadernos_mais_recentes(opcoes.diretorio_raw)
    saida = opcoes.saida_dir or RAIZ / "outputs/payloads" / (
        f"{datetime.now():%Y-%m-%d}_previa_sem_mongo"
    )
    saida.mkdir(parents=True, exist_ok=True)

    alvos = {alvo.alvo_id: alvo for alvo in carregar_alvos_csv_tolerante(opcoes.catalogo).alvos}
    registros = carregar_inventario_bruto_de_cadernos(sorted(cadernos.glob("*.jsonl")))
    por_alvo: dict[str, list] = defaultdict(list)
    for registro in registros:
        if registro.alvo_id in alvos:
            por_alvo[registro.alvo_id].append(registro)
    print(f"Coleta: {cadernos.name} | páginas: {len(registros)} | fontes: {len(por_alvo)}")

    cache = str(opcoes.diretorio_raw.parent / "cache" / "paginas.sqlite")
    parciais = []
    with ProcessPoolExecutor(max_workers=opcoes.processos) as executor:
        futuros = [
            executor.submit(_extrair_alvo, str(opcoes.diretorio_raw), regs, alvo_id, cache)
            for alvo_id, regs in por_alvo.items()
        ]
        for feitos, futuro in enumerate(as_completed(futuros), start=1):
            parciais.append(futuro.result())
            if feitos % 500 == 0 or feitos == len(futuros):
                print(f"  extração: {feitos}/{len(futuros)} fontes", flush=True)
    extracao = combinar_parciais(parciais)
    print(
        f"Anúncios extraídos: {len(extracao.anuncios)} | repetidos juntados: "
        f"{extracao.anuncios_duplicados}"
    )

    empresas, vagas = _EmpresasEmMemoria(), _VagasEmMemoria()
    anuncios = []
    sem_empresa = 0
    for anuncio in extracao.anuncios:
        try:
            empresa = extrair_empresa_do_anuncio(anuncio)
        except (ErroResolucaoEmpresa, ValueError):
            sem_empresa += 1
            anuncios.append(anuncio)
            continue
        empresas.por_id.setdefault(empresa.id, empresa)
        anuncio = anuncio.model_copy(update={"empresa_id": empresa.id})
        try:
            vaga = converter_anuncio_em_vaga_canonica(anuncio)
            vagas.por_id[vaga.id] = vaga
        except ValueError:
            pass
        anuncios.append(anuncio)

    resultado = preparar_fila_empregos(
        anuncios,
        alvos=alvos,
        repositorio_empresas=empresas,
        repositorio_vagas=vagas,
        repositorio_publicacoes=_SemPublicacoes(),
        momento_referencia=datetime.now(UTC),
    )
    payloads = [item.preparacao.payload for item in resultado.elegiveis if item.preparacao]
    bloqueios = Counter(
        f"{motivo.codigo}:{motivo.campo or '-'}"
        for item in resultado.bloqueadas
        for motivo in item.bloqueios
    )
    (saida / "payloads_unificados.json").write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    relatorio = {
        "modo": "PREVIA_SEM_MONGODB_SEM_API",
        "coleta": cadernos.name,
        "gerado_em": datetime.now(UTC).isoformat(),
        "resumo": {
            "anuncios_extraidos": len(extracao.anuncios),
            "repetidos_juntados": extracao.anuncios_duplicados,
            "elegiveis": len(resultado.elegiveis),
            "bloqueados": len(resultado.bloqueadas),
            "duplicadas_entre_fontes": len(resultado.duplicadas),
            "sem_empresa_na_extracao": sem_empresa,
        },
        "bloqueios_por_motivo": dict(bloqueios.most_common()),
        "itens_bloqueados": [
            {
                "titulo": item.titulo,
                "alvo_id": item.alvo_id,
                "bloqueios": [
                    {"codigo": m.codigo, "campo": m.campo, "mensagem": m.mensagem}
                    for m in item.bloqueios
                ],
            }
            for item in resultado.bloqueadas
        ],
    }
    (saida / "relatorio.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"Prontas para publicar: {len(payloads)} | barradas: {len(resultado.bloqueadas)}")
    for motivo, total in bloqueios.most_common(8):
        print(f"  {total:6}  {motivo}")
    print(f"Arquivos em: {saida} ({time.perf_counter() - inicio:.0f}s)")
    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace", line_buffering=True)
    raise SystemExit(executar())
