from observatorio_vagas.extraction.json_publico import extrair_vagas_json_publico


def test_extrai_vagas_de_listagem_json_publica() -> None:
    resultado = extrair_vagas_json_publico(
        b'{"jobs":[{"id":"42","title":"Analista de Dados","description":"Atua com dados.","detailUrl":"/vagas/42","location":"Sao Paulo - SP"}]}',
        url="https://empresa.example/api/jobs",
    )

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["identifier"] == "42"
    assert resultado.vagas[0]["url"] == "https://empresa.example/vagas/42"


def test_ignora_objeto_json_sem_sinal_estrutural_de_vaga() -> None:
    resultado = extrair_vagas_json_publico(
        b'{"company":{"name":"Empresa"}}', url="https://empresa.example/api/jobs"
    )
    assert resultado.vagas == ()
