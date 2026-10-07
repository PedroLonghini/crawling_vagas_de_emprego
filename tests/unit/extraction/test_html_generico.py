"""Testes do fallback HTML conservador."""

from observatorio_vagas.extraction.html_generico import (
    extrair_job_posting_html_generico,
)


def test_extrai_detalhe_estatico_sem_json_ld() -> None:
    """Título e descrição explícitos devem produzir um JobPosting interno."""

    html = """
        <html>
            <head><meta property="og:site_name" content="Empresa Exemplo"></head>
            <body>
                <h1>Pessoa Desenvolvedora Python</h1>
                <div class="job-location">Campinas - SP</div>
                <section class="job-description">
                    Procuramos uma pessoa desenvolvedora para criar e manter
                    sistemas em Python, escrever testes automatizados e
                    colaborar diariamente com o restante da equipe técnica.
                </section>
            </body>
        </html>
    """.encode()

    resultado = extrair_job_posting_html_generico(
        html,
        url="https://empresa.example/vagas/python",
        empresa_nome=None,
    )

    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert vaga["title"] == "Pessoa Desenvolvedora Python"
    assert vaga["hiringOrganization"]["name"] == "Empresa Exemplo"
    assert vaga["jobLocation"]["address"]["addressLocality"] == "Campinas - SP"


def test_rejeita_listagem_generica() -> None:
    """Uma página de listagem não deve virar uma vaga falsa."""

    html = (
        b"<html><h1>Vagas</h1><div class='job-description'>"
        + (b"Texto institucional sem uma vaga individual. " * 5)
        + b"</div></html>"
    )

    resultado = extrair_job_posting_html_generico(
        html,
        url="https://empresa.example/vagas",
        empresa_nome="Empresa Exemplo",
    )

    assert resultado.vagas == ()


def test_rejeita_descricao_curta() -> None:
    """Pouco conteúdo não oferece evidência suficiente para criar anúncio."""

    resultado = extrair_job_posting_html_generico(
        b"<html><h1>Analista</h1><p class='job-description'>Venha trabalhar.</p></html>",
        url="https://empresa.example/vagas/analista",
        empresa_nome="Empresa Exemplo",
    )

    assert resultado.vagas == ()


def test_codam_extrai_descricao_e_localidade_do_detalhe() -> None:
    html = """
    <html><body><h1>Operador de Produção</h1>
    <div class="module-icon-item"><svg><use href="#tf-fas-location-pin"></use></svg>
      <span>Piracaia - SP</span></div>
    <div data-testid="text-section">
      <h3 data-testid="section-Descrição da vaga-title">Descrição da vaga</h3>
      <div>Operar máquinas de produção, embalar produtos e manter a organização
      do setor, seguindo padrões de segurança e qualidade durante todos os turnos.</div>
    </div></body></html>
    """.encode()

    vagas = extrair_job_posting_html_generico(
        html, url="https://vagas.codam.com.br/operador-de-producao/", empresa_nome="Codam"
    ).vagas

    assert len(vagas) == 1
    assert vagas[0]["hiringOrganization"]["name"] == "Codam"
    assert vagas[0]["jobLocation"]["address"]["addressLocality"] == "Piracaia - SP"
    assert vagas[0]["_observatorio_apply_url"] == (
        "https://vagas.codam.com.br/operador-de-producao/"
    )


def test_abler_extrai_detalhe_com_titulo_e_secao_proprios() -> None:
    html = """
    <html><body><h2>Auxiliar de Produção</h2>
    <h3>Sobre a vaga</h3><section>Atuar na linha de produção, organizar materiais,
    conferir produtos e seguir as normas de qualidade e segurança da empresa.</section>
    </body></html>
    """.encode()

    vagas = extrair_job_posting_html_generico(
        html,
        url="https://ats.abler.com.br/jobs/rhnossa?slug=auxiliar-de-producao-1",
        empresa_nome="RH Nossa",
    ).vagas

    assert len(vagas) == 1
    assert vagas[0]["title"] == "Auxiliar de Produção"


def test_data_publicacao_nao_e_prazo_de_candidatura() -> None:
    html = """
    <html><body><h1>Técnico de segurança</h1>
    <p>Publicada em 08/09/2026</p>
    <div class="job-description">Atuação em segurança do trabalho, com inspeções,
    treinamentos e elaboração de relatórios técnicos para unidades da empresa.</div>
    </body></html>
    """.encode()

    vagas = extrair_job_posting_html_generico(
        html, url="https://www.vagas.preventwork.com.br/vagas/tecnico", empresa_nome="Prevent Work"
    ).vagas

    assert len(vagas) == 1
    assert vagas[0]["datePosted"] == "2026-09-08"
    assert "validThrough" not in vagas[0]


def test_extrai_estrutura_estatica_semantica_de_pagina_de_carreiras() -> None:
    """O conteúdo principal prevalece sobre a descrição curta de rede social."""

    html = """
        <html>
            <head>
                <meta
                    property="og:description"
                    content="Confira a vaga e candidate-se."
                >
            </head>
            <body>
                <h6 class="post-vaga-title bold">Analista de Suporte</h6>
                <div class="vagas-container">
                    Local: São Paulo - SP. A pessoa contratada realizará
                    atendimento técnico, análise de redes, documentação dos
                    procedimentos e colaboração com a equipe de infraestrutura.
                </div>
            </body>
        </html>
    """.encode()

    resultado = extrair_job_posting_html_generico(
        html,
        url="https://empresa.example/vagas/view/123",
        empresa_nome="Empresa Exemplo",
    )

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Analista de Suporte"
    assert "atendimento técnico" in resultado.vagas[0]["description"]


def test_extrai_campos_rotulados_visiveis_da_vaga() -> None:
    """Rótulos explícitos enriquecem o documento sem adivinhar valores."""

    html = """
        <html><body>
            <h1>Analista de Dados</h1>
            <div class="job-description">
                Buscamos uma pessoa analista para construir indicadores,
                documentar modelos, colaborar com o time de produto e apoiar
                decisões de negócio baseadas em dados confiáveis.
            </div>
            <p>Modalidade: Híbrido | Tipo de contrato: CLT | Senioridade: Pleno</p>
            <p>Local: Belo Horizonte - MG | Salário: R$ 3.500,00 a R$ 4.200,00 mensal</p>
            <p>Inscrições até: 30/09/2026</p>
            <p>Benefícios: VR, plano de saúde e auxílio educação</p>
            <p>Publicada em: 01/09/2026</p>
        </body></html>
    """.encode()

    resultado = extrair_job_posting_html_generico(
        html,
        url="https://empresa.example/vagas/analista-dados",
        empresa_nome="Empresa Exemplo",
    )

    vaga = resultado.vagas[0]
    assert vaga["jobLocationType"] == "Híbrido"
    assert vaga["employmentType"] == "CLT"
    assert vaga["experienceRequirements"] == "Pleno"
    assert vaga["jobLocation"]["address"]["addressLocality"] == "Belo Horizonte - MG"
    assert vaga["baseSalary"]["currency"] == "BRL"
    assert vaga["baseSalary"]["value"]["minValue"] == "3500.00"
    assert vaga["baseSalary"]["value"]["maxValue"] == "4200.00"
    assert vaga["baseSalary"]["value"]["unitText"] == "MONTH"
    assert vaga["validThrough"] == "2026-09-30"
    assert vaga["jobBenefits"] == "VR, plano de saúde e auxílio educação"
    assert vaga["datePosted"] == "2026-09-01"


def test_prioriza_microdados_padrao_quando_a_pagina_os_oferece() -> None:
    """Microdados evitam depender do texto e já trazem datas normalizadas."""

    html = """
        <html><body>
            <h1>Engenheira de Plataforma</h1>
            <section class="job-description">
                Esta vaga cuida de plataformas internas, automação de entregas,
                observabilidade, segurança e suporte às pessoas desenvolvedoras
                em um produto digital de grande escala.
            </section>
            <meta itemprop="jobLocationType" content="TELECOMMUTE">
            <meta itemprop="employmentType" content="FULL_TIME">
            <meta itemprop="experienceRequirements" content="MID_SENIOR_LEVEL">
            <meta itemprop="validThrough" content="2026-10-31T23:59:59+00:00">
            <meta itemprop="datePosted" content="2026-09-03">
            <meta itemprop="jobBenefits" content="Plano de saúde e vale alimentação">
            <div itemprop="baseSalary">
                <meta itemprop="currency" content="BRL">
                <meta itemprop="minValue" content="4800.00">
                <meta itemprop="maxValue" content="6200.00">
                <meta itemprop="unitText" content="MONTH">
            </div>
        </body></html>
    """.encode()

    resultado = extrair_job_posting_html_generico(
        html,
        url="https://empresa.example/vagas/plataforma",
        empresa_nome="Empresa Exemplo",
    )

    vaga = resultado.vagas[0]
    assert vaga["jobLocationType"] == "TELECOMMUTE"
    assert vaga["employmentType"] == "FULL_TIME"
    assert vaga["experienceRequirements"] == "MID_SENIOR_LEVEL"
    assert vaga["validThrough"] == "2026-10-31"
    assert vaga["datePosted"] == "2026-09-03"
    assert vaga["jobBenefits"] == "Plano de saúde e vale alimentação"
    assert vaga["baseSalary"]["currency"] == "BRL"
    assert vaga["baseSalary"]["value"]["minValue"] == "4800.00"
    assert vaga["baseSalary"]["value"]["maxValue"] == "6200.00"
    assert vaga["baseSalary"]["value"]["unitText"] == "MONTH"


def test_extrai_campos_em_tabela_e_lista_de_definicao_sem_dois_pontos() -> None:
    html = """
        <html><body>
            <h1>Pessoa Engenheira de Dados</h1>
            <section class="job-description">
                Esta vaga desenvolve produtos de dados, mantém pipelines confiáveis,
                trabalha em colaboração com engenharia e produto e documenta decisões
                técnicas importantes para que a equipe evolua com segurança.
            </section>
            <dl>
                <dt>Local de trabalho</dt><dd>Recife - PE</dd>
                <dt>Formato de trabalho</dt><dd>Remoto</dd>
                <dt>Tipo de contrato</dt><dd>CLT</dd>
                <dt>Senioridade</dt><dd>Sênior</dd>
                <dt>Prazo para candidatura</dt><dd>14 de setembro de 2026</dd>
            </dl>
            <table><tr><th>Salário</th><td>R$ 8.000,00 a R$ 10.000,00 mensal</td></tr></table>
        </body></html>
    """.encode()

    vaga = extrair_job_posting_html_generico(
        html,
        url="https://empresa.example/vagas/engenheira-dados",
        empresa_nome="Empresa Exemplo",
    ).vagas[0]

    assert vaga["jobLocation"]["address"]["addressLocality"] == "Recife - PE"
    assert vaga["jobLocationType"] == "Remoto"
    assert vaga["employmentType"] == "CLT"
    assert vaga["experienceRequirements"] == "Sênior"
    assert vaga["validThrough"] == "2026-09-14"
    assert vaga["baseSalary"]["value"]["minValue"] == "8000.00"
    assert vaga["baseSalary"]["value"]["maxValue"] == "10000.00"


DESCRICAO_LONGA = "Responsabilidades: atender clientes, organizar rotinas e apoiar a equipe. " * 5


def _extrair(titulo: str, url: str, descricao: str = DESCRICAO_LONGA):
    from observatorio_vagas.extraction.html_generico import extrair_job_posting_html_generico

    html = (
        f"<html><head><title>{titulo}</title></head><body><h1>{titulo}</h1>"
        f'<div class="job-description">{descricao}</div></body></html>'
    )
    return extrair_job_posting_html_generico(html.encode(), url=url, empresa_nome="Empresa")


def test_vaga_real_continua_sendo_extraida() -> None:
    assert _extrair("Analista Administrativo", "https://empresa.example/vagas/123").vagas


def test_paginas_de_listagem_nao_viram_vaga() -> None:
    for titulo in (
        "Vagas de Técnico Químico",
        "494 Vagas de Oficial de Manutenção",
        "8676 Vagas de Emprego em São José dos Campos/SP",
        "Jobs in Crato",
        "Trabalhe conosco",
    ):
        assert not _extrair(titulo, "https://empresa.example/vagas/123").vagas, titulo


def test_conteudo_editorial_nao_vira_vaga() -> None:
    for caminho in (
        "/blog/como-ser-analista",
        "/noticias/x",
        "/profissoes/tecnico",
        "/observatorio/",
    ):
        assert not _extrair("Analista de dados", f"https://empresa.example{caminho}").vagas, caminho


def test_texto_de_cookies_nao_vale_como_descricao() -> None:
    cookies = "Necessary cookies are essential for the website to function. " * 5

    assert not _extrair("Analista", "https://empresa.example/vagas/1", cookies).vagas


def _extrair_com_json_ld(
    tipos: str,
    titulo: str = "Analista Administrativo",
    url: str = "https://empresa.example/vagas/1",
):
    from observatorio_vagas.extraction.html_generico import extrair_job_posting_html_generico

    html = (
        f'<html><head><script type="application/ld+json">{tipos}</script>'
        f"<title>{titulo}</title></head><body><h1>{titulo}</h1>"
        f'<div class="job-description">{DESCRICAO_LONGA}</div></body></html>'
    )
    return extrair_job_posting_html_generico(html.encode(), url=url, empresa_nome="Empresa")


def test_pagina_que_se_declara_loja_evento_ou_lista_nao_vira_vaga() -> None:
    for tipos in (
        '{"@type": ["ClothingStore", "Event"]}',
        '{"@type": "CollectionPage"}',
        '{"@graph": [{"@type": "ItemList"}]}',
        '{"@type": "Product"}',
    ):
        assert not _extrair_com_json_ld(tipos).vagas, tipos


def test_loja_ou_organizacao_no_site_inteiro_nao_derruba_a_vaga() -> None:
    assert _extrair_com_json_ld('{"@type": "ClothingStore"}').vagas
    assert _extrair_com_json_ld('{"@type": "Organization"}').vagas


def test_noticia_so_vale_se_o_titulo_falar_de_vaga() -> None:
    artigo = '{"@type": "NewsArticle"}'

    url_noticia = "https://jornal.example/2026/09/calendario"
    assert not _extrair_com_json_ld(artigo, "Eleições 2026: veja o calendário", url_noticia).vagas
    assert _extrair_com_json_ld(artigo, "Cacau Show contrata operadora de loja", url_noticia).vagas
    # Endereço de vaga também salva a notícia (ex.: /trabalhe-conosco/...).
    assert _extrair_com_json_ld(artigo, "Operador de Máquina").vagas


def test_jobposting_na_pagina_sempre_vale() -> None:
    assert _extrair_com_json_ld('[{"@type": "Event"}, {"@type": "JobPosting"}]').vagas


def test_tipo_com_prefixo_de_vocabulario_tambem_conta() -> None:
    assert not _extrair_com_json_ld('{"@type": "schema:Product"}').vagas
    assert not _extrair_com_json_ld('{"@type": "http://schema.org/Event"}').vagas


def test_oferta_aninhada_na_organizacao_nao_derruba_a_vaga() -> None:
    organizacao = '{"@type": "Organization", "makesOffer": {"@type": "Offer"}}'

    assert _extrair_com_json_ld(organizacao).vagas


def test_og_type_produto_so_derruba_quando_nao_fala_de_vaga() -> None:
    from observatorio_vagas.extraction.html_generico import extrair_job_posting_html_generico

    def extrair(titulo: str, url: str):
        html = (
            '<html><head><meta property="og:type" content="product">'
            f"<title>{titulo}</title></head><body><h1>{titulo}</h1>"
            f'<div class="job-description">{DESCRICAO_LONGA}</div></body></html>'
        )
        return extrair_job_posting_html_generico(html.encode(), url=url, empresa_nome="E").vagas

    assert not extrair("Camiseta Polo Azul", "https://loja.example/camiseta-polo")
    assert extrair("Vaga: Auxiliar de Secretaria", "https://basilica.example/vaga-auxiliar")


def _empresa(site_name: str, json_ld: str = '{"@type": "WebPage"}', empresa_nome: str = "X"):
    from observatorio_vagas.extraction.html_generico import extrair_job_posting_html_generico

    html = (
        f'<html><head><meta property="og:site_name" content="{site_name}">'
        f'<script type="application/ld+json">{json_ld}</script></head><body>'
        f'<h1>Operador de Máquina</h1><div class="job-description">{DESCRICAO_LONGA}</div>'
        "</body></html>"
    )
    vagas = extrair_job_posting_html_generico(
        html.encode(), url="https://site.example/vagas/1", empresa_nome=empresa_nome
    ).vagas
    return (vagas[0].get("hiringOrganization") or {}).get("name") if vagas else "SEM VAGA"


def test_nome_de_portal_ou_jornal_nao_vira_empresa() -> None:
    for portal in ("Empregos na Bahia", "Mais Vagas ES", "Notícias Botucatu", "Gazeta Digital"):
        assert _empresa(portal, empresa_nome="Portal") is None, portal


def test_nome_ambiguo_so_cai_em_pagina_de_noticia() -> None:
    post = '{"@type": "BlogPosting"}'
    for nome in ("Folha de Paraguaçu", "Mundo RH", "Turismoemfoco"):
        assert _empresa(nome, empresa_nome="Portal") == nome, nome
        assert _empresa(nome, post, empresa_nome="Portal") is None, nome


def test_nome_da_empresa_do_site_continua_valendo() -> None:
    assert _empresa("Comdarpe") == "Comdarpe"
    assert _empresa("Laserflex") == "Laserflex"
    assert _empresa("Vagalume") == "Vagalume"
    assert _empresa("Carreiras Nu: Faça Parte do Time") == "Carreiras Nu: Faça Parte do Time"
    # O Yoast marca páginas de empresa como Article; isso sozinho não derruba o nome.
    assert _empresa("Massa.com.br", '{"@type": "Article"}') == "Massa.com.br"


def test_pagina_de_noticia_nao_herda_o_nome_do_site() -> None:
    noticia = '{"@type": "NewsArticle"}'

    # A vaga passa (o endereço tem /vagas/), mas a empresa não é o jornal.
    assert _empresa("Hora Brasil", noticia, empresa_nome="Hora Brasil") is None


def _materia(titulo: str, corpo: str, extra: str = "") -> list:
    from observatorio_vagas.extraction.html_generico import extrair_job_posting_html_generico

    html = (
        f"<html><head><title>{titulo}</title>{extra}</head><body><h1>{titulo}</h1>"
        '<span itemprop="author">Por Fábio Cardoso</span>'
        '<time itemprop="datePublished">10/08/2026</time>'
        f'<div class="job-description">{corpo}</div></body></html>'
    )
    return extrair_job_posting_html_generico(
        html.encode(), url="https://turismo.example/v1/2026/07/21/materia/", empresa_nome="X"
    ).vagas


def test_materia_com_autor_e_data_so_vale_com_cara_de_vaga() -> None:
    noticia = "A Azul amplia o plano de contratação de pilotos. Requisitos: licença. " * 4
    assert not _materia("Azul anuncia contratação de 446 novos pilotos", noticia)

    vaga = noticia + " Interessados devem enviar currículo para rh@empresa.example."
    assert _materia("Empresa contrata auxiliar de cozinha", vaga)


def test_lista_de_materias_de_jornal_nao_vira_vaga() -> None:
    from observatorio_vagas.extraction.html_generico import extrair_job_posting_html_generico

    itens = "".join(
        f'<article><time itemprop="datePublished">{d}/09/2026</time>'
        f"<h2>Entrevista {d}</h2></article>"
        for d in range(1, 8)
    )
    publisher = (
        '<script type="application/ld+json">{"@type": "WebPage", "publisher": '
        '{"@type": "NewsMediaOrganization", "name": "Jornal"}}</script>'
    )
    html = (
        f"<html><head><title>Entrevista da Segunda</title>{publisher}</head><body>"
        f"<h1>Entrevista da Segunda</h1>{itens}"
        f'<div class="job-description">{DESCRICAO_LONGA}</div></body></html>'
    )

    assert not extrair_job_posting_html_generico(
        html.encode(), url="https://jornal.example/especial/entrevista/", empresa_nome="X"
    ).vagas


def test_chamada_de_varias_vagas_nao_e_uma_vaga() -> None:
    for titulo in (
        "Bunge: MULTINACIONAL tem mais de 70 vagas de trabalho disponíveis, confira - 99 Empregos",
        "Besni: Varejista de moda tem EXCELENTES oportunidades, confira - 99 Empregos",
        "Magazine Luiza abre 300 vagas",
    ):
        assert not _extrair(titulo, "https://empregos.example/vaga/x").vagas, titulo

    assert _extrair("Auxiliar de Reposição - Arujá", "https://empregos.example/vaga/x").vagas


def test_paginas_de_lista_do_eu_dev_nao_sao_vaga() -> None:
    from observatorio_vagas.extraction.html_generico import TITULO_DE_LISTAGEM

    assert TITULO_DE_LISTAGEM.search("Vagas com Full-Stack — 300 abertas (Remoto e Híbrido)")
    assert TITULO_DE_LISTAGEM.search("Carreira · eu.dev.br")
    assert not TITULO_DE_LISTAGEM.search("Desenvolvedor Back-end · eu.dev.br")


def test_busca_curso_e_chamada_de_noticia_nao_sao_vaga() -> None:
    from observatorio_vagas.extraction.html_generico import TITULO_DE_LISTAGEM

    for titulo in (
        'Encontramos 9 vagas em 5 anúncios relacionadas à busca de "Serralheiro"',
        "Curso gratuito de Libras abre inscrições com 1.500 vagas mensais em São Paulo",
        "Segala's Alimentos VOLTA A CONTRATAR; Confira!",
    ):
        assert TITULO_DE_LISTAGEM.search(titulo), titulo
    assert not TITULO_DE_LISTAGEM.search("Auxiliar de Cozinha - Turno Noite")
