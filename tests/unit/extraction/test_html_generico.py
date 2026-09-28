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
