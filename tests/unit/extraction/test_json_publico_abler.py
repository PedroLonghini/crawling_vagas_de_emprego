"""Cobertura da API pública de vagas da Abler."""

import json

from observatorio_vagas.extraction.json_publico import extrair_vagas_json_publico


def test_extrai_vaga_abler_com_url_publica_de_candidatura() -> None:
    corpo = json.dumps(
        {
            "data": [
                {
                    "id": "42",
                    "attributes": {
                        "title": "Analista de Dados",
                        "description": "Descricao",
                        "full_url": "https://empresa.abler.com.br/vagas/analista",
                        "company_name": "Empresa",
                        "city": "Sao Paulo",
                        "state": "SP",
                    },
                },
            ],
        }
    ).encode()
    resultado = extrair_vagas_json_publico(
        corpo,
        url="https://hulk-smash.abler.com.br/api/company/v1/careers_pages/empresa/vacancies?page=1",
    )

    assert resultado.vagas == (
        {
            "@type": "JobPosting",
            "identifier": "abler-42",
            "title": "Analista de Dados",
            "description": "Descricao",
            "url": "https://empresa.abler.com.br/vagas/analista",
            "_observatorio_apply_url": "https://empresa.abler.com.br/vagas/analista",
            "_observatorio_extrator": "abler_api_publica",
            "_observatorio_atributos": {
                "title": "Analista de Dados",
                "description": "Descricao",
                "full_url": "https://empresa.abler.com.br/vagas/analista",
                "company_name": "Empresa",
                "city": "Sao Paulo",
                "state": "SP",
            },
            "hiringOrganization": {"@type": "Organization", "name": "Empresa"},
            "jobLocation": {
                "@type": "Place",
                "address": {"addressLocality": "Sao Paulo, SP"},
            },
        },
    )
