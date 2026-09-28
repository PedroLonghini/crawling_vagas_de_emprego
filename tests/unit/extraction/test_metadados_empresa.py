"""Testes da extração de metadados institucionais da empresa."""

import json

import pytest

from observatorio_vagas.extraction.metadados_empresa import (
    enriquecer_job_posting_com_empresa,
    extrair_metadados_empresa_html,
    extrair_metadados_site_institucional_html,
)


def test_extrai_descricao_site_e_logo_da_empresa() -> None:
    """Uma seção institucional deve fornecer os três metadados."""

    html = b"""
        <div class="header-with-logo-logo">
            <img src="/arquivos/logo-empresa.png">
        </div>

        <section id="school-overview">
            <h2>About Harris Academy Tottenham</h2>

            <a
                data-link-type="school_website"
                href="https://empresa.example.com"
            >
                Company website
            </a>

            <p>
                Harris Academy Tottenham is an academy focused on
                outstanding teaching, learning and student development.
            </p>
        </section>
    """

    resultado = extrair_metadados_empresa_html(
        html,
        url_base="https://vagas.example.org/jobs/123",
    )

    assert resultado is not None

    assert resultado.descricao == (
        "Harris Academy Tottenham is an academy focused on "
        "outstanding teaching, learning and student development."
    )

    assert resultado.site == "https://empresa.example.com"

    assert resultado.logo_url == ("https://vagas.example.org/arquivos/logo-empresa.png")

    assert resultado.evidencias == (
        "secao_institucional",
        "link_site_institucional",
        "imagem_cabecalho_empresa",
    )


def test_pagina_sem_secao_institucional_nao_inventa_dados() -> None:
    """Uma página comum não deve produzir metadados falsos."""

    html = """
        <h1>Pessoa Desenvolvedora Python</h1>

        <p>
            Esta é apenas a descrição da vaga e não uma descrição
            institucional da empresa.
        </p>

        <a href="/contato">Contato</a>
    """

    resultado = extrair_metadados_empresa_html(
        html,
        url_base="https://empresa.example.com/vagas/123",
    )

    assert resultado is None


@pytest.mark.parametrize(
    "titulo",
    ("Sobre a empresa", "Quem somos", "Sobre nós", "A empresa"),
)
def test_extrai_descricao_apos_titulo_institucional(titulo: str) -> None:
    """Títulos institucionais explícitos devem revelar o texto subsequente."""

    html = f"""
        <section>
            <h2>{titulo}</h2>
            <p>
                Empresa brasileira especializada em produtos digitais,
                tecnologia e serviços para organizações.
            </p>
        </section>
    """

    resultado = extrair_metadados_empresa_html(
        html,
        url_base="https://empresa.example.com/vagas/123",
    )

    assert resultado is not None
    assert resultado.descricao == (
        "Empresa brasileira especializada em produtos digitais, tecnologia "
        "e serviços para organizações."
    )
    assert resultado.evidencias == ("titulo_secao_institucional",)


def test_titulo_sobre_a_vaga_nao_e_descricao_da_empresa() -> None:
    """O extrator não pode converter a seção da vaga em texto institucional."""

    html = """
        <section>
            <h2>Sobre a vaga</h2>
            <p>
                A pessoa será responsável por desenvolver produtos,
                participar de cerimônias e colaborar com o time técnico.
            </p>
        </section>
    """

    resultado = extrair_metadados_empresa_html(
        html,
        url_base="https://empresa.example.com/vagas/123",
    )

    assert resultado is None


def test_enriquecimento_preenche_campos_ausentes() -> None:
    """Os metadados HTML devem completar hiringOrganization."""

    html = """
        <div class="header-with-logo-logo">
            <img src="https://cdn.example.com/logo.png">
        </div>

        <section id="company-overview">
            <a
                data-link-type="company_website"
                href="https://empresa.example.com"
            >
                Site da empresa
            </a>

            <p>
                Empresa especializada em tecnologia, produtos digitais
                e desenvolvimento de sistemas corporativos.
            </p>
        </section>
    """

    metadados = extrair_metadados_empresa_html(
        html,
        url_base="https://vagas.example.com/123",
    )

    documento = {
        "@type": "JobPosting",
        "title": "Pessoa Desenvolvedora",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Empresa Exemplo",
            "description": None,
        },
    }

    resultado = enriquecer_job_posting_com_empresa(
        documento,
        metadados,
    )

    organizacao = resultado["hiringOrganization"]

    assert organizacao["name"] == "Empresa Exemplo"

    assert organizacao["description"] == (
        "Empresa especializada em tecnologia, produtos digitais "
        "e desenvolvimento de sistemas corporativos."
    )

    assert organizacao["sameAs"] == "https://empresa.example.com"
    assert organizacao["logo"] == "https://cdn.example.com/logo.png"

    # O documento recebido não pode ser modificado silenciosamente.
    organizacao_original = documento["hiringOrganization"]

    assert organizacao_original["description"] is None
    assert "sameAs" not in organizacao_original
    assert "logo" not in organizacao_original


def test_enriquecimento_nao_sobrescreve_json_ld() -> None:
    """Informações já publicadas no JSON-LD possuem prioridade."""

    html = """
        <div class="header-with-logo-logo">
            <img src="https://html.example.com/logo.png">
        </div>

        <section id="employer-overview">
            <a
                data-link-type="employer_website"
                href="https://html.example.com"
            >
                Site
            </a>

            <p>
                Esta descrição veio do HTML e não deve substituir
                a descrição estruturada que já estava disponível.
            </p>
        </section>
    """

    metadados = extrair_metadados_empresa_html(
        html,
        url_base="https://vagas.example.com/123",
    )

    documento = {
        "@type": "JobPosting",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Empresa Estruturada",
            "description": "Descrição oficial do JSON-LD.",
            "sameAs": "https://json-ld.example.com",
            "logo": "https://json-ld.example.com/logo.png",
        },
    }

    resultado = enriquecer_job_posting_com_empresa(
        documento,
        metadados,
    )

    organizacao = resultado["hiringOrganization"]

    assert organizacao["description"] == "Descrição oficial do JSON-LD."
    assert organizacao["sameAs"] == "https://json-ld.example.com"
    assert organizacao["logo"] == "https://json-ld.example.com/logo.png"


def test_nao_enriquece_documento_sem_hiring_organization() -> None:
    """Metadados sem uma organização identificada não devem ser associados."""

    html = """
        <section id="company-overview">
            <p>
                Empresa especializada em tecnologia e produtos digitais.
            </p>
        </section>
    """

    metadados = extrair_metadados_empresa_html(
        html,
        url_base="https://vagas.example.com/123",
    )

    documento = {
        "@type": "JobPosting",
        "title": "Analista",
    }

    resultado = enriquecer_job_posting_com_empresa(
        documento,
        metadados,
    )

    assert "hiringOrganization" not in resultado


def test_substitui_logo_relativo_por_logo_absoluto() -> None:
    """Um ícone relativo pode ser substituído pelo logo institucional."""

    html = """
        <div class="header-with-logo-logo">
            <img src="https://cdn.example.com/logo-real.png">
        </div>
    """

    metadados = extrair_metadados_empresa_html(
        html,
        url_base="https://vagas.example.com/jobs/123",
    )

    documento = {
        "@type": "JobPosting",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Empresa Exemplo",
            "logo": "/assets/images/icone-generico.png",
        },
    }

    resultado = enriquecer_job_posting_com_empresa(
        documento,
        metadados,
    )

    organizacao = resultado["hiringOrganization"]

    assert organizacao["logo"] == ("https://cdn.example.com/logo-real.png")


def test_extrai_metadados_institucionais_do_next_data() -> None:
    """O estado público do Next.js deve fornecer dados da empresa."""

    dados_next = {
        "props": {
            "pageProps": {
                "job": {
                    "careerPage": {
                        "about": (
                            "<p>Empresa brasileira especializada em produtos "
                            "jurídicos e soluções digitais.</p>"
                        ),
                        "urlSite": "",
                        "urlLogo": "https://cdn.example.com/aurum.png",
                        "socialLinks": {
                            "urlSite": "https://www.aurum.com.br/",
                        },
                    },
                },
            },
        },
    }

    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(dados_next, ensure_ascii=False)}"
        "</script>"
    )

    resultado = extrair_metadados_empresa_html(
        html,
        url_base="https://aurum.gupy.io/jobs/123",
    )

    assert resultado is not None

    assert resultado.descricao == (
        "Empresa brasileira especializada em produtos jurídicos e soluções digitais."
    )

    assert resultado.site == "https://www.aurum.com.br/"
    assert resultado.logo_url == "https://cdn.example.com/aurum.png"

    assert resultado.evidencias == (
        "next_data_descricao_empresa",
        "next_data_site_empresa",
        "next_data_logo_empresa",
    )


def test_site_institucional_aceita_meta_descricao() -> None:
    """O enriquecimento explícito pode usar o resumo do site oficial."""

    html = """
        <html>
            <head>
                <meta
                    name="description"
                    content="Empresa brasileira especializada em tecnologia jurídica
                    e soluções digitais."
                >
            </head>
        </html>
    """

    resultado = extrair_metadados_site_institucional_html(
        html,
        url_base="https://www.empresa.example.com/",
    )

    assert resultado is not None
    assert resultado.descricao == (
        "Empresa brasileira especializada em tecnologia jurídica e soluções digitais."
    )
    assert resultado.site == "https://www.empresa.example.com/"
    assert resultado.evidencias == ("meta_description_site_institucional",)
