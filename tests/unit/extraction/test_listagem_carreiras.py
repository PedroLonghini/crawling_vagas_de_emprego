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
