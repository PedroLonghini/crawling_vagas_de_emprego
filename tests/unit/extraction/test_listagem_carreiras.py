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
