"""Testes dos adaptadores para APIs licenciadas de agregadores."""

from observatorio_vagas.crawling.adaptadores.agregadores import (
    AdaptadorAdzunaApi,
    AdaptadorJoobleApi,
)


def test_adzuna_converte_resultado_completo_em_json_ld() -> None:
    """O adaptador preserva os campos úteis sem depender de HTML."""

    vagas = AdaptadorAdzunaApi().extrair_job_postings(
        {
            "results": [
                {
                    "id": "adz-123",
                    "title": "Pessoa Desenvolvedora Python",
                    "description": "Trabalhe com produtos públicos.",
                    "redirect_url": "https://www.adzuna.com.br/details/123",
                    "created": "2026-09-14T12:00:00Z",
                    "contract_type": "full_time",
                    "contract_time": "permanent",
                    "salary_min": 6000,
                    "salary_max": 8000,
                    "company": {"display_name": "Empresa Exemplo"},
                    "location": {"display_name": "São Paulo, SP"},
                    "category": {"label": "Tecnologia"},
                    "latitude": -23.5505,
                    "longitude": -46.6333,
                }
            ]
        }
    )

    assert len(vagas) == 1
    assert vagas[0]["@type"] == "JobPosting"
    assert vagas[0]["identifier"]["value"] == "adz-123"
    assert vagas[0]["hiringOrganization"]["name"] == "Empresa Exemplo"
    assert vagas[0]["baseSalary"]["value"]["minValue"] == 6000
    assert vagas[0]["occupationalCategory"] == "Tecnologia"
    assert vagas[0]["jobLocation"]["geo"]["latitude"] == -23.5505


def test_jooble_descarta_item_sem_url_publica() -> None:
    """Uma vaga sem URL não pode ser republicada nem entrar no pipeline."""

    vagas = AdaptadorJoobleApi().extrair_job_postings(
        {
            "jobs": [
                {"id": "sem-link", "title": "Analista"},
                {
                    "id": "job-456",
                    "title": "Analista de Dados",
                    "snippet": "Atuação remota.",
                    "link": "https://br.jooble.org/desc/456",
                    "company": "Empresa Dados",
                    "location": "Remoto",
                },
            ]
        }
    )

    assert len(vagas) == 1
    assert vagas[0]["identifier"]["name"] == "jooble"
    assert vagas[0]["jobLocation"]["address"]["addressLocality"] == "Remoto"
