"""Testes do bloqueio global de concursos públicos."""

from observatorio_vagas.crawling.filtro_conteudo import (
    eh_concurso_publico,
    eh_conteudo_nao_empregaticio,
)


def test_bloqueia_concurso_pelo_texto() -> None:
    assert eh_concurso_publico(
        url="https://orgao.gov.br/oportunidades",
        conteudo="Edital de concurso público para provimento de cargos.",
    )


def test_bloqueia_concurso_pela_url() -> None:
    assert eh_concurso_publico(url="https://orgao.gov.br/concurso-publico/edital")


def test_permite_vaga_de_emprego_comum() -> None:
    assert not eh_concurso_publico(
        url="https://empresa.example/vagas/desenvolvedor",
        conteudo="Vaga de emprego para pessoa desenvolvedora Python.",
    )


def test_bloqueia_edital_de_contratacao_de_consultoria() -> None:
    """Comprar consultoria não é anunciar uma posição de trabalho."""

    assert eh_conteudo_nao_empregaticio(
        url=("https://empresa.example/oportunidades/edital-para-contratacao-de-consultoria"),
        conteudo="Edital para contratação de consultoria em comunicação.",
    )


def test_permite_vaga_para_consultor() -> None:
    """O filtro não pode confundir uma profissão com aquisição de serviço."""

    assert not eh_conteudo_nao_empregaticio(
        url="https://empresa.example/vagas/consultor-de-vendas",
        conteudo="Vaga para Consultor de Vendas com contratação CLT.",
    )


def test_ignora_link_relacionado_e_analisa_titulo_da_pagina() -> None:
    """Uma vaga não pode ser bloqueada por edital citado no rodapé da página."""

    assert not eh_conteudo_nao_empregaticio(
        url="https://empresa.example/oportunidades/estagio-administracao",
        conteudo="""
            <html>
              <head><title>Vaga de estágio em Administração</title></head>
              <body>
                <a href="/edital-para-contratacao-de-consultoria">
                  Edital para contratação de consultoria
                </a>
              </body>
            </html>
        """,
    )
