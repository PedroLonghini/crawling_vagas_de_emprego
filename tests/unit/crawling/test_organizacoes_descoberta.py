"""Testes da descoberta de artigos institucionais que anunciam vagas."""

import json

from scrapy.http import HtmlResponse, TextResponse

from observatorio_vagas.crawling.adaptadores.empresa_direta import (
    AdaptadorEmpresaDireta,
)


def _resposta(html: str, url: str) -> HtmlResponse:
    return HtmlResponse(
        url=url,
        body=html.encode(),
        encoding="utf-8",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


def test_nome_privacy_no_dominio_nao_bloqueia_url_de_vaga() -> None:
    """A regra de privacidade avalia o destino, não o nome da organização."""

    resposta = _resposta(
        '<a href="/vaga-para-analista/">Vaga para Analista</a>',
        "https://www.dataprivacybr.org/tag/vaga/",
    )

    candidatos = AdaptadorEmpresaDireta().descobrir(resposta)

    assert [item.url for item in candidatos] == [
        "https://www.dataprivacybr.org/vaga-para-analista/"
    ]


def test_descobre_vaga_mencionada_no_cartao_da_noticia() -> None:
    """O título do link pode ser genérico quando o cartão explica a vaga."""

    resposta = _resposta(
        """
        <article>
          <a href="/p/boletim-semanal">Boletim quinzenal</a>
          <p>Veja ainda: vaga para estágio em Direito na organização.</p>
        </article>
        <article>
          <a href="/p/noticia-comum">Outra edição</a>
          <p>Dados públicos e transparência.</p>
        </article>
        """,
        "https://news.fiquemsabendo.com.br/archive",
    )

    candidatos = AdaptadorEmpresaDireta().descobrir(resposta)

    assert [item.url for item in candidatos] == [
        "https://news.fiquemsabendo.com.br/p/boletim-semanal"
    ]
    assert candidatos[0].evidencias == ("vaga_no_cartao_institucional",)


def test_cartao_nao_libera_dominio_externo_nem_inscricao_encerrada() -> None:
    """A descoberta não atravessa domínios e evita vagas já marcadas como fechadas."""

    resposta = _resposta(
        """
        <article><a href="https://externa.example/vaga">Vaga externa</a></article>
        <article><a href="/vaga-antiga">Vaga com inscrições encerradas</a></article>
        """,
        "https://ok.org.br/noticias/",
    )

    assert AdaptadorEmpresaDireta().descobrir(resposta) == ()


def test_codam_segue_apenas_botoes_de_detalhe_da_propria_empresa() -> None:
    resposta = _resposta(
        '<a href="/operador-de-producao/">Candidatar-se</a>'
        '<a href="https://externa.example/analista/">Candidatar-se</a>'
        '<a href="/curriculo/">Enviar currículo</a>',
        "https://vagas.codam.com.br/",
    )

    candidatos = AdaptadorEmpresaDireta().descobrir(resposta)

    assert [item.url for item in candidatos] == [
        "https://vagas.codam.com.br/operador-de-producao/"
    ]


def test_solides_descobre_api_publica_e_detalhes_no_portal_da_empresa() -> None:
    listagem = _resposta("<div id='__next'></div>", "https://nitrogymct.vagas.solides.com.br/")
    api = AdaptadorEmpresaDireta().descobrir(listagem)

    assert api[0].url == (
        "https://apigw.solides.com.br/jobs/v3/home/vacancy?take=12&slug=nitrogymct&page=1"
    )
    resposta_api = TextResponse(
        url=api[0].url,
        body=json.dumps(
            {
                "data": {
                    "currentPage": 1,
                    "totalPages": 2,
                    "data": [{"id": 651691, "title": "Estagiário de Musculação"}],
                }
            }
        ).encode(),
        encoding="utf-8",
    )

    encontrados = AdaptadorEmpresaDireta().descobrir(resposta_api)

    assert [item.url for item in encontrados] == [
        "https://nitrogymct.vagas.solides.com.br/vaga/651691",
        "https://apigw.solides.com.br/jobs/v3/home/vacancy?take=12&slug=nitrogymct&page=2",
    ]


def test_senior_descobre_consulta_do_tenant() -> None:
    resposta = _resposta(
        "<div id='app'></div>",
        "https://platform.senior.com.br/hcmrs/hcm/curriculo/?tenant=kalunga&tenantdomain=kalunga.com.br",
    )

    encontrados = AdaptadorEmpresaDireta().descobrir(resposta)

    assert encontrados[0].evidencias == ("listagem_senior_api",)
    assert "tenant=kalunga" in encontrados[0].url
