"""Testes para layouts próprios de empresas."""

from observatorio_vagas.extraction.carreiras_empresas import extrair_vagas_carreiras_empresas


def test_procatalogo_separa_tres_cargos_e_tres_formularios() -> None:
    cartoes = "".join(
        f'<div class="elementor-inner-column"><h4 class="elementor-icon-box-title">{titulo}</h4>'
        '<p class="elementor-icon-box-description">Esta é uma vaga para trabalho remoto.</p>'
        f'<a href="https://forms.gle/{identificador}">Quero me candidatar</a></div>'
        for titulo, identificador in (("Marketing", "aaa"), ("Vendas", "bbb"), ("Suporte", "ccc"))
    )
    resultado = extrair_vagas_carreiras_empresas(
        cartoes.encode(), url="https://procatalogo.com.br/trabalhe-conosco/"
    )
    assert len(resultado.vagas) == 3
    assert len({v["identifier"] for v in resultado.vagas}) == 3
    assert [v["_observatorio_apply_url"] for v in resultado.vagas] == [
        "https://forms.gle/aaa", "https://forms.gle/bbb", "https://forms.gle/ccc"
    ]


def test_ari_preserva_empresa_da_vaga_e_url_propria() -> None:
    html = """
    <main><h1>Serviços Gerais - UniAri</h1>
    <div><h2>Requisitos</h2><p>Ensino fundamental e experiência na área.</p></div>
    <div><h2>Responsabilidades da Vaga</h2>
    <p>Limpeza e manutenção dos ambientes físicos da instituição.</p></div>
    <div><h2>Benefícios</h2><p>Vale alimentação e vale transporte.</p></div>
    <div>Status: Aberta Localização: Fortaleza - CE Empresa: UniAri - Centro Universitário</div>
    </main>
    """
    resultado = extrair_vagas_carreiras_empresas(
        html.encode(), url="https://trabalheconosco.aridesa.com.br/16"
    )
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["hiringOrganization"]["name"] == "UniAri - Centro Universitário"
    assert resultado.vagas[0]["_observatorio_apply_url"] == (
        "https://trabalheconosco.aridesa.com.br/16"
    )


def test_ari_nao_extrai_vaga_fechada() -> None:
    html = "<main><h1>Analista</h1><div>Status: Encerrada</div></main>"
    resultado = extrair_vagas_carreiras_empresas(
        html.encode(), url="https://trabalheconosco.aridesa.com.br/16"
    )
    assert resultado.vagas == ()


def test_estrela_extrai_descricao_completa_e_cidade() -> None:
    html = """
    <main><h1>Auxiliar Financeiro</h1>
    <span><i data-lucide="map-pin"></i> Jacareí</span>
    <div><h2>Descrição da Vaga</h2><div class="prose-job">
    Atendimento financeiro, conciliação bancária e organização de documentos.
    </div></div>
    <div><h2>Requisitos</h2><div class="prose-job">
    Conhecimento em Excel e experiência em rotinas administrativas.
    </div></div></main>
    """
    resultado = extrair_vagas_carreiras_empresas(
        html.encode(), url="https://rh.estreladolar.com.br/jacarei/auxiliar-financeiro"
    )
    assert len(resultado.vagas) == 1
    assert "conciliação bancária" in resultado.vagas[0]["description"]
    assert resultado.vagas[0]["jobLocation"]["address"]["addressLocality"] == "Jacareí"


def test_estrela_exclui_banco_de_talentos() -> None:
    html = b'<main><h1>BANCO Auxiliar</h1><h2>Descricao</h2></main>'
    resultado = extrair_vagas_carreiras_empresas(
        html, url="https://rh.estreladolar.com.br/jacarei/banco-auxiliar"
    )
    assert resultado.vagas == ()
