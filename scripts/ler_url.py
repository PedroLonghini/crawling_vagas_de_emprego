"""Lê uma página de empregos isoladamente, sem gravar no MongoDB."""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.paginacao import eh_link_listagem
from observatorio_vagas.crawling.raw_storage import ArmazenamentoBrutoLocal
from observatorio_vagas.crawling.urls import normalizar_url_vaga
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta
from observatorio_vagas.extraction.html_generico import (
    extrair_job_posting_html_generico,
)
from observatorio_vagas.extraction.json_ld import extrair_job_postings_json_ld

URL_EXEMPLO = "https://vagas.solides.com.br/vagas"
_STATUS_REPETIR = {408, 425, 429, 500, 502, 503, 504, 522, 524}


def _baixar(url: str, *, tentativas: int = 3) -> tuple[bytes, int, str]:
    """Baixa uma página com tentativas para falhas transitórias."""

    ultima_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            requisicao = Request(
                url,
                headers={
                    "User-Agent": "ObservatorioVagas/0.1.0",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            with urlopen(requisicao, timeout=20) as resposta:  # noqa: S310
                return resposta.read(10 * 1024 * 1024), resposta.status, resposta.geturl()
        except HTTPError as erro:
            ultima_erro = erro
            if erro.code not in _STATUS_REPETIR or tentativa == tentativas:
                raise
        except (TimeoutError, URLError) as erro:
            ultima_erro = erro
            if tentativa == tentativas:
                raise
        sleep(0.5 * tentativa)

    raise RuntimeError("falha ao baixar página") from ultima_erro


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lê uma URL de listagem e mostra links candidatos a vagas."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=URL_EXEMPLO,
        help=f"URL completa da página de empregos (padrão: {URL_EXEMPLO})",
    )
    parser.add_argument("--limite", type=int, default=100)
    parser.add_argument("--salvar-raw", action="store_true", help="salva respostas em data/raw")
    parser.add_argument("--alvo-id", default="teste_url_empregos", help="ID usado no armazenamento")
    args = parser.parse_args()

    url_informada = args.url.strip()
    # Aceita também links copiados no formato Markdown: [texto](https://...).
    correspondencia = re.fullmatch(r"\[[^\]]*\]\((https?://[^)]+)\)", url_informada)
    url_informada = correspondencia.group(1) if correspondencia else url_informada
    endereco = urlsplit(url_informada)
    if endereco.scheme not in {"http", "https"} or not endereco.netloc:
        parser.error("informe uma URL HTTP/HTTPS completa")
    if args.limite < 1:
        parser.error("--limite deve ser positivo")

    try:
        corpo, status, url_final = _baixar(url_informada)
    except Exception as erro:  # noqa: BLE001
        print(f"ERRO ao ler URL: {erro}")
        return 1

    resposta_listagem = HtmlResponse(url=url_final, body=corpo, encoding="utf-8")
    links: list[str] = []
    armazenamento = ArmazenamentoBrutoLocal(Path("data/raw")) if args.salvar_raw else None
    momento = datetime.now(UTC)

    def salvar(
        url_solicitada: str,
        url_final: str,
        status_http: int,
        corpo: bytes,
        numero: int,
        tipo_pagina: TipoPaginaColeta,
    ) -> None:
        if armazenamento is None:
            return
        armazenamento.salvar(
            RespostaBruta(
                fonte=Fonte.OUTRA,
                alvo_id=args.alvo_id,
                empresa_nome="Empregos (teste controlado)",
                url_solicitada=url_solicitada,
                url_final=url_final,
                status_http=status_http,
                corpo=corpo,
                numero_pagina=numero,
                tipo_pagina=tipo_pagina,
                tipo_conteudo="text/html",
                codificacao="utf-8",
                coletado_em=momento,
            )
        )

    salvar(url_informada, url_final, status, corpo, 1, TipoPaginaColeta.INICIAL)
    links = list(
        dict.fromkeys(
            normalizar_url_vaga(c.url)
            for c in AdaptadorGenericoHTML().descobrir(resposta_listagem)
            if urlsplit(c.url).hostname == urlsplit(url_final).hostname
            and not eh_link_listagem(c.url)
        )
    )
    print(f"Candidatos antes do limite: {len(links)}")
    links = links[: args.limite]

    print(f"HTTP: {status}")
    print(f"URL final: {url_final}")
    print(f"Links candidatos encontrados: {len(links)}")
    anuncios = 0
    falhas_http = 0
    paginas_sem_vaga = 0
    for indice, link in enumerate(links, 1):
        try:
            corpo_pagina, status_pagina, url_final_pagina = _baixar(link)
            salvar(
                link,
                url_final_pagina,
                status_pagina,
                corpo_pagina,
                indice + 1,
                TipoPaginaColeta.DETALHE_VAGA,
            )
            resultado_json_ld = extrair_job_postings_json_ld(corpo_pagina)
            vagas = resultado_json_ld.vagas
            if not vagas:
                resultado_html = extrair_job_posting_html_generico(
                    corpo_pagina,
                    url=url_final_pagina,
                    empresa_nome=None,
                )
                vagas = resultado_html.vagas
            for vaga in vagas:
                anuncios += 1
                print(
                    f"{indice}. {vaga.get('title', '(sem título)')} | "
                    f"{vaga.get('hiringOrganization', {}).get('name', '(sem empresa)')} | {link}"
                )
            if not vagas:
                paginas_sem_vaga += 1
                print(f"{indice}. IGNORADO (vaga não reconhecida) | {link}")
        except Exception as erro:  # noqa: BLE001
            falhas_http += 1
            print(f"{indice}. IGNORADO ({erro.__class__.__name__}) | {link}")
    print(f"Anúncios de vagas extraídos: {anuncios}")
    print(f"Falhas ao abrir detalhes: {falhas_http}")
    print(f"Detalhes sem vaga reconhecida: {paginas_sem_vaga}")
    if armazenamento is not None:
        print("Respostas brutas salvas em data/raw.")
    else:
        print("Nenhuma alteração foi feita no armazenamento local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
