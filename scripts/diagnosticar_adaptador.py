"""Inspeciona uma página de carreiras antes de criar um adaptador.

Não grava no MongoDB, não altera o catálogo e não submete formulários.
"""

from __future__ import annotations

import argparse
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.extraction.json_ld import extrair_job_postings_json_ld


def _baixar(url: str) -> tuple[bytes, int, str]:
    pedido = Request(
        url,
        headers={
            "User-Agent": "ObservatorioVagas/0.1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(pedido, timeout=20) as resposta:  # noqa: S310
        return resposta.read(10 * 1024 * 1024), resposta.status, resposta.geturl()


def _recomendacao(
    *, candidatos: int, json_ld: int, cards: int, formularios: int, sinais_js: int
) -> str:
    if json_ld or candidatos:
        return "usar_extrator_generico"
    if cards or formularios:
        return "adaptador_de_cards_na_listagem"
    if sinais_js:
        return "testar_renderizacao_javascript_ou_api_publica"
    return "inspecao_manual_da_estrutura_necessaria"


def diagnosticar(url: str) -> dict[str, object]:
    """Produz evidência mínima para decidir o menor adaptador possível."""

    corpo, status, url_final = _baixar(url)
    resposta = HtmlResponse(url=url_final, body=corpo, encoding="utf-8")
    seletor = resposta.selector
    candidatos = tuple(
        candidato
        for candidato in AdaptadorGenericoHTML().descobrir(resposta)
        if candidato.url.rstrip("/") != url_final.rstrip("/")
    )
    json_ld = extrair_job_postings_json_ld(resposta.text).vagas
    botoes = seletor.xpath(
        "//a[contains(translate(normalize-space(string(.)), "
        "'CANDIDATAR-SEAPPLY', 'candidatar-seapply'), 'candidatar') "
        "or contains(translate(normalize-space(string(.)), 'CANDIDATAR-SEAPPLY', "
        "'candidatar-seapply'), 'apply')]"
    )
    cards = [
        botao.xpath("ancestor::*[self::article or self::li or self::section or self::div][.//h2 or .//h3 or .//h4][1]")
        for botao in botoes
    ]
    sinais_js = (
        len(seletor.css("script[src]"))
        + len(seletor.css("script#__NEXT_DATA__"))
        + int("webpack" in resposta.text.casefold())
        + int("__next" in resposta.text.casefold())
    )
    exemplos = [
        {"texto": " ".join(item.texto.split())[:120], "url": item.url}
        for item in candidatos[:3]
    ]
    formularios = len(seletor.css("form"))
    return {
        "url_informada": url,
        "url_final": url_final,
        "http": status,
        "dominio": urlsplit(url_final).hostname,
        "job_postings_json_ld": len(json_ld),
        "links_genericos": len(candidatos),
        "formularios": formularios,
        "botoes_candidatura": len(botoes),
        "cards_com_candidatura": len([card for card in cards if card]),
        "sinais_javascript": sinais_js,
        "paginacao": bool(re.search(r"(?:page=|pagina|pr[oó]xima|next)", resposta.text, re.I)),
        "recomendacao": _recomendacao(
            candidatos=len(candidatos),
            json_ld=len(json_ld),
            cards=len([card for card in cards if card]),
            formularios=formularios,
            sinais_js=sinais_js,
        ),
        "amostras_links": exemplos,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostica a estratégia de adaptador de uma URL.")
    parser.add_argument("url", help="URL pública da página de carreiras")
    args = parser.parse_args()
    try:
        print(json.dumps(diagnosticar(args.url), ensure_ascii=False, indent=2))
    except Exception as erro:  # noqa: BLE001
        print(json.dumps({"erro": f"{erro.__class__.__name__}: {erro}"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
