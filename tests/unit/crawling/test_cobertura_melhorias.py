"""Regressões de descoberta sem rede e sem MongoDB."""

from types import SimpleNamespace

from scrapy.http import HtmlResponse, Request

from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.crawling.spiders.catalogo_fontes import (
    CatalogoFontesSpider,
    _diagnosticar_fonte,
)
from observatorio_vagas.crawling.urls import normalizar_url_vaga


def test_leitor_isolado_extrai_multiplas_vagas_do_mesmo_detalhe(monkeypatch, capsys):
    from scripts import ler_url

    paginas = iter(
        [
            b'<a href="/vaga/1">Vaga</a>',
            b"""<script type="application/ld+json">[
        {"@type":"JobPosting","title":"Analista","hiringOrganization":{"name":"A"}},
        {"@type":"JobPosting","title":"Tecnico","hiringOrganization":{"name":"B"}}
        ]</script>""",
        ]
    )
    monkeypatch.setattr(ler_url, "_baixar", lambda url: (next(paginas), 200, url))
    monkeypatch.setattr("sys.argv", ["ler_url.py", "https://empresa.example/vagas"])
    assert ler_url.main() == 0
    assert "Anúncios de vagas extraídos: 2" in capsys.readouterr().out


def test_card_sem_palavra_vaga_e_tracking_duplicado():
    resposta = HtmlResponse(
        url="https://empresa.example/carreiras",
        encoding="utf-8",
        body=b"""<article itemtype="https://schema.org/JobPosting">
          <a itemprop="url" href="/123?utm_source=email">Analista</a>
          <a itemprop="url" href="/123?utm_source=feed">Analista</a>
        </article><a href="/jobs/123/apply">Vaga</a>
        <script>const links = ["/jobs/123/formulario-curriculo"];</script>""",
    )
    assert [c.url for c in AdaptadorGenericoHTML().descobrir(resposta)] == [
        "https://empresa.example/123"
    ]


def test_normalizacao_preserva_identidade_paginacao_e_codifica_acentos():
    assert (
        normalizar_url_vaga(
            "https://empresa.example/vaga/técnico?id=12&page=2&utm_medium=email#descricao"
        )
        == "https://empresa.example/vaga/t%C3%A9cnico?id=12&page=2"
    )
    assert normalizar_url_vaga("https://empresa.example/jobs?id=13") != normalizar_url_vaga(
        "https://empresa.example/jobs?id=12"
    )


def test_relatorio_distingue_http_erro_limite_e_rede(tmp_path):
    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica\n"
        "teste,Empresa,outra,https://empresa.example/jobs,true,10,somente_coleta\n",
        encoding="utf-8",
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo))
    urls = [f"https://empresa.example/jobs/{i}" for i in range(5)]
    spider.urls_agendadas["teste"] = set(urls[:4])
    spider.detalhes_descobertos["teste"] = set(urls)
    spider.resultados_download["teste"] = {
        urls[0]: {"status_http": 200},
        urls[1]: {"status_http": 404},
    }
    spider.tratar_falha_download(
        SimpleNamespace(
            request=Request(urls[2], meta={"observatorio_alvo_id": "teste", "retry_times": 2}),
            value=TimeoutError("timeout"),
        )
    )
    resumo = spider.resumir_cobertura("finished")["fontes"][0]
    assert resumo["candidatos_unicos"] == 5
    assert resumo["detalhes_http_ok"] == 1
    assert resumo["candidatos_nao_agendados"] == [urls[4]]
    assert resumo["agendadas_sem_resposta"] == [urls[3]]
    assert set(resumo["erros"]) == {urls[1], urls[2]}
    assert resumo["erros"][urls[2]]["tentativas"] == 3


def test_relatorio_mostra_se_a_listagem_foi_lida_inteira(tmp_path):
    """Base da detecção de vagas removidas: listagem perdida não pode sumir do relatório."""

    from scrapy.exceptions import IgnoreRequest

    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica\n"
        "teste,Empresa,outra,https://empresa.example/jobs,true,10,somente_coleta\n",
        encoding="utf-8",
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo))
    paginas = [f"https://empresa.example/jobs?page={i}" for i in range(1, 5)]
    spider.urls_agendadas["teste"] = set(paginas)
    spider.urls_listagem_agendadas["teste"] = set(paginas)
    # Página 1 respondeu depois de redirecionar; página 2 respondeu direto.
    spider.listagens_respondidas["teste"] = {paginas[0], paginas[1]}
    spider.resultados_download["teste"] = {paginas[1]: {"status_http": 200}}
    # Página 3 foi descartada (robots, bloqueio do domínio); página 4 nunca respondeu.
    spider.tratar_falha_download(
        SimpleNamespace(
            request=Request(
                paginas[2],
                meta={"observatorio_alvo_id": "teste", "observatorio_tipo_pagina": "inicial"},
            ),
            value=IgnoreRequest("robots"),
        )
    )
    spider.listagens_inalteradas["teste"] += 1
    spider.motivos_fim_navegacao["teste"] = {"limite_atingido"}

    resumo = spider.resumir_cobertura("finished")["fontes"][0]

    assert resumo["listagens_sem_resposta"] == 2
    assert resumo["listagens_descartadas"] == 1
    assert resumo["listagens_inalteradas"] == 1
    assert resumo["motivos_fim_navegacao"] == ["limite_atingido"]
    assert resumo["janela_horas"] is None


def test_diagnostico_separa_bloqueio_de_pagina_sem_vagas():
    bloqueio = _diagnosticar_fonte(
        resultados={
            "https://empresa.example/carreiras": {
                "tipo_pagina": "inicial",
                "status_http": 403,
            }
        },
        candidatos=0,
        detalhes_http_ok=0,
        candidatos_nao_agendados=0,
    )
    assert bloqueio["diagnostico"] == "acesso_restrito_ou_rate_limit"

    vazio = _diagnosticar_fonte(
        resultados={
            "https://empresa.example/carreiras": {
                "tipo_pagina": "inicial",
                "status_http": 200,
                "indicios_html": {"scripts": 0, "raiz_spa": False},
            }
        },
        candidatos=0,
        detalhes_http_ok=0,
        candidatos_nao_agendados=0,
    )
    assert vazio["diagnostico"] == "nenhum_link_de_vaga_reconhecido"


def test_diagnostico_aponta_possivel_dependencia_javascript():
    diagnostico = _diagnosticar_fonte(
        resultados={
            "https://empresa.example/carreiras": {
                "tipo_pagina": "inicial",
                "status_http": 200,
                "indicios_html": {"scripts": 5, "raiz_spa": True},
            }
        },
        candidatos=0,
        detalhes_http_ok=0,
        candidatos_nao_agendados=0,
    )
    assert diagnostico["diagnostico"] == "possivel_javascript_ou_adaptador"
