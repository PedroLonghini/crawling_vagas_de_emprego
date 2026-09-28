"""Testes da descoberta de URLs de candidatura."""

from observatorio_vagas.extraction.url_candidatura import (
    extrair_url_candidatura_html,
)


def test_encontra_link_externo_de_candidatura() -> None:
    """A URL contendo apply deve vencer links comuns da página."""

    html = b"""
        <a href="/jobs/senior-science-technician">
            Detalhes
        </a>

        <a
            aria-label="Learn more about this role and how to apply"
            href="https://careers.example.org/vacancies/apply?jobId=123"
        >
            View advert on school website
        </a>
    """

    resultado = extrair_url_candidatura_html(
        html,
        url_base="https://jobs.example.org/vaga/123",
    )

    assert resultado is not None

    assert resultado.url == ("https://careers.example.org/vacancies/apply?jobId=123")

    assert resultado.evidencias == (
        "palavra_na_url",
        "palavra_no_texto",
    )


def test_resolve_link_relativo() -> None:
    """Um caminho relativo deve usar o domínio da página coletada."""

    resultado = extrair_url_candidatura_html(
        '<a href="/candidatura/456">Candidate-se</a>',
        url_base="https://empresa.example.com/vagas/456",
    )

    assert resultado is not None

    assert resultado.url == ("https://empresa.example.com/candidatura/456")


def test_texto_pode_identificar_link_sem_palavra_na_url() -> None:
    """O texto Apply now pode identificar uma URL de ATS."""

    resultado = extrair_url_candidatura_html(
        ('<a href="https://ats.example.com/jobs/789">Apply now</a>'),
        url_base="https://empresa.example.com/vagas/789",
    )

    assert resultado is not None

    assert resultado.url == ("https://ats.example.com/jobs/789")

    assert resultado.evidencias == ("palavra_no_texto",)


def test_atributo_explicito_de_candidatura_funciona_em_botao() -> None:
    """Um botão de frontend pode expor a URL sem usar uma âncora HTML."""

    resultado = extrair_url_candidatura_html(
        ('<button data-apply-url="/candidaturas/engenheira-plataforma">Candidatar</button>'),
        url_base="https://empresa.example.com/vagas/plataforma",
    )

    assert resultado is not None
    assert resultado.url == "https://empresa.example.com/candidaturas/engenheira-plataforma"
    assert resultado.evidencias == (
        "palavra_na_url",
        "atributo_de_candidatura",
    )


def test_ignora_links_sem_evidencia() -> None:
    """Uma página comum não deve gerar candidatura inventada."""

    resultado = extrair_url_candidatura_html(
        """
        <a href="/sobre">Sobre a empresa</a>
        <a href="/contato">Contato</a>
        """,
        url_base="https://empresa.example.com/vagas/123",
    )

    assert resultado is None


def test_rejeita_mailto_e_javascript() -> None:
    """Protocolos que não representam páginas não podem ser usados."""

    resultado = extrair_url_candidatura_html(
        """
        <a href="mailto:rh@example.com">Apply now</a>
        <a href="javascript:enviar()">Candidate-se</a>
        """,
        url_base="https://empresa.example.com/vagas/123",
    )

    assert resultado is None


def test_prefere_formulario_real_a_ancora_da_mesma_pagina() -> None:
    base = "https://emix.com.br/vaga/analista/"
    resultado = extrair_url_candidatura_html(
        '<a href="#candidatura">Candidate-se agora</a>'
        '<a href="https://docs.google.com/forms/d/abc/viewform">Envie seu currículo</a>',
        url_base=base,
    )
    assert resultado is not None
    assert resultado.url == "https://docs.google.com/forms/d/abc/viewform"
