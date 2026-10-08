"""Testes dos adaptadores de descoberta por plataforma."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, Response, TextResponse

from observatorio_vagas.crawling.adaptadores import selecionar_adaptador
from observatorio_vagas.domain.enums import Fonte


def _criar_resposta(
    corpo: str,
    *,
    url: str = "https://empresa.gupy.io/",
) -> HtmlResponse:
    """Cria uma página HTML sem acessar a internet."""

    return HtmlResponse(
        url=url,
        status=200,
        body=corpo.encode(),
        encoding="utf-8",
        headers={
            b"Content-Type": b"text/html; charset=utf-8",
        },
    )


def _criar_next_data(
    vagas: list[object],
) -> str:
    """Monta o estado público mínimo utilizado pela Gupy."""

    dados = {
        "props": {
            "pageProps": {
                "jobs": vagas,
            }
        }
    }

    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(dados)}"
        "</script></html>"
    )


def test_registro_seleciona_gupy_e_fallback_generico() -> None:
    """A plataforma conhecida usa integração própria e as demais usam HTML."""

    assert selecionar_adaptador(Fonte.GUPY).nome == "gupy"
    assert selecionar_adaptador(Fonte.PANDAPE).nome == "pandape"
    assert selecionar_adaptador(Fonte.CKAN).nome == "ckan_dados_abertos"
    assert selecionar_adaptador(Fonte.QUERIDO_DIARIO).nome == "querido_diario_api"
    assert selecionar_adaptador(Fonte.OUTRA).nome == "geral_html"
    assert selecionar_adaptador(Fonte.PAGINA_CARREIRAS).nome == "empresa_direta_html"


def test_ckan_seleciona_somente_csv_do_ano_mais_recente() -> None:
    """Séries anuais não devem reler arquivos históricos diariamente."""

    url = "https://dados.ufpe.br/api/3/action/package_show?id=concursos"
    resposta = TextResponse(
        url=url,
        status=200,
        body=json.dumps(
            {
                "success": True,
                "result": {
                    "resources": [
                        {
                            "name": "dicionario.pdf",
                            "format": "PDF",
                            "url": "https://dados.ufpe.br/dicionario.pdf",
                        },
                        {
                            "name": "concursos-2025-ufpe.csv",
                            "format": "CSV",
                            "state": "active",
                            "url": "https://dados.ufpe.br/concursos-2025.csv",
                        },
                        {
                            "name": "concursos-2026-ufpe.csv",
                            "format": "CSV",
                            "state": "active",
                            "url": "https://dados.ufpe.br/concursos-2026.csv",
                        },
                    ]
                },
            }
        ).encode(),
        encoding="utf-8",
        headers={b"Content-Type": b"application/json"},
    )

    candidatos = selecionar_adaptador(Fonte.CKAN).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://dados.ufpe.br/concursos-2026.csv"
    ]
    assert candidatos[0].evidencias == ("recurso_csv_ckan",)


def test_ckan_preserva_varios_csvs_sem_ano() -> None:
    """Um CSV por posto de atendimento precisa ser coletado por inteiro."""

    resposta = TextResponse(
        url="https://dados.es.gov.br/api/3/action/package_show?id=vagas",
        body=json.dumps(
            {
                "success": True,
                "result": {
                    "resources": [
                        {
                            "name": "Vagas Anchieta",
                            "format": "CSV",
                            "url": "https://dados.es.gov.br/anchieta.csv",
                        },
                        {
                            "name": "Vagas Linhares",
                            "format": "CSV",
                            "url": "https://dados.es.gov.br/linhares.csv",
                        },
                    ]
                },
            }
        ).encode(),
        encoding="utf-8",
    )

    candidatos = selecionar_adaptador(Fonte.CKAN).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://dados.es.gov.br/anchieta.csv",
        "https://dados.es.gov.br/linhares.csv",
    ]


def test_querido_diario_processa_o_json_inicial_sem_seguir_arquivos() -> None:
    """A API é autocontida e não deve liberar domínios externos."""

    resposta = Response(
        url="https://api.queridodiario.org.br/gazettes",
        status=200,
        body=b'{"gazettes": []}',
        headers={b"Content-Type": b"application/json"},
    )

    adaptador = selecionar_adaptador(Fonte.QUERIDO_DIARIO)

    assert adaptador.descobrir(resposta) == ()


def test_generico_descobre_detalhes_e_paginacao_de_json_publico() -> None:
    resposta = TextResponse(
        url="https://empresa.example/api/jobs",
        body=json.dumps(
            {
                "items": [
                    {"jobTitle": "Pessoa Desenvolvedora", "detailUrl": "/jobs/123"},
                    {"name": "Página institucional", "url": "/sobre"},
                ],
                "nextPageUrl": "/api/jobs?cursor=abc",
            }
        ).encode(),
        encoding="utf-8",
        headers={b"Content-Type": b"application/json"},
    )

    candidatos = selecionar_adaptador(Fonte.OUTRA).descobrir(resposta)

    assert [(c.url, c.evidencias) for c in candidatos] == [
        ("https://empresa.example/jobs/123", ("url_de_detalhe_em_json",)),
        ("https://empresa.example/api/jobs?cursor=abc", ("paginacao_json",)),
    ]


def test_empresa_direta_dhl_aceita_somente_detalhe_publico() -> None:
    resposta = _criar_resposta(
        """
        <a href="/amer/pt/home">Página inicial</a>
        <a href="/amer/pt/job/Sao-Paulo/Analista-Logistica/12345">Analista</a>
        <a href="https://externa.example/job/123">Ignorar</a>
        """,
        url="https://careers.dhl.com/amer/pt/home",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(c.url, c.evidencias) for c in candidatos] == [
        (
            "https://careers.dhl.com/amer/pt/job/Sao-Paulo/Analista-Logistica/12345",
            ("detalhe_vaga_dhl_publico",),
        )
    ]


def test_empresa_direta_ceva_isola_vagas_do_quadro_ceva() -> None:
    resposta = _criar_resposta(
        """
        <a href="/CEVALogistics/job/Sao-Paulo/Analista-Logistico/1414550733/">CEVA</a>
        <a href="/CMA-CGM/job/Sao-Paulo/Analista/1414550733/">Outra empresa</a>
        <a href="/CEVALogistics/search/">Busca</a>
        """,
        url="https://jobs.cmacgm-group.com/CEVALogistics/?locale=pt_BR",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(c.url, c.evidencias) for c in candidatos] == [
        (
            "https://jobs.cmacgm-group.com/CEVALogistics/job/Sao-Paulo/Analista-Logistico/1414550733/",
            ("detalhe_vaga_ceva_publico",),
        )
    ]


def test_adaptadores_massivos_aceitam_apenas_rotas_publicas_de_detalhe() -> None:
    """Cargill, Nestlé, Deere e Caterpillar não devem seguir links de menu."""

    casos = [
        (
            "https://careers.cargill.com/pt-br/busca-de-vagas?country=Brazil",
            '<a href="/pt-br/vaga/Primavera/Analista-II/31240/100995176592">Analista</a>'
            '<a href="/pt-br/sobre">Sobre</a>',
            "https://careers.cargill.com/pt-br/vaga/Primavera/Analista-II/31240/100995176592",
            "detalhe_vaga_cargill_publico",
        ),
        (
            "https://www.nestle.com.br/jobs/search-jobs",
            '<a href="https://jobdetails.nestle.com/job/Rio-de-Janeiro-Vaga/857853701/?feedId=256801">Vaga</a>'
            '<a href="https://jobdetails.nestle.com/career">Ignorar</a>',
            "https://jobdetails.nestle.com/job/Rio-de-Janeiro-Vaga/857853701/?feedId=256801",
            "detalhe_vaga_nestle_publico",
        ),
        (
            "https://jobs.deere.com/search",
            '<a href="/eightfold/job/Indaiatuba-Analista-SP/1434674700/">Analista</a>'
            '<a href="/search?startrow=25">2</a>',
            "https://jobs.deere.com/eightfold/job/Indaiatuba-Analista-SP/1434674700/",
            "detalhe_vaga_john_deere_publico",
        ),
        (
            "https://careers.caterpillar.com/pt/empregos/",
            '<a href="/pt/empregos/r0000386930/product-service-consultant/">Consultor</a>'
            '<a href="/pt/empregos/?page=2">2</a>',
            "https://careers.caterpillar.com/pt/empregos/r0000386930/product-service-consultant/",
            "detalhe_vaga_caterpillar_publico",
        ),
    ]

    for url, corpo, esperado, evidencia in casos:
        candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(
            _criar_resposta(corpo, url=url)
        )
        assert any(candidato.url == esperado and candidato.evidencias == (evidencia,) for candidato in candidatos)


def test_adaptador_basf_segue_apenas_quadro_successfactors_indicado() -> None:
    resposta = _criar_resposta(
        '<a href="https://career5.successfactors.eu/career?company=C0000159936P">Vagas</a>'
        '<a href="https://career5.successfactors.eu/career?company=outra">Ignorar</a>',
        url="https://www.basf.com/br/pt/careers/jobs",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(c.url, c.evidencias) for c in candidatos] == [
        (
            "https://career5.successfactors.eu/career?company=C0000159936P",
            ("portal_basf_successfactors",),
        )
    ]


def test_adaptadores_bunge_schneider_e_honeywell_isolam_detalhes_publicos() -> None:
    """Os três portais não podem transformar navegação ou login em anúncio."""

    casos = [
        (
            "https://jobs.bunge.com/viewalljobs/?locale=pt_BR",
            '<a href="/job/Analista-de-Dados-Curitiba/1417438333/">Analista</a>'
            '<a href="/viewalljobs/?locale=pt_BR&startrow=50">2</a>',
            [
                (
                    "https://jobs.bunge.com/job/Analista-de-Dados-Curitiba/1417438333/",
                    ("detalhe_vaga_bunge_publico",),
                ),
                (
                    "https://jobs.bunge.com/viewalljobs/?locale=pt_BR&startrow=50",
                    ("paginacao_bunge",),
                ),
            ],
        ),
        (
            "https://careers.se.com/jobs?lang=pt-BR",
            '<a href="/jobs/133824?lang=pt-br">Consultor</a>'
            '<a href="/brazil?lang=pt-BR">Brasil</a>',
            [
                (
                    "https://careers.se.com/jobs/133824?lang=pt-br",
                    ("detalhe_vaga_schneider_publico",),
                )
            ],
        ),
        (
            "https://careers.honeywell.com/en/sites/Honeywell/jobs",
            '<a href="/en/sites/Honeywell/job/158000/">Engenheira</a>'
            '<a href="/en/sites/Honeywell/login">Login</a>',
            [
                (
                    "https://careers.honeywell.com/en/sites/Honeywell/job/158000/",
                    ("detalhe_vaga_honeywell_publico",),
                )
            ],
        ),
    ]

    for url, corpo, esperados in casos:
        candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(
            _criar_resposta(corpo, url=url)
        )
        assert [(c.url, c.evidencias) for c in candidatos] == esperados


def test_adaptador_tetra_pak_separa_detalhe_publico_de_navegacao() -> None:
    resposta = _criar_resposta(
        '<a href="/job/Monte-Mor-Aprendiz-SP/100596-pt_BR/">Aprendiz</a>'
        '<a href="/viewalljobs/?locale=pt_BR&startrow=20">2</a>'
        '<a href="/content/sobre">Sobre</a>',
        url="https://jobs.tetrapak.com/viewalljobs/?locale=pt_BR",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(c.url, c.evidencias) for c in candidatos] == [
        (
            "https://jobs.tetrapak.com/job/Monte-Mor-Aprendiz-SP/100596-pt_BR/",
            ("detalhe_vaga_tetra_pak_publico",),
        ),
        (
            "https://jobs.tetrapak.com/viewalljobs/?locale=pt_BR&startrow=20",
            ("paginacao_tetra_pak",),
        ),
    ]


def test_querido_diario_pagina_busca_sem_seguir_cdn_externa() -> None:
    """O volume cresce por offset e continua no domínio autorizado."""

    url = "https://api.queridodiario.org.br/gazettes?querystring=processo+seletivo&size=100"
    requisicao = Request(
        url,
        meta={"observatorio_limite_paginas": 4},
    )
    resposta = TextResponse(
        url=url,
        status=200,
        body=json.dumps(
            {
                "total_gazettes": 950,
                "gazettes": [
                    {
                        "url": "https://cdn.externa.example/diario.pdf",
                        "txt_url": "https://cdn.externa.example/diario.txt",
                    }
                ],
            }
        ).encode(),
        encoding="utf-8",
        headers={b"Content-Type": b"application/json"},
        request=requisicao,
    )

    candidatos = selecionar_adaptador(Fonte.QUERIDO_DIARIO).descobrir(resposta)

    assert len(candidatos) == 1
    assert [candidato.url.split("offset=")[1] for candidato in candidatos] == ["100"]
    assert all(
        candidato.url.startswith("https://api.queridodiario.org.br/gazettes?")
        for candidato in candidatos
    )
    assert all("cdn.externa.example" not in candidato.url for candidato in candidatos)
    assert candidatos[0].evidencias == ("paginacao_querido_diario",)


def test_querido_diario_preserva_todos_os_municipios_na_paginacao() -> None:
    """Cada página mantém os códigos IBGE repetidos da busca original."""

    url = (
        "https://api.queridodiario.org.br/gazettes?"
        "territory_ids=3550308&territory_ids=3304557&size=50"
    )
    requisicao = Request(
        url,
        meta={"observatorio_limite_paginas": 2},
    )
    resposta = TextResponse(
        url=url,
        status=200,
        body=b'{"total_gazettes": 80, "gazettes": [{}]}',
        encoding="utf-8",
        request=requisicao,
    )

    candidatos = selecionar_adaptador(Fonte.QUERIDO_DIARIO).descobrir(resposta)

    assert len(candidatos) == 1
    parametros = parse_qs(urlsplit(candidatos[0].url).query)
    assert parametros["territory_ids"] == ["3550308", "3304557"]
    assert parametros["offset"] == ["50"]


def test_registro_rejeita_fonte_fora_do_enum() -> None:
    """Uma chave arbitrária não pode selecionar código dinamicamente."""

    with pytest.raises(
        TypeError,
        match="enum Fonte",
    ):
        selecionar_adaptador("gupy")  # type: ignore[arg-type]


def test_gupy_descobre_vagas_somente_no_next_data() -> None:
    """A listagem estruturada funciona mesmo quando não há âncoras HTML."""

    resposta = _criar_resposta(
        _criar_next_data(
            [
                {
                    "id": 123,
                    "title": "Pessoa Desenvolvedora Backend",
                },
                {
                    "id": "456",
                    "title": "Analista de Dados",
                },
            ]
        )
    )

    candidatos = selecionar_adaptador(Fonte.GUPY).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.gupy.io/jobs/123",
        "https://empresa.gupy.io/jobs/456",
    ]
    assert candidatos[0].texto == "Pessoa Desenvolvedora Backend"
    assert candidatos[0].evidencias == ("gupy_next_data",)


def test_gupy_une_next_data_e_html_sem_repetir_vaga() -> None:
    """O HTML completa o JSON, mas parâmetros não duplicam o mesmo ID."""

    dados = {
        "props": {
            "pageProps": {
                "jobs": [
                    {
                        "id": 123,
                        "title": "Vaga do JSON",
                    }
                ]
            }
        }
    }
    corpo = f"""
    <html>
      <script id="__NEXT_DATA__" type="application/json">
        {json.dumps(dados)}
      </script>
      <a href="/jobs/123?jobBoardSource=gupy_public_page">Repetida</a>
      <a href="/jobs/789">Título sem a palavra vaga</a>
    </html>
    """

    candidatos = selecionar_adaptador(Fonte.GUPY).descobrir(
        _criar_resposta(corpo),
    )

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.gupy.io/jobs/123",
        "https://empresa.gupy.io/jobs/789",
    ]
    assert candidatos[1].evidencias == ("padrao_url_gupy",)


def test_gupy_ignora_ids_invalidos_sem_interromper_fallback() -> None:
    """JSON alterado ou malicioso não derruba a descoberta pelo HTML."""

    corpo = _criar_next_data(
        [
            {"id": True, "title": "Booleano"},
            {"id": "123/../../segredo", "title": "Injeção"},
            {"id": None, "title": "Ausente"},
        ]
    ).replace(
        "</html>",
        '<a href="/jobs/900">Oportunidade pública</a></html>',
    )

    candidatos = selecionar_adaptador(Fonte.GUPY).descobrir(
        _criar_resposta(corpo),
    )

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.gupy.io/jobs/900",
    ]


def test_gupy_json_malformado_nao_interrompe_links_html() -> None:
    """Uma mudança no Next.js deve degradar para o HTML já renderizado."""

    resposta = _criar_resposta(
        """
        <html>
          <script id="__NEXT_DATA__" type="application/json">{invalido</script>
          <a href="/jobs/321">Abrir posição</a>
        </html>
        """
    )

    candidatos = selecionar_adaptador(Fonte.GUPY).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.gupy.io/jobs/321",
    ]


def test_gupy_resposta_binaria_retorna_vazio() -> None:
    """O adaptador não tenta interpretar arquivos binários como listagem."""

    resposta = Response(
        url="https://empresa.gupy.io/arquivo",
        body=b"binario",
    )

    assert selecionar_adaptador(Fonte.GUPY).descobrir(resposta) == ()


def test_pandape_descobre_detalhes_sem_depender_do_texto() -> None:
    """O identificador na URL basta para reconhecer uma vaga Pandapé."""

    resposta = _criar_resposta(
        """
        <html>
          <a class="card-vacancy" href="/Detail/3677979">
            Especialista de Ensino II
          </a>
          <a class="card-vacancy" href="/Detail/3677667">Auxiliar</a>
        </html>
        """,
        url="https://fiesc.pandape.infojobs.com.br/",
    )

    candidatos = selecionar_adaptador(Fonte.PANDAPE).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://fiesc.pandape.infojobs.com.br/Detail/3677979",
        "https://fiesc.pandape.infojobs.com.br/Detail/3677667",
    ]
    assert candidatos[0].texto == "Especialista de Ensino II"
    assert candidatos[0].evidencias == ("padrao_url_pandape",)


def test_pandape_canonicaliza_e_elimina_repeticoes() -> None:
    """Parâmetros, fragmentos e caixa não identificam outra vaga."""

    resposta = _criar_resposta(
        """
        <html>
          <a href="/detail/123?origem=portal">Primeira</a>
          <a href="/Detail/123#descricao">Repetida</a>
          <a href="https://externa.example/Detail/999">Externa</a>
          <a href="/Apply/123">Candidatura</a>
          <a href="/Detail/invalido">Inválida</a>
        </html>
        """,
        url="https://empresa.pandape.infojobs.com.br/",
    )

    candidatos = selecionar_adaptador(Fonte.PANDAPE).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.pandape.infojobs.com.br/Detail/123",
    ]


def test_pandape_preserva_fallback_html_seguro() -> None:
    """Uma rota futura ainda pode ser reconhecida pelas regras genéricas."""

    resposta = _criar_resposta(
        '<html><a href="/oportunidades/42">Ver oportunidade</a></html>',
        url="https://empresa.pandape.infojobs.com.br/",
    )

    candidatos = selecionar_adaptador(Fonte.PANDAPE).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.pandape.infojobs.com.br/oportunidades/42",
    ]


def test_pandape_resposta_binaria_retorna_vazio() -> None:
    """O adaptador não interpreta arquivos binários como listagem."""

    resposta = Response(
        url="https://empresa.pandape.infojobs.com.br/arquivo",
        body=b"binario",
    )

    assert selecionar_adaptador(Fonte.PANDAPE).descobrir(resposta) == ()


def test_adaptador_generico_preserva_descoberta_existente() -> None:
    """Sites desconhecidos continuam usando as regras conservadoras antigas."""

    resposta = _criar_resposta(
        '<html><a href="/oportunidades/42">Ver oportunidade</a></html>',
        url="https://empresa.example/carreiras",
    )

    candidatos = selecionar_adaptador(Fonte.OUTRA).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/oportunidades/42",
    ]


def test_adaptador_generico_descobre_rotas_filhas_da_pagina_de_carreiras() -> None:
    """Cargos podem estar em /trabalhe-conosco sem usar a palavra vaga."""

    resposta = _criar_resposta(
        """
        <html>
          <a href="/trabalhe-conosco/coordenacao-de-projetos/">
            Coordenação de Projetos
          </a>
          <a href="/trabalhe-conosco/consultoria-em-seguranca/">
            Consultoria em Segurança
          </a>
          <a href="/sobre/">Sobre</a>
        </html>
        """,
        url="https://empresa.example/trabalhe-conosco/",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/trabalhe-conosco/coordenacao-de-projetos/",
        "https://empresa.example/trabalhe-conosco/consultoria-em-seguranca/",
    ]
    assert all(
        candidato.evidencias == ("rota_filha_da_pagina_de_carreiras",) for candidato in candidatos
    )


def test_pagina_carreiras_descobre_detalhes_lever_por_id() -> None:
    """Cargos do Lever não precisam conter a palavra vaga no título."""

    resposta = _criar_resposta(
        """
        <html>
          <a href="/flashapp/11111111-1111-1111-1111-111111111111">Apply</a>
          <a href="/flashapp/11111111-1111-1111-1111-111111111111">
            Analista de Dados Pleno
          </a>
          <a href="/flashapp">Início</a>
          <a href="/outraempresa/22222222-2222-2222-2222-222222222222">Externa</a>
        </html>
        """,
        url="https://jobs.lever.co/flashapp",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.texto, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://jobs.lever.co/flashapp/11111111-1111-1111-1111-111111111111",
            "Analista de Dados Pleno",
            ("padrao_url_lever",),
        )
    ]


def test_adaptador_generico_descobre_detalhes_publicos_no_json_ld() -> None:
    """Uma lista estruturada deve revelar cada URL de vaga no mesmo domínio."""

    resposta = _criar_resposta(
        """
        <html>
          <script type="application/ld+json">
            {
              "@graph": [
                {
                  "@type": "JobPosting",
                  "title": "Analista de Dados",
                  "url": "/vagas/analista-de-dados"
                },
                {
                  "@type": "JobPosting",
                  "title": "Link externo não permitido",
                  "url": "https://externa.example/vagas/1"
                }
              ]
            }
          </script>
        </html>
        """,
        url="https://empresa.example/carreiras",
    )

    candidatos = selecionar_adaptador(Fonte.OUTRA).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/vagas/analista-de-dados",
    ]
    assert candidatos[0].texto == "Analista de Dados"
    assert candidatos[0].evidencias == ("job_posting_json_ld",)


def test_adaptador_generico_descobre_url_de_vaga_no_estado_javascript() -> None:
    """Uma aplicação cliente pode expor detalhes antes de renderizar os cards."""

    resposta = _criar_resposta(
        """
        <html>
          <script>
            window.__ESTADO__ = {
              "jobs": [
                {"detailUrl": "\\/vagas\\/analista-de-dados"},
                {"detailUrl": "/jobs/engenheira-backend"}
              ],
              "imagem": "/assets/vagas.png",
              "externa": "https://externa.example/jobs/nao-seguir"
            };
          </script>
        </html>
        """,
        url="https://empresa.example/carreiras",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/vagas/analista-de-dados",
        "https://empresa.example/jobs/engenheira-backend",
    ]
    assert all(candidato.evidencias == ("url_de_detalhe_em_script",) for candidato in candidatos)


def test_adaptador_abler_separa_apenas_vagas_do_mesmo_locatario() -> None:
    resposta = _criar_resposta(
        """
        <a href="?slug=analista-de-dados-123">Analista de Dados</a>
        <a href="/jobs/outra-consultoria?slug=nao-seguir">Outra empresa</a>
        <a href="?pagina=2">Próxima</a>
        """,
        url="https://ats.abler.com.br/jobs/rhnossa",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://ats.abler.com.br/jobs/rhnossa?slug=analista-de-dados-123",
            ("detalhe_vaga_abler",),
        ),
    ]


def test_adaptador_abler_usa_api_publica_quando_html_nao_tem_cards() -> None:
    resposta = _criar_resposta(
        "<div id='__nuxt'></div>",
        url="https://ats.abler.com.br/jobs/goldenrh",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://hulk-smash.abler.com.br/api/company/v1/careers_pages/goldenrh/vacancies?per_page=100&page=1",
            ("listagem_abler_api",),
        ),
    ]


def test_adaptador_abler_avanca_paginacao_indicada_pela_api() -> None:
    resposta = _criar_resposta(
        '{"data": [], "meta": {"page": 1, "next": 2}}',
        url="https://hulk-smash.abler.com.br/api/company/v1/careers_pages/goldenrh/vacancies?per_page=100&page=1",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://hulk-smash.abler.com.br/api/company/v1/careers_pages/goldenrh/vacancies?per_page=100&page=2",
            ("paginacao_abler_api",),
        ),
    ]


def test_adaptador_randstad_classifica_paginas_publicas_como_navegacao() -> None:
    resposta = _criar_resposta(
        """
        <a href="/vagas/auxiliar-de-logistica_cajamar_123/">Vaga de logística</a>
        <script>const pagina = "/vagas/page-2/";</script>
        """,
        url="https://www.randstad.com.br/vagas/",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    evidencias_por_url = {candidato.url: candidato.evidencias for candidato in candidatos}
    assert evidencias_por_url["https://www.randstad.com.br/vagas/page-2/"] == (
        "paginacao_randstad",
    )


def test_adaptador_smartrecruiters_consulta_a_listagem_publica_da_empresa() -> None:
    resposta = _criar_resposta(
        "<div id='root'></div>",
        url="https://jobs.smartrecruiters.com/BoschGroup",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100&offset=0",
            ("listagem_smartrecruiters_api",),
        ),
    ]


def test_adaptador_smartrecruiters_cria_detalhes_e_pagina_a_api_publica() -> None:
    resposta = TextResponse(
        url="https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=2&offset=0",
        status=200,
        body=json.dumps(
            {
                "limit": 2,
                "offset": 0,
                "totalFound": 3,
                "content": [
                    {"id": "744000123", "name": "Analista de Dados"},
                    {"uuid": "abc-123", "name": "Técnica de Produção"},
                ],
            }
        ).encode(),
        encoding="utf-8",
        headers={b"Content-Type": b"application/json"},
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://jobs.smartrecruiters.com/BoschGroup/744000123",
            ("detalhe_smartrecruiters_api",),
        ),
        (
            "https://jobs.smartrecruiters.com/BoschGroup/abc-123",
            ("detalhe_smartrecruiters_api",),
        ),
        (
            "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=2&offset=2",
            ("paginacao_smartrecruiters_api",),
        ),
    ]


def test_adaptador_bradesco_segue_apenas_o_portal_csod_oficial() -> None:
    resposta = _criar_resposta(
        """
        <a href="https://bradesco.csod.com/ux/ats/careersite/1/home?c=bradesco">
          Encontrar vagas
        </a>
        <a href="https://outra.csod.com/ux/ats/careersite/1/home?c=outra">Ignorar</a>
        """,
        url="https://banco.bradesco/carreiras/index.shtm",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://bradesco.csod.com/ux/ats/careersite/1/home?c=bradesco",
            ("portal_csod_bradesco",),
        ),
    ]


def test_adaptador_csod_bradesco_reconhece_detalhe_publico() -> None:
    resposta = _criar_resposta(
        """
        <a href="/ux/ats/careersite/1/home/requisition/84642?c=bradesco">
          Analista de Dados
        </a>
        <a href="/ux/ats/careersite/1/home/requisition/99?c=outro">Ignorar</a>
        """,
        url="https://bradesco.csod.com/ux/ats/careersite/1/home?c=bradesco",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://bradesco.csod.com/ux/ats/careersite/1/home/requisition/84642?c=bradesco",
            ("detalhe_vaga_csod_bradesco",),
        ),
    ]


def test_adaptador_sicoob_segue_quadro_empregare_e_pagina_vagas() -> None:
    portal = _criar_resposta(
        '<a href="https://sicoob.empregare.com/pt-br/vagas">Confira vagas</a>',
        url="https://www.sicoob.com.br/web/sicoob/trabalhe-conosco",
    )
    listagem = _criar_resposta(
        """
        <a href="/pt-br/vaga-gerente-de-relacionamento_182248">Gerente</a>
        <a href="/pt-br/vagas?pagina=2">2</a>
        """,
        url="https://sicoob.empregare.com/pt-br/vagas",
    )

    candidatos_portal = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(portal)
    candidatos_listagem = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(listagem)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos_portal] == [
        (
            "https://sicoob.empregare.com/pt-br/vagas",
            ("portal_empregare_sicoob",),
        ),
    ]
    assert [(candidato.url, candidato.evidencias) for candidato in candidatos_listagem] == [
        (
            "https://sicoob.empregare.com/pt-br/vaga-gerente-de-relacionamento_182248",
            ("detalhe_vaga_empregare_sicoob",),
        ),
        (
            "https://sicoob.empregare.com/pt-br/vagas?pagina=2",
            ("paginacao_empregare_sicoob",),
        ),
    ]


def test_adaptador_larsil_nao_transforma_formulario_generico_em_vaga() -> None:
    resposta = _criar_resposta(
        """
        <form action="/candidatura"><select name="vaga"><option>Vaga escolhida</option></select></form>
        <a href="/vaga/operador-de-maquinas">Operador de máquinas</a>
        """,
        url="https://vagas.larsil.com.br/",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://vagas.larsil.com.br/vaga/operador-de-maquinas",
            ("detalhe_vaga_larsil",),
        ),
    ]


def test_adaptador_portal_lg_reconhece_somente_detalhe_com_codigo() -> None:
    resposta = _criar_resposta(
        """
        <a href="/Vagas/c/abc/p/portaldevagas/pt-BR/Vaga/Divulgacao?codigo=abc123">
          Analista Administrativo
        </a>
        <a href="/Vagas/c/abc/p/portaldevagas/pt-BR/Busca/Vagas">Listagem</a>
        """,
        url="https://prd-pc1.lg.com.br/Vagas/c/abc/p/portaldevagas/pt-BR/Busca/Vagas",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://prd-pc1.lg.com.br/Vagas/c/abc/p/portaldevagas/pt-BR/Vaga/Divulgacao?codigo=abc123",
            ("detalhe_vaga_portal_lg",),
        ),
    ]


def test_adaptador_workday_consulta_listagem_publica_cxs() -> None:
    resposta = _criar_resposta(
        "<div id='root'></div>",
        url="https://alliancewd.wd3.myworkdayjobs.com/pt-BR/renault-group-careers",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://alliancewd.wd3.myworkdayjobs.com/wday/cxs/alliancewd/renault-group-careers/jobs?limit=20&offset=0",
            ("listagem_workday_cxs",),
        ),
    ]


def test_adaptador_workday_cria_detalhes_e_paginacao() -> None:
    resposta = TextResponse(
        url=(
            "https://alliancewd.wd3.myworkdayjobs.com/wday/cxs/alliancewd/"
            "renault-group-careers/jobs?limit=2&offset=0"
        ),
        status=200,
        body=json.dumps(
            {
                "total": 3,
                "jobPostings": [
                    {"title": "Analista de Dados", "externalPath": "/job/Curitiba/Analista_JR1"},
                    {"title": "Técnico", "externalPath": "/job/Sao-Paulo/Tecnico_JR2"},
                ],
            }
        ).encode(),
        encoding="utf-8",
        headers={b"Content-Type": b"application/json"},
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos] == [
        (
            "https://alliancewd.wd3.myworkdayjobs.com/wday/cxs/alliancewd/renault-group-careers/job/Curitiba/Analista_JR1",
            ("detalhe_vaga_workday_cxs",),
        ),
        (
            "https://alliancewd.wd3.myworkdayjobs.com/wday/cxs/alliancewd/renault-group-careers/job/Sao-Paulo/Tecnico_JR2",
            ("detalhe_vaga_workday_cxs",),
        ),
        (
            "https://alliancewd.wd3.myworkdayjobs.com/wday/cxs/alliancewd/renault-group-careers/jobs?limit=2&offset=2",
            ("paginacao_workday_cxs",),
        ),
    ]


def test_descobre_endpoint_json_publico_de_vagas_no_estado_inicial() -> None:
    from observatorio_vagas.crawling.adaptadores.generico import descobrir_endpoints_json_publicos

    resposta = _criar_resposta(
        """
        <script>
          window.__CAREERS__ = { jobsApi: "/api/jobs/openings", analytics: "/api/metrics" };
        </script>
        """,
        url="https://empresa.example/carreiras",
    )

    assert descobrir_endpoints_json_publicos(resposta) == (
        "https://empresa.example/api/jobs/openings",
    )


def test_adaptador_generico_descobre_card_com_data_url() -> None:
    """Cards clicáveis também podem expor a URL de detalhe publicamente."""

    resposta = _criar_resposta(
        """
        <html>
          <article data-url="/vagas/engenheira-de-dados">
            <h2>Engenheira de Dados</h2>
          </article>
          <div data-href="/sobre">Sobre a empresa</div>
          <div data-url="https://externa.example/jobs/nao-seguir">Externa</div>
        </html>
        """,
        url="https://empresa.example/carreiras",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/vagas/engenheira-de-dados",
    ]
    assert candidatos[0].evidencias == ("url_de_detalhe_em_data_atributo",)


def test_adaptador_generico_descobre_card_com_onclick_literal() -> None:
    """A URL declarada pelo card é lida sem executar JavaScript."""

    resposta = _criar_resposta(
        """
        <html>
          <article onclick="window.location.href='/jobs/analista-de-seguranca'">
            Analista de Segurança
          </article>
          <article onclick="window.open('/vagas/desenvolvedora-python')">
            Desenvolvedora Python
          </article>
          <article onclick="executarCodigoDinamico()">Ignorar</article>
        </html>
        """,
        url="https://empresa.example/carreiras",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/jobs/analista-de-seguranca",
        "https://empresa.example/vagas/desenvolvedora-python",
    ]
    assert all(candidato.evidencias == ("url_de_detalhe_em_onclick",) for candidato in candidatos)


def test_adaptador_generico_decodifica_estado_publico_e_novas_rotas_de_card() -> None:
    url_serializada = (
        r"https\u003A\u002F\u002Fempresa.example\u002Fjobs\u002F42"
        r"\u003Farea\u003Ddados"
    )
    resposta = _criar_resposta(
        f"""
        <html>
          <script>
            window.__ESTADO__ = {{"url": "{url_serializada}"}};
          </script>
          <article data-job-detail-url="/vagas/43">Pessoa Analista</article>
          <article onclick="router.push('/jobs/44')">Pessoa Desenvolvedora</article>
        </html>
        """,
        url="https://empresa.example/carreiras",
    )

    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/jobs/42?area=dados",
        "https://empresa.example/vagas/43",
        "https://empresa.example/jobs/44",
    ]


def test_ari_descobre_rotas_numericas_sem_seguir_links_institucionais() -> None:
    resposta = _criar_resposta(
        '<a href="/2">Ver Detalhes</a><a href="/sobre">Ver Detalhes</a>',
        url="https://trabalheconosco.aridesa.com.br/",
    )
    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)
    assert [c.url for c in candidatos] == ["https://trabalheconosco.aridesa.com.br/2"]


def test_estrela_descobre_cidade_cargo_mas_exclui_banco() -> None:
    resposta = _criar_resposta(
        '<a href="/jacarei/auxiliar-financeiro">Ver Detalhes</a>'
        '<a href="/braganca/banco-auxiliar">Ver Detalhes</a>'
        '<a href="/candidato/">Cadastre-se</a>',
        url="https://rh.estreladolar.com.br/",
    )
    candidatos = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(resposta)
    assert [c.url for c in candidatos] == [
        "https://rh.estreladolar.com.br/jacarei/auxiliar-financeiro"
    ]


def test_adaptadores_accor_e_volvo_isolam_detalhes_publicos() -> None:
    """Accor pagina a busca e Volvo expõe detalhes com rotas distintas."""

    accor = _criar_resposta(
        '<a href="/global/en/job/gerente-de-contas-pleno-jid-111036">Gerente</a>'
        '<a href="/global/en/jobs?options=336&page=2">2</a>'
        '<a href="/global/en/brazil">Brasil</a>',
        url="https://careers.accor.com/global/en/jobs?options=336&page=1",
    )
    volvo = _criar_resposta(
        '<a href="/job/Curitiba-Data-Engineer-81260-900/1369089555/">Data</a>'
        '<a href="/content/Locations---BR/?locale=pt_BR">Localização</a>',
        url="https://jobs.volvogroup.com/?locale=pt_BR",
    )

    candidatos_accor = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(accor)
    candidatos_volvo = selecionar_adaptador(Fonte.PAGINA_CARREIRAS).descobrir(volvo)

    assert [(candidato.url, candidato.evidencias) for candidato in candidatos_accor] == [
        (
            "https://careers.accor.com/global/en/job/gerente-de-contas-pleno-jid-111036",
            ("detalhe_vaga_accor_publico",),
        ),
        (
            "https://careers.accor.com/global/en/jobs?options=336&page=2",
            ("paginacao_accor",),
        ),
    ]
    assert [(candidato.url, candidato.evidencias) for candidato in candidatos_volvo] == [
        (
            "https://jobs.volvogroup.com/job/Curitiba-Data-Engineer-81260-900/1369089555/",
            ("detalhe_vaga_volvo_publico",),
        )
    ]
