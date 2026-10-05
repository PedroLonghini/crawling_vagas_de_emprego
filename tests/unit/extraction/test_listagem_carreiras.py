from observatorio_vagas.extraction.listagem_carreiras import extrair_vagas_listagem_carreiras


def test_extrai_opcoes_de_vagas_do_formulario() -> None:
    resultado = extrair_vagas_listagem_carreiras(
        b'<select id="vaga"><option>Selecione</option><option>Operador de Producao</option></select>',
        url="https://empresa.example/carreiras",
    )
    assert [vaga["title"] for vaga in resultado.vagas] == ["Operador de Producao"]


def test_extrai_card_com_candidatura_explicita() -> None:
    resultado = extrair_vagas_listagem_carreiras(
        b'<article class="job"><h2>Analista Financeiro</h2><p>Vaga presencial.</p><a href="/candidatar/1">Candidate-se</a></article>',
        url="https://empresa.example/carreiras",
    )
    assert resultado.vagas[0]["_observatorio_apply_url"] == "https://empresa.example/candidatar/1"


def _titulos(html: str) -> list[str]:
    resultado = extrair_vagas_listagem_carreiras(
        html.encode(), url="https://empresa.example/trabalhe-conosco"
    )
    return [vaga["title"] for vaga in resultado.vagas]


def test_filtros_de_busca_nao_viram_vagas() -> None:
    filtros = (
        '<select name="field_job_salary_min"><option>Min</option><option>10k</option>'
        "<option>15k</option></select>"
        '<select id="job_type"><option>CLT Full</option><option>Estagio</option></select>'
        '<select name="job_category_filter"><option>Agronomia</option></select>'
        '<select name="awsm_job_spec[job-location]"><option>ALEGRETE</option></select>'
        '<select id="filter-job-order-by"><option>Mais recentes</option></select>'
    )

    assert _titulos(filtros) == []


def test_menu_de_cargo_de_contato_ou_site_nao_vira_vaga() -> None:
    menus = (
        '<select name="cargo"><option>CEO</option><option>Diretor</option></select>'
        '<select id="menu-cargo"><option>Almoxarife</option><option>Analista</option></select>'
    )

    assert _titulos(menus) == []


def test_cargo_pretendido_continua_sendo_lido_sem_placeholders() -> None:
    html = (
        '<select id="cargo-pretendido"><option>Selecione</option><option>— Escolha uma opção —</option>'
        "<option>Analista de BI</option><option>Todos os cargos</option>"
        "<option>Outras vagas</option></select>"
        '<select name="desired_position"><option>Advogado</option><option>SP</option></select>'
    )

    assert _titulos(html) == ["Analista de BI", "Advogado"]


def test_menu_enorme_nao_e_lista_de_vagas() -> None:
    opcoes = "".join(f"<option>Cargo {n}</option>" for n in range(200))

    assert _titulos(f'<select id="cargo-pretendido">{opcoes}</select>') == []


def _vagas(url: str, **argumentos):
    from observatorio_vagas.extraction.listagem_carreiras import atribuir_empresa_do_site

    html = (
        b'<html><head><meta property="og:site_name" content="Tilibra"></head><body>'
        b'<select id="cargo-pretendido"><option>Analista</option></select></body></html>'
    )
    vagas = extrair_vagas_listagem_carreiras(html, url=url).vagas
    return atribuir_empresa_do_site(
        vagas, url=url, empresa_nome=argumentos.get("empresa_nome", "Tilibra Ltda"), corpo=html
    )


def test_aba_trabalhe_conosco_herda_a_empresa_do_proprio_site() -> None:
    vaga = _vagas("https://www.tilibra.com.br/trabalhe-conosco")[0]

    assert vaga["hiringOrganization"]["name"] == "Tilibra"
    assert vaga["hiringOrganization"]["sameAs"] == "https://tilibra.com.br"
    assert vaga["_observatorio_empresa_origem"] == "site_proprio"


def test_sem_og_site_name_usa_o_nome_do_catalogo() -> None:
    from observatorio_vagas.extraction.listagem_carreiras import atribuir_empresa_do_site

    html = b'<select id="cargo-pretendido"><option>Analista</option></select>'
    url = "https://empresa.example/carreiras"
    vagas = extrair_vagas_listagem_carreiras(html, url=url).vagas

    resultado = atribuir_empresa_do_site(vagas, url=url, empresa_nome="Empresa Ltda", corpo=html)

    assert resultado[0]["hiringOrganization"]["name"] == "Empresa Ltda"


def test_agregador_consultoria_e_plataforma_nao_herdam_a_empresa() -> None:
    for url in (
        "https://www.michaelpage.com.br/jobs/barueri",
        "https://www.jobijoba.com.br/vagas-emprego/x",
        "https://app.talentbrand.com.br/jobs",
        "https://jobs.lever.co/empresa/vagas",
    ):
        assert "hiringOrganization" not in _vagas(url)[0], url


def test_pagina_que_nao_e_de_carreira_nao_herda_a_empresa() -> None:
    assert "hiringOrganization" not in _vagas("https://empresa.example/produtos/valvulas")[0]


def test_empresa_que_a_vaga_ja_traz_nao_e_trocada() -> None:
    from observatorio_vagas.extraction.listagem_carreiras import atribuir_empresa_do_site

    vaga = {"title": "Analista", "hiringOrganization": {"name": "Outra"}}

    resultado = atribuir_empresa_do_site(
        (vaga,), url="https://empresa.example/carreiras", empresa_nome="X", corpo=b""
    )

    assert resultado[0]["hiringOrganization"]["name"] == "Outra"


def test_banco_de_talentos_e_marcadores_entre_colchetes_nao_sao_vagas() -> None:
    html = (
        '<select id="cargo-pretendido"><option>[lista_vagas_dinamica]</option>'
        "<option>Banco de talentos (outras áreas)</option><option>Cadastre seu currículo</option>"
        "<option>Engenheiro Eletricista</option></select>"
    )

    assert _titulos(html) == ["Engenheiro Eletricista"]
