"""Testes dos extratores de recursos CSV publicados via CKAN."""

from datetime import date
from pathlib import Path

import pytest

from observatorio_vagas.extraction.ckan import extrair_job_postings_ckan


def test_extrai_csv_mensal_de_vagas_da_ufms() -> None:
    """O esquema cargo, nível e quantidade é oficial da UFMS."""

    resultado = extrair_job_postings_ckan(
        "cargo;nivel;qtde\nTécnico de Laboratório;Médio;2\nProfessor;Superior;1\n".encode(),
        url=("https://dadosabertos.ufms.br/dataset/recurso/download/servidores-vagas-09-2026.csv"),
        data_referencia=date(2026, 9, 1),
    )

    assert resultado.linhas_encontradas == 2
    assert resultado.linhas_invalidas == 0
    assert [vaga["title"] for vaga in resultado.vagas] == ["Técnico de Laboratório", "Professor"]
    assert resultado.vagas[0]["datePosted"] == "2026-09-01"
    assert "Quantidade divulgada" in resultado.vagas[0]["description"]


def test_extrai_vaga_setades_com_campos_publicos() -> None:
    """O recurso diário do ES deve produzir vaga sem inventar empregador."""

    cabecalho = ",".join(
        (
            "VAGA",
            "CÓDIGO DA VAGA",
            "DESCRIÇÃO DA VAGA",
            "EXIGÊNCIA DE QUALIFICAÇÃO",
            "EXIGÊNCIA DE ESCOLARIZAÇÃO",
            "QUANTIDADE",
            "dataAtualizacao",
            "posto",
        )
    )
    linha = ",".join(
        (
            "Auxiliar Administrativo",
            "12345",
            "Atendimento ao público",
            "Experiência de seis meses",
            "Ensino médio",
            "2",
            "09/01/2026",
            "Anchieta",
        )
    )
    csv = f"{cabecalho}\n{linha}\n"

    resultado = extrair_job_postings_ckan(
        csv,
        url="https://dados.es.gov.br/vagas_anchieta.csv",
    )

    assert resultado.linhas_encontradas == 1
    assert resultado.linhas_invalidas == 0
    assert len(resultado.vagas) == 1

    vaga = resultado.vagas[0]

    assert vaga["title"] == "Auxiliar Administrativo"
    assert vaga["datePosted"] == "2026-09-01"
    assert vaga["hiringOrganization"]["name"] == ("Empregador não divulgado (Anchieta)")
    assert vaga["jobLocation"]["address"]["addressRegion"] == "ES"
    assert "Quantidade de vagas: 2" in vaga["description"]


def test_ufpe_filtra_finalizado_e_agrega_cotas() -> None:
    """Somente edital em andamento vira uma oportunidade única."""

    cabecalho = ",".join(
        (
            "id_edital",
            "tipo_concurso",
            "numero_edital",
            "ano_edital",
            "id_edital_original",
            "tipo_edital",
            "numero_dou",
            "data_dou",
            "qnt_vagas_ampla_concorrencia",
            "qnt_vagas_pcd",
            "qnt_vagas_raciais",
            "inicio_inscricao",
            "fim_inscricao",
            "Status",
        )
    )
    ativa = (
        "10,Professor Substituto,12,2026,,Abertura,100,"
        "2026-08-01,3,1,1,2026-08-10,2026-09-30,EM ANDAMENTO"
    )
    finalizada = (
        "11,Magistério Superior,13,2026,,Abertura,101,"
        "2026-01-01,2,0,0,2026-01-10,2026-02-01,FINALIZADO"
    )
    csv = f"{cabecalho}\n{ativa}\n{ativa}\n{finalizada}\n"

    resultado = extrair_job_postings_ckan(
        csv.encode(),
        url="https://dados.ufpe.br/concursos-2026.csv",
        data_referencia=date(2026, 9, 2),
    )

    assert resultado.linhas_encontradas == 3
    assert resultado.linhas_ignoradas == 1
    assert len(resultado.vagas) == 1

    vaga = resultado.vagas[0]

    assert vaga["title"] == "Professor Substituto - Edital 12/2026"
    assert vaga["employmentType"] == "TEMPORARY"
    assert vaga["validThrough"] == "2026-09-30"
    assert vaga["_observatorio_ckan"]["quantidadeVagas"] == 5


def test_iftm_converte_apenas_vaga_ainda_vigente() -> None:
    """O CSV do IFTM deve preservar dados publicados e descartar vencidos."""

    cabecalho = ",".join(
        (
            "concedente",
            "tipo_vaga",
            "vaga",
            "no_salario_inicio",
            "no_salario_limite",
            "carga_horaria",
            "dt_vigencia_inicio",
            "dt_vigencia_limite",
            "cidade",
            "no_qtd_vagas",
        )
    )
    vigente = (
        "Empresa Exemplo,Estágio,Desenvolvedor Python,1200,1500,30,01/09/2026,30/09/2026,Uberaba,2"
    )
    vencida = "Empresa Antiga,Emprego,Analista,2000,2500,40,01/01/2026,31/01/2026,Uberlândia,1"
    sem_prazo = "Empresa Sem Prazo,Estágio,Analista,1000,1200,30,01/09/2026,,Uberaba,1"
    csv = f"{cabecalho}\n{vigente}\n{vencida}\n{sem_prazo}\n"

    resultado = extrair_job_postings_ckan(
        csv,
        url="https://dadosabertos.iftm.edu.br/vagas.csv",
        data_referencia=date(2026, 9, 4),
    )

    assert resultado.linhas_encontradas == 3
    assert resultado.linhas_ignoradas == 2
    assert len(resultado.vagas) == 1

    vaga = resultado.vagas[0]

    assert vaga["title"] == "Desenvolvedor Python"
    assert vaga["hiringOrganization"]["name"] == "Empresa Exemplo"
    assert vaga["jobLocation"]["address"]["addressLocality"] == "Uberaba"
    assert vaga["validThrough"] == "2026-09-30"
    assert vaga["employmentType"] == "INTERN"


def test_rejeita_csv_ckan_desconhecido() -> None:
    """Uma mudança silenciosa de esquema não pode criar vagas erradas."""

    with pytest.raises(ValueError, match="esquema"):
        extrair_job_postings_ckan(
            "coluna,nova\nvalor,teste\n",
            url="https://dados.example/recurso.csv",
        )


def test_pbh_no_crawler_preserva_campos_e_descarta_encerradas() -> None:
    resultado = extrair_job_postings_ckan(
        Path("tests/fixtures/pbh/vagas_ofertadas.csv").read_bytes(),
        url="https://ckan.pbh.gov.br/dataset/vagas/resource/a/download/a.csv",
        data_referencia=date(2026, 8, 31),
    )
    assert resultado.linhas_encontradas >= 2
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Técnico de suporte"
    assert resultado.vagas[0]["_observatorio"]["expiracao_inferida"] is True
    assert resultado.linhas_ignoradas >= 1


def test_ufvjm_nao_confunde_concurso_valido_com_candidaturas_abertas() -> None:
    csv = (
        "Carreira,Nº do edital,Finalidade do concurso/processo seletivo,Situação,"
        "Data final da validade,URL da fonte\n"
        "Docente,10/2026,Professor substituto,Em andamento,31/12/2028,https://ufvjm.example/10\n"
        "Docente,11/2026,Professor substituto,Encerrado,31/12/2028,https://ufvjm.example/11\n"
        "Docente,12/2026,Professor substituto,Inscrições abertas,,https://ufvjm.example/12\n"
    )
    resultado = extrair_job_postings_ckan(csv, url="https://dados.ufvjm.edu.br/recurso.csv")
    assert resultado.linhas_ignoradas == 2
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["identifier"]["value"] == "ufvjm:12/2026"
    assert "validThrough" not in resultado.vagas[0]
