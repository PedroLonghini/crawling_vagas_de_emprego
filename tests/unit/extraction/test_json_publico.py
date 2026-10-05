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


def test_le_a_empresa_e_a_uf_e_ignora_os_pedacos_de_dentro_da_vaga() -> None:
    import json as _json

    from observatorio_vagas.extraction.json_publico import extrair_vagas_json_publico as extrair

    solides = {
        "data": [
            {
                "id": 116093,
                "title": "Auxiliar de Coordenação",
                "description": "<p>Apoiar a coordenação pedagógica.</p>",
                "companyName": "CCDA",
                "state": {"id": 20, "name": "São Paulo", "code": "SP"},
                "city": {"id": 3420, "name": "Diadema", "state_id": 20},
                "benefits": [
                    {"id": 1, "name": "Refeitório"},
                    {"id": 2, "name": "Vale alimentação"},
                ],
                "seniority": {"id": 3, "name": "Junior"},
            }
        ]
    }
    vagas = extrair(_json.dumps(solides).encode(), url="https://apigw.solides.com.br/jobs/v3")

    assert [v["title"] for v in vagas.vagas] == ["Auxiliar de Coordenação"]
    vaga = vagas.vagas[0]
    assert vaga["hiringOrganization"]["name"] == "CCDA"
    assert vaga["jobLocation"]["address"] == {"addressLocality": "Diadema", "addressRegion": "SP"}


def test_smartrecruiters_traz_empresa_e_pais() -> None:
    import json as _json

    from observatorio_vagas.extraction.json_publico import extrair_vagas_json_publico as extrair

    dados = {
        "content": [
            {
                "id": "744000152132239",
                "name": "Account Sales Manager",
                "company": {"identifier": "RedBull", "name": "Red Bull"},
                "location": {"city": "Sheffield", "region": "England", "country": "gb"},
            }
        ]
    }
    vaga = extrair(_json.dumps(dados).encode(), url="https://api.smartrecruiters.com/v1/x").vagas[0]

    assert vaga["hiringOrganization"]["name"] == "Red Bull"
    assert vaga["jobLocation"]["address"]["addressCountry"] == "gb"
