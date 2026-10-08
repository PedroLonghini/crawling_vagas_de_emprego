"""Conteúdo próprio, limites do artigo e licença das novas organizações."""

import pytest
from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.extraction.carreiras_empresas import extrair_vagas_carreiras_empresas
from observatorio_vagas.extraction.organizacoes import (
    extrair_vagas_carreiras_estaticas,
    extrair_vagas_jobconvo,
    extrair_vagas_organizacoes,
    extrair_vagas_teleperformance,
)


def test_extrai_cards_publicos_da_viatec_com_url_de_candidatura():
    corpo = b'''<div><h4>AUXILIAR DE TELECOMUNICACOES</h4><p>Panambi - RS codigo: 40</p><a href="/trabalheconosco/user/acesso/40">Quero me candidatar</a></div>'''

    resultado = extrair_vagas_carreiras_empresas(
        corpo,
        url="https://viatectelecom.com.br/trabalheconosco/",
    )

    assert resultado.dominio_reconhecido is True
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "AUXILIAR DE TELECOMUNICACOES"
    assert resultado.vagas[0]["_observatorio_apply_url"].endswith("/acesso/40")


def test_extrai_cards_publicos_do_gt_grupo():
    corpo = b'''<div id="vagas"><div><h3>Auxiliar de Logistica</h3><p>Campinas - SP</p><a href="/candidato/login.php">Candidatar-se</a></div></div>'''

    resultado = extrair_vagas_carreiras_empresas(corpo, url="https://vagas.gtgrupo.com.br/")

    assert resultado.dominio_reconhecido is True
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Auxiliar de Logistica"
    assert resultado.vagas[0]["_observatorio_apply_url"].endswith("/candidato/login.php")


def test_extrai_acordeao_e_formulario_proprio_da_marvi():
    corpo = b'''<details class="e-n-accordion-item"><summary><div class="e-n-accordion-item-title-text">AUXILIAR ADMINISTRATIVO</div></summary><div><form id="form_2192"><input name="nome"></form></div></details>'''

    resultado = extrair_vagas_carreiras_empresas(
        corpo, url="https://www.marvi.com.br/carreiras/"
    )

    assert resultado.dominio_reconhecido is True
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "AUXILIAR ADMINISTRATIVO"
    assert resultado.vagas[0]["_observatorio_apply_url"].endswith("#form_2192")
    assert resultado.vagas[0]["jobLocation"]["address"]["addressCountry"] == "BR"


def test_extrai_card_da_perigo_zero_com_candidatura_por_email():
    corpo = b'''<article><h3>Tecnico de Seguranca</h3><p>Atue em projetos industriais.</p><a href="mailto:rh@perigozero.com.br">Candidate-se</a></article>'''

    resultado = extrair_vagas_carreiras_empresas(
        corpo, url="https://perigozero.com.br/carreiras"
    )

    assert resultado.dominio_reconhecido is True
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Tecnico de Seguranca"
    assert resultado.vagas[0]["_observatorio_apply_url"] == "https://perigozero.com.br/carreiras"


def test_extrai_opcoes_do_formulario_proprio_da_marquezim():
    corpo = b'''<main id="content"><p>Vendedor(a) - Jales: atendimento ao cliente e experiencia em vendas.</p><form id="custom-form"><select name="Qual vaga deseja se candidatar"><option value="">Escolha</option><option value="Vendedor(a) - Jales">Vendedor(a) - Jales</option><option value="Outros">Outros</option></select></form></main>'''

    resultado = extrair_vagas_carreiras_empresas(
        corpo, url="https://www.marquezim.com.br/trabalhe-conosco"
    )

    assert resultado.dominio_reconhecido is True
    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert vaga["title"] == "Vendedor(a) - Jales"
    assert "atendimento" in vaga["description"]
    assert vaga["_observatorio_apply_url"].endswith("#custom-form")
    assert vaga["jobLocation"]["address"]["addressCountry"] == "BR"


@pytest.mark.parametrize(
    ("url", "corpo", "empresa"),
    [
        (
            "https://vagas.asscont.com.br/",
            b'<div class="modal"><h2>Analista Fiscal</h2><p>Sao Paulo/SP</p><a href="#">Candidatar-se</a></div>',
            "Asscont",
        ),
        (
            "https://www.setrata.com.br/trabalhe-conosco/",
            b'<div class="job reveal"><h3>Coordenador de Operacoes</h3><p>Campinas/SP</p><a href="#">Candidatar-se</a></div>',
            "Setrata",
        ),
    ],
)
def test_extrai_cards_com_candidatura_controlada_pela_pagina(url, corpo, empresa):
    resultado = extrair_vagas_carreiras_empresas(corpo, url=url)

    assert resultado.dominio_reconhecido is True
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["hiringOrganization"]["name"] == empresa
    assert resultado.vagas[0]["_observatorio_apply_url"] == url
    assert resultado.vagas[0]["jobLocation"]["address"]["addressCountry"] == "BR"


@pytest.mark.parametrize(
    ("dominio", "empresa", "titulo_tag", "container"),
    [
        (
            "nupef.org.br",
            "Instituto Nupef",
            'p class="elementor-heading-title"',
            '<div class="jet-listing-dynamic-field__content">{}</div>',
        ),
        (
            "www.transparencia.org.br",
            "Transparência Brasil",
            "h1",
            "<article><section>{}</section></article>",
        ),
    ],
)
def test_somente_vagas_proprias(dominio, empresa, titulo_tag, container):
    corpo = (
        f"<p>{empresa} busca profissionais para sua equipe de tecnologia, com "
        "experiência em pesquisa e desenvolvimento de sistemas para organizações sociais.</p>"
        "<p>Inscrições até 30/09/2026.</p>"
        '<p><a href="https://forms.example/inscricao">Formulário de candidatura</a></p>'
    )
    html = (
        '<meta property="article:published_time" content="2026-09-15T10:00:00Z">'
        f"<{titulo_tag}>Vaga: Analista de Tecnologia</{titulo_tag.split()[0]}>"
        + container.format(corpo)
        + "<footer>Publicidade que não pertence ao anúncio.</footer>"
    )
    url = f"https://{dominio}/noticias/vaga-analista/"
    resultado = extrair_vagas_organizacoes(html.encode(), url=url)
    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert vaga["hiringOrganization"]["name"] == empresa
    assert vaga["title"] == "Analista de Tecnologia"
    assert vaga["validThrough"] == "2026-09-30"
    assert vaga["datePosted"] == "2026-09-15"
    assert "Publicidade" not in vaga["description"]

    # A mesma notícia sobre outro empregador não herda a licença da organização.
    terceiro = html.replace(f"{empresa} busca", "Outra Empresa busca")
    assert not extrair_vagas_organizacoes(terceiro.encode(), url=url).vagas
    encerrada = html.replace("Vaga: Analista", "Vaga encerrada: Analista")
    assert not extrair_vagas_organizacoes(encerrada.encode(), url=url).vagas


def test_descobre_anuncio_em_lista_de_noticias():
    resposta = HtmlResponse(
        url="https://nupef.org.br/noticias/",
        encoding="utf-8",
        body=b'<a href="/2026/09/15/vaga-analista/">Nupef abre vaga para Analista</a>',
    )
    candidatos = AdaptadorGenericoHTML().descobrir(resposta)
    assert any(c.url.endswith("/vaga-analista/") for c in candidatos)


def test_separa_cartoes_de_vagas_estaticas_da_pagmais():
    """Cada cartão público deve produzir sua própria vaga brasileira."""

    html = """
    <main>
      <h2>Vagas abertas</h2>
      <section>
        <div><div><h3>Engenheiro(a) Backend</h3></div></div>
        <p>Desenvolva serviços de pagamentos com Node.js, PostgreSQL e alta
        disponibilidade para uma plataforma brasileira em crescimento.</p>
      </section>
      <section>
        <div><div><h3>Analista de Segurança</h3></div></div>
        <p>Fortaleça controles de segurança, auditorias e conformidade PCI DSS
        em uma operação de pagamentos digitais no Brasil.</p>
      </section>
    </main>
    """.encode()

    resultado = extrair_vagas_carreiras_estaticas(
        html,
        url="https://www.sejapagmaisbrasil.com/carreiras",
    )

    assert resultado.dominio_reconhecido
    assert [vaga["title"] for vaga in resultado.vagas] == [
        "Engenheiro(a) Backend",
        "Analista de Segurança",
    ]
    assert all(vaga["jobLocation"]["address"]["addressCountry"] == "BR" for vaga in resultado.vagas)


def test_extrai_jobconvo_e_descarta_empregador_terceiro():
    """A página Lear não pode publicar a vaga de uma consultoria terceira."""

    html = """
    <meta property="og:title" content="Auxiliar de Produção - LEAR DO BRASIL">
    <meta property="og:description" content="Atue na produção industrial com
    atenção à qualidade, segurança e trabalho em equipe na unidade da Lear em Caçapava.">
    <script type="application/ld+json">
      {"datePosted":"2026-09-21","validThrough":"2026-12-31",
      "employmentType":"FULL_TIME","jobLocation":"@type":"Place",
      "address":{"addressLocality":"CAÇAPAVA","addressRegion":"SP"}}
    </script>
    """.encode()
    url = (
        "https://app.jobconvo.com/job/abc/?career_page="
        "6476f49c-0260-4215-bd5e-e67da2dfc1a9"
    )

    resultado = extrair_vagas_jobconvo(html, url=url)

    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert vaga["title"] == "Auxiliar de Produção"
    assert vaga["hiringOrganization"]["name"] == "Lear"
    assert vaga["_observatorio_apply_url"] == url
    assert vaga["jobLocation"]["address"]["addressCountry"] == "BR"

    terceiro = html.replace(b"LEAR DO BRASIL", b"Adecco")
    assert not extrair_vagas_jobconvo(terceiro, url=url).vagas


def test_extrai_descricao_codificada_da_teleperformance():
    html = """
    <h1 class="vagaDetails__title my-4">Agente de Atendimento</h1>
    <input id="dh" value="&lt;p&gt;Atenda clientes e ofereça suporte técnico com
    comunicação clara, atenção aos detalhes e foco em uma boa experiência.&lt;/p&gt;
    &lt;p&gt;Local: Lapa – São Paulo/SP&lt;/p&gt;">
    """.encode()
    url = (
        "https://portaldevagas.teleperformance.com.br/VagaCandidatura/VagasDetail?"
        "idVaga=abc123"
    )

    resultado = extrair_vagas_teleperformance(html, url=url)

    assert resultado.dominio_reconhecido
    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert vaga["title"] == "Agente de Atendimento"
    assert vaga["hiringOrganization"]["name"] == "Teleperformance"
    assert vaga["_observatorio_apply_url"] == url
    assert vaga["jobLocation"]["address"] == {
        "@type": "PostalAddress",
        "addressCountry": "BR",
        "addressRegion": "SP",
        "addressLocality": "São Paulo",
    }
