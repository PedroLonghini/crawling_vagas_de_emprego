"""Triagem de um catálogo de fontes: tira o que não é página de vagas e lista as improdutivas.

    python scripts/triar_catalogo.py --catalogo config/lote_10mil/catalogo_fontes.csv \\
        --saida-dir config/lote_10mil_triado
    python scripts/triar_catalogo.py --catalogo ... --saida-dir ... --com-mongo

Gera na pasta de saída:

    catalogo_fontes.csv, fontes_autorizadas.csv  só as fontes que passaram na triagem
    excluidas.csv                                as que saíram, com o motivo
    fontes_sem_anuncio.csv                       (com --com-mongo) fontes que nunca
                                                 geraram nenhum anúncio no MongoDB
    resumo.json                                  totais por motivo, classe e domínio

A triagem só exclui o inequívoco (PDF/imagem, categoria de blog, artigo datado sem
palavra de vaga, lista editorial, produto/loja/curso, descrição de cargo). Com
--com-mongo, também tira notícia, curso, produto ou institucional que nunca rendeu
anúncio. As demais fontes improdutivas NÃO são removidas: o relatório é para você
revisar, e o agendamento já as visita só a cada 7 ou 30 dias.
Não altera o catálogo original e, mesmo com --com-mongo, só lê o MongoDB.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.catalog import carregar_alvos_csv_tolerante
from observatorio_vagas.crawling.triagem_fontes import (
    MOTIVO_EDITORIAL_SEM_ANUNCIO,
    MOTIVO_ENTRADA_REPETIDA,
    classificar,
    dominio_lido_inteiro,
    motivo_de_exclusao,
    tem_palavra_de_vaga,
)
from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB

NOME_CATALOGO = "catalogo_fontes.csv"
NOME_AUTORIZACOES = "fontes_autorizadas.csv"


def _escrever_urls(caminho: Path, urls: list[str]) -> None:
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(("url",))
        escritor.writerows((url,) for url in urls)


def _alvos_com_anuncio() -> dict[str, int]:
    """Anúncios gravados por alvo (só os alvos com pelo menos um)."""

    banco = ConexaoMongoDB(get_settings()).banco
    return {
        documento["_id"]: documento["total"]
        for documento in banco["anuncios"].aggregate(
            [{"$group": {"_id": "$alvo_id", "total": {"$sum": 1}}}]
        )
    }


# Site lido inteiro a partir de qualquer entrada, detectado pelos anúncios: mais de
# uma entrada do mesmo domínio e uma delas concentra quase tudo (os anúncios iguais
# ficam com a última entrada que os gravou). Na coleta de 06/10/2026: maisvagases
# (3 entradas x 1.877), 99empregos (3 x 997), app.jobfy.pro (2 x 3.176). Jobijoba e
# BNE não entram: cada entrada traz poucas vagas diferentes (máximo 83 de ~1.000).
MINIMO_ANUNCIOS_SITE_INTEIRO = 200
FATIA_DA_ENTRADA_PRINCIPAL = 0.6


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def _dominios_lidos_inteiros(alvos, com_anuncio: dict[str, int]) -> set[str]:
    por_dominio: dict[str, list[int]] = collections.defaultdict(list)
    for alvo in alvos:
        por_dominio[_host(alvo.url_inicial)].append(com_anuncio.get(alvo.alvo_id, 0))
    return {
        dominio
        for dominio, totais in por_dominio.items()
        if len(totais) > 1
        and max(totais) >= MINIMO_ANUNCIOS_SITE_INTEIRO
        and max(totais) >= FATIA_DA_ENTRADA_PRINCIPAL * sum(totais)
    }


def _dominio_lido_inteiro(url: str, detectados: set[str]) -> str | None:
    host = _host(url)
    return dominio_lido_inteiro(url) or (host if host in detectados else None)


def _entrada_que_le_o_site_inteiro(
    alvos, com_anuncio: dict[str, int], detectados: set[str] = frozenset()
) -> dict[str, str]:
    """Para cada site lido inteiro a partir de qualquer entrada, o alvo que fica.

    Fica o que mais rendeu anúncios na última rodada (sem Mongo, o primeiro do catálogo).
    """

    escolhido: dict[str, str] = {}
    for alvo in alvos:
        dominio = _dominio_lido_inteiro(alvo.url_inicial, detectados)
        if dominio is None:
            continue
        atual = escolhido.get(dominio)
        if atual is None or com_anuncio.get(alvo.alvo_id, 0) > com_anuncio.get(atual, 0):
            escolhido[dominio] = alvo.alvo_id
    return escolhido


def triar(catalogo: Path, saida: Path, *, com_mongo: bool) -> dict:
    resultado = carregar_alvos_csv_tolerante(catalogo)
    alvos = resultado.alvos
    saida.mkdir(parents=True, exist_ok=True)

    com_anuncio = _alvos_com_anuncio() if com_mongo else {}
    detectados = _dominios_lidos_inteiros(alvos, com_anuncio)
    unica_entrada = _entrada_que_le_o_site_inteiro(alvos, com_anuncio, detectados)
    mantidas: list[str] = []
    excluidas: list[tuple[str, str]] = []
    por_motivo: collections.Counter[str] = collections.Counter()
    for alvo in alvos:
        motivo = motivo_de_exclusao(alvo.url_inicial)
        dominio = _dominio_lido_inteiro(alvo.url_inicial, detectados)
        if not motivo and dominio and unica_entrada.get(dominio) != alvo.alvo_id:
            motivo = MOTIVO_ENTRADA_REPETIDA
        # Notícia, curso, produto ou institucional que já foi visitada e nunca rendeu
        # nenhum anúncio sai do catálogo; com anúncio, fica (pode ser vaga em notícia).
        if (
            not motivo
            and com_mongo
            and alvo.alvo_id not in com_anuncio
            and classificar(alvo.url_inicial) == "editorial_ou_produto"
            and not tem_palavra_de_vaga(alvo.url_inicial)
        ):
            motivo = MOTIVO_EDITORIAL_SEM_ANUNCIO
        if motivo:
            excluidas.append((alvo.url_inicial, motivo))
            por_motivo[motivo] += 1
        else:
            mantidas.append(alvo.url_inicial)

    _escrever_urls(saida / NOME_CATALOGO, mantidas)
    _escrever_urls(saida / NOME_AUTORIZACOES, mantidas)
    with (saida / "excluidas.csv").open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(("url", "motivo"))
        escritor.writerows(excluidas)

    resumo: dict = {
        "catalogo": str(catalogo),
        "fontes_no_catalogo": len(alvos),
        "fontes_mantidas": len(mantidas),
        "fontes_excluidas": len(excluidas),
        "excluidas_por_motivo": dict(por_motivo.most_common()),
        "linhas_invalidas_no_catalogo": len(resultado.falhas),
    }

    if com_mongo:
        sem_anuncio = [alvo for alvo in alvos if alvo.alvo_id not in com_anuncio]
        por_classe: collections.Counter[str] = collections.Counter()
        por_dominio: collections.Counter[str] = collections.Counter()
        with (saida / "fontes_sem_anuncio.csv").open("w", encoding="utf-8", newline="") as arquivo:
            escritor = csv.writer(arquivo)
            escritor.writerow(("url", "classe", "dominio", "alvo_id"))
            for alvo in sem_anuncio:
                classe = classificar(alvo.url_inicial)
                dominio = (urlsplit(alvo.url_inicial).hostname or "").removeprefix("www.")
                por_classe[classe] += 1
                por_dominio[dominio] += 1
                escritor.writerow((alvo.url_inicial, classe, dominio, alvo.alvo_id))
        resumo["fontes_sem_nenhum_anuncio_no_mongo"] = len(sem_anuncio)
        resumo["fontes_com_anuncio_no_mongo"] = len(alvos) - len(sem_anuncio)
        resumo["sem_anuncio_por_classe"] = dict(por_classe.most_common())
        resumo["sem_anuncio_maiores_dominios"] = por_dominio.most_common(15)

    (saida / "resumo.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return resumo


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--catalogo", type=Path, required=True)
    parser.add_argument("--saida-dir", type=Path, required=True)
    parser.add_argument(
        "--com-mongo",
        action="store_true",
        help="também lista as fontes que nunca geraram anúncio (consulta o MongoDB, só leitura)",
    )
    opcoes = parser.parse_args()

    if opcoes.saida_dir.resolve() == opcoes.catalogo.resolve().parent:
        print("ERRO: a pasta de saída não pode ser a do catálogo original.")
        return 2

    try:
        resumo = triar(opcoes.catalogo, opcoes.saida_dir, com_mongo=opcoes.com_mongo)
    except (OSError, ValueError, RuntimeError) as erro:
        print(f"ERRO: {erro}")
        return 1

    print("# TRIAGEM DO CATÁLOGO")
    print(f"Fontes no catálogo: {resumo['fontes_no_catalogo']}")
    print(f"Mantidas: {resumo['fontes_mantidas']} | excluídas: {resumo['fontes_excluidas']}")
    for motivo, total in resumo["excluidas_por_motivo"].items():
        print(f"  {total:5}  {motivo}")
    if "fontes_sem_nenhum_anuncio_no_mongo" in resumo:
        print(
            f"Fontes sem nenhum anúncio no MongoDB: {resumo['fontes_sem_nenhum_anuncio_no_mongo']}"
            f" (com anúncio: {resumo['fontes_com_anuncio_no_mongo']})"
        )
        print(f"  por classe: {resumo['sem_anuncio_por_classe']}")
    print(f"Arquivos em: {opcoes.saida_dir}")
    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
