"""Testes do extrator específico do quadro de vagas do NIC.br."""

from observatorio_vagas.extraction.nic_br import extrair_job_posting_nic_br


def test_extrai_campos_institucionais_e_prazo_do_nic_br() -> None:
    html = """
    <main>
      <h6 class="post-vaga-title bold">Assessor Técnico</h6>
      <div>
        I. Descrição da vaga: O NIC.br busca uma pessoa experiente para
        acompanhar projetos de governança da Internet, produzir relatórios,
        realizar análises técnicas e colaborar com uma equipe multidisciplinar.
      </div>
      <div>
        III. Informações gerais: Contratação pelo regime CLT. Modelo: presencial.
        Carga horária: 40 horas semanais. Local: sede do NIC.br em São Paulo - SP.
        Enviar currículo com pretensão salarial até 14 de setembro de 2026.
        <a href="mailto:selecao@cgi.br">Envie seu currículo</a>
      </div>
    </main>
    """.encode()

    resultado = extrair_job_posting_nic_br(
        html,
        url="https://nic.br/vagas/view/189/",
    )

    assert resultado.dominio_reconhecido
    vaga = resultado.vagas[0]
    assert vaga["identifier"] == "189"
    assert vaga["hiringOrganization"]["taxID"] == "05.506.560/0001-36"
    assert vaga["hiringOrganization"]["description"]
    assert vaga["jobLocation"]["address"]["addressCountry"] == "BR"
    assert vaga["employmentType"] == "CLT"
    assert vaga["jobLocationType"] == "ON_SITE"
    assert vaga["validThrough"] == "2026-09-14"
    assert vaga["_observatorio_email_candidatura"] == "selecao@cgi.br"
    assert "_observatorio_apply_url" not in vaga


def test_extrai_ultimo_dia_do_periodo_de_captacao() -> None:
    html = """
    <main><h6 class="post-vaga-title">Analista de Redes</h6>
    <p>Esta oportunidade requer conhecimentos técnicos, experiência prática e
    colaboração constante com a equipe responsável pela infraestrutura.</p>
    <p>Captação de currículos: 11/05/2026 a 29/05/2026</p></main>
    """.encode()

    resultado = extrair_job_posting_nic_br(
        html,
        url="https://nic.br/vagas/view/163/",
    )

    assert resultado.vagas[0]["validThrough"] == "2026-05-29"


def test_nao_interpreta_outras_paginas_como_nic_br() -> None:
    resultado = extrair_job_posting_nic_br(
        b"<main><h6 class='post-vaga-title'>Analista</h6></main>",
        url="https://example.com/vagas/view/189/",
    )

    assert not resultado.dominio_reconhecido
    assert resultado.vagas == ()


def test_recusa_detalhe_sem_conteudo_suficiente() -> None:
    resultado = extrair_job_posting_nic_br(
        b"<main><h6 class='post-vaga-title'>Analista</h6>Curto.</main>",
        url="https://nic.br/vagas/view/189/",
    )

    assert resultado.dominio_reconhecido
    assert resultado.vagas == ()
