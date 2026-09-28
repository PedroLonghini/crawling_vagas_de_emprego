"""Testes da conversão de JSON-LD para AnuncioVaga."""

from datetime import date, datetime

import pytest

from observatorio_vagas.domain.enums import (
    Fonte,
    StatusAnuncio,
)
from observatorio_vagas.extraction.mapeamento_json_ld import (
    converter_job_posting_em_anuncio,
)

HASH_TESTE = "a" * 64


def test_converte_job_posting_completo() -> None:
    """Os principais campos publicados devem ser preservados."""

    documento = {
        "@type": "JobPosting",
        "identifier": {
            "@type": "PropertyValue",
            "value": "vaga-123",
        },
        "title": "Senior Science Technician",
        "description": ("<p>Descrição completa da oportunidade.</p>"),
        "datePosted": "2026-08-11",
        "validThrough": ("2026-08-24T23:59:00+01:00"),
        "employmentType": [
            "FULL_TIME",
            "TEMPORARY",
        ],
        "url": ("https://empresa.example/jobs/vaga-123"),
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Empresa Exemplo",
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "Rua Exemplo, 100",
                "addressLocality": "London",
                "addressRegion": "London",
                "postalCode": "N17 9LN",
                "addressCountry": "GB",
            },
        },
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "GBP",
            "value": {
                "@type": "QuantitativeValue",
                "value": "£30,288 - £32,070",
                "unitText": "YEAR",
            },
        },
    }

    anuncio = converter_job_posting_em_anuncio(
        documento,
        fonte=Fonte.OUTRA,
        hash_conteudo=HASH_TESTE,
        referencia_bruta=("respostas/vaga-123.json"),
        # Simula a URL encontrada no HTML.
        url_candidatura=("https://empresa.example/jobs/vaga-123/apply"),
    )

    assert anuncio.id_externo == "vaga-123"

    assert anuncio.titulo_original == ("Senior Science Technician")

    assert anuncio.empresa_original == ("Empresa Exemplo")

    assert anuncio.endereco_original == ("Rua Exemplo, 100")

    assert anuncio.cep_original == "N17 9LN"

    assert anuncio.regime_original == ("FULL_TIME, TEMPORARY")

    assert anuncio.salario_original == ("£30,288 - £32,070 GBP YEAR")

    assert str(anuncio.url_candidatura) == ("https://empresa.example/jobs/vaga-123/apply")

    assert anuncio.publicado_em == date(
        2026,
        8,
        11,
    )

    assert anuncio.expira_em == datetime.fromisoformat("2026-08-24T23:59:00+01:00")

    assert anuncio.status == (StatusAnuncio.DESCOBERTO)

    assert anuncio.campos_estruturados["title"] == ("Senior Science Technician")


def test_usa_url_fallback_e_cria_identificador_estavel() -> None:
    """Uma vaga sem URL pode usar a URL coletada."""

    documento = {
        "@type": "JobPosting",
        "title": "Analista de Dados",
    }

    anuncio = converter_job_posting_em_anuncio(
        documento,
        fonte=Fonte.OUTRA,
        hash_conteudo=HASH_TESTE,
        referencia_bruta=("respostas/analista.json"),
        url_fallback=("https://empresa.example/jobs/analista"),
    )

    assert str(anuncio.url) == ("https://empresa.example/jobs/analista")

    assert len(anuncio.id_externo) == 50

    # Executar novamente com a mesma URL
    # deve gerar o mesmo identificador.
    segundo_anuncio = converter_job_posting_em_anuncio(
        documento,
        fonte=Fonte.OUTRA,
        hash_conteudo=HASH_TESTE,
        referencia_bruta=("respostas/analista.json"),
        url_fallback=("https://empresa.example/jobs/analista"),
    )

    assert segundo_anuncio.id_externo == (anuncio.id_externo)


def test_rejeita_job_posting_sem_titulo() -> None:
    """Sem título não é possível criar um anúncio válido."""

    documento = {
        "@type": "JobPosting",
        "url": ("https://empresa.example/jobs/sem-titulo"),
    }

    with pytest.raises(
        ValueError,
        match="não possui title",
    ):
        converter_job_posting_em_anuncio(
            documento,
            fonte=Fonte.OUTRA,
            hash_conteudo=HASH_TESTE,
            referencia_bruta=("respostas/sem-titulo.json"),
        )
