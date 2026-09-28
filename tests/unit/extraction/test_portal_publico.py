"""Testes da extração de processos seletivos municipais."""

from observatorio_vagas.extraction.portal_publico import (
    extrair_job_postings_portal_publico,
)

URL_EDITAL = "https://www.pratania.sp.gov.br/portal/editais/0/3/1481"

HTML_EDITAL = """
<html>
    <head>
        <title>
            Prefeitura de Pratânia - SP - PROCESSO SELETIVO Nº 002/2026
        </title>
    </head>
    <body>
        <div class="ed_linha_lista_detalhes">
            <div class="ed_nome_detalhe">Situação</div>
            <div class="ed_descricao_detalhe"><span>Aberto</span></div>
        </div>
        <div class="ed_linha_lista_detalhes">
            <div class="ed_nome_detalhe">Nº do Processo</div>
            <div class="ed_descricao_detalhe">2/2026</div>
        </div>
        <div class="ed_linha_lista_detalhes">
            <div class="ed_nome_detalhe">Publicado em</div>
            <div class="ed_descricao_detalhe">28/08/2026 às 16h00</div>
        </div>
        <div class="ed_linha_lista_detalhes">
            <div class="ed_nome_detalhe">Início das Inscrições</div>
            <div class="ed_descricao_detalhe">31/08/2026 às 08h00</div>
        </div>
        <div class="ed_linha_lista_detalhes">
            <div class="ed_nome_detalhe">Fim das Inscrições</div>
            <div class="ed_descricao_detalhe">11/09/2026 às 16h30</div>
        </div>
        <div class="ed_descricao_edital">
            Processo Seletivo Simplificado, destinado à seleção de candidatos
            para eventual contratação temporária de MOTORISTA NÍVEL III, para
            atendimento das demandas das Diretorias Municipais de Educação e Saúde.
        </div>
        <footer>CNPJ 01.576.782/0001-74</footer>
    </body>
</html>
"""


def test_extrai_vaga_aberta_com_empresa_e_validade() -> None:
    """O edital aberto deve produzir um JobPosting auditável."""

    resultado = extrair_job_postings_portal_publico(
        HTML_EDITAL,
        url=URL_EDITAL,
    )

    assert resultado.pagina_reconhecida
    assert len(resultado.vagas) == 1

    vaga = resultado.vagas[0]

    assert vaga["title"] == "Motorista Nível III"
    assert vaga["identifier"]["value"] == "2/2026"
    assert vaga["datePosted"] == "2026-08-28T16:00:00-03:00"
    assert vaga["validThrough"] == "2026-09-11T16:30:00-03:00"
    assert vaga["employmentType"] == "TEMPORARY"
    assert vaga["_observatorio_apply_url"] == URL_EDITAL
    assert vaga["hiringOrganization"]["name"] == "Prefeitura de Pratânia"
    assert vaga["hiringOrganization"]["taxID"] == "01.576.782/0001-74"
    assert vaga["jobLocation"]["address"]["addressLocality"] == "Pratânia"


def test_ignora_pagina_fora_do_padrao() -> None:
    """Uma página comum não deve ser transformada em vaga pública."""

    resultado = extrair_job_postings_portal_publico(
        HTML_EDITAL,
        url="https://www.pratania.sp.gov.br/noticias/1481",
    )

    assert not resultado.pagina_reconhecida
    assert resultado.vagas == ()


def test_edital_encerrado_nao_produz_vaga() -> None:
    """O portal pode ser reconhecido sem publicar processo encerrado."""

    resultado = extrair_job_postings_portal_publico(
        HTML_EDITAL.replace(">Aberto<", ">Encerrado<"),
        url=URL_EDITAL,
    )

    assert resultado.pagina_reconhecida
    assert resultado.vagas == ()
