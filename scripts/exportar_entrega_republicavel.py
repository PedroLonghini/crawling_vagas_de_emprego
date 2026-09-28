"""Exporta payloads locais a partir de uma coleta, sem MongoDB nem API.

Útil quando a coleta foi concluída, mas o túnel/local do MongoDB não está
disponível. O resultado é uma lista JSON em arquivo ``.txt``, pronta para
revisão ou para ser usada como lote de payloads.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from observatorio_vagas.crawling.catalog import carregar_alvos_csv_tolerante
from observatorio_vagas.crawling.inventario import carregar_inventario_bruto_desde
from observatorio_vagas.domain.localizacao import endereco_indica_brasil
from observatorio_vagas.extraction.processador import processar_respostas_brutas


def _data_hora(valor: str) -> datetime:
    resultado = datetime.fromisoformat(valor)
    if resultado.tzinfo is None or resultado.utcoffset() is None:
        raise argparse.ArgumentTypeError("--coletado-desde precisa incluir fuso horário")
    return resultado


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogo", type=Path, default=Path("config/catalogo_fontes.csv"))
    parser.add_argument("--diretorio-raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--coletado-desde", type=_data_hora, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    return parser


def _payload(anuncio: object, *, url_fonte: str) -> dict[str, object]:
    identificador = sha256(
        f"{anuncio.alvo_id}\n{anuncio.id_externo}".encode()
    ).hexdigest()
    url_candidatura = str(anuncio.url_candidatura or anuncio.url)
    localizacao = anuncio.endereco_original or anuncio.localidade_original
    return {
        "company": {
            "applyUrl": url_candidatura,
            "name": anuncio.empresa_original,
        },
        "externalJobPostingId": f"fonte-{identificador}",
        "jobPostingOperationType": "CREATE",
        "title": anuncio.titulo_original,
        "description": (
            f"{anuncio.descricao_original.strip()}\n\n"
            f"Fonte: {url_fonte}\n"
            f"Origem da vaga: {anuncio.url}"
        ),
        "location": {"address": localizacao},
    }


def executar(argumentos: list[str] | None = None) -> int:
    opcoes = criar_parser().parse_args(argumentos)
    catalogo = carregar_alvos_csv_tolerante(opcoes.catalogo)
    alvos = {
        alvo.alvo_id: alvo
        for alvo in catalogo.alvos
        if alvo.habilitado_para_publicacao
    }
    registros = carregar_inventario_bruto_desde(
        opcoes.diretorio_raw,
        desde=opcoes.coletado_desde,
    )
    resultado = processar_respostas_brutas(
        opcoes.diretorio_raw,
        coletado_desde=opcoes.coletado_desde,
        registros=registros,
    )

    payloads: list[dict[str, object]] = []
    por_fonte: Counter[str] = Counter()
    bloqueados = Counter()
    for anuncio in resultado.anuncios:
        alvo = alvos.get(anuncio.alvo_id)
        if alvo is None:
            bloqueados["fonte_sem_permissao_atual"] += 1
            continue
        if not anuncio.empresa_original:
            bloqueados["empresa_ausente"] += 1
            continue
        if len(anuncio.descricao_original.strip()) < 80:
            bloqueados["descricao_insuficiente"] += 1
            continue
        if not (anuncio.url_candidatura or anuncio.url):
            bloqueados["url_candidatura_ausente"] += 1
            continue
        if not endereco_indica_brasil(
            anuncio.localidade_original,
            anuncio.endereco_original,
            anuncio.cep_original,
        ):
            bloqueados["fora_do_brasil_ou_localizacao_insuficiente"] += 1
            continue
        payloads.append(_payload(anuncio, url_fonte=alvo.url_inicial))
        por_fonte[anuncio.alvo_id or "sem_alvo"] += 1

    opcoes.saida.parent.mkdir(parents=True, exist_ok=True)
    opcoes.saida.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    relatorio = {
        "modo": "EXPORTACAO_LOCAL_SEM_MONGODB_SEM_API",
        "coletado_desde": opcoes.coletado_desde.isoformat(),
        "anuncios_extraidos": len(resultado.anuncios),
        "payloads_exportados": len(payloads),
        "por_fonte": dict(sorted(por_fonte.items())),
        "bloqueados": dict(sorted(bloqueados.items())),
        "falhas_extracao": len(resultado.falhas),
    }
    caminho_relatorio = opcoes.saida.with_suffix(".relatorio.json")
    caminho_relatorio.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Payloads exportados: {len(payloads)}")
    print(f"Arquivo TXT: {opcoes.saida}")
    print(f"Relatório: {caminho_relatorio}")
    for alvo_id, quantidade in sorted(por_fonte.items(), key=lambda item: (-item[1], item[0])):
        print(f"- {alvo_id}: {quantidade}")
    if bloqueados:
        print("Bloqueios:")
        for motivo, quantidade in sorted(bloqueados.items()):
            print(f"- {motivo}: {quantidade}")
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
