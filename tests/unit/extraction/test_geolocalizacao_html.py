"""Testes da extração de coordenadas presentes no HTML."""

from observatorio_vagas.extraction.geolocalizacao_html import (
    enriquecer_job_posting_com_geolocalizacao,
    extrair_coordenadas_html,
)


def test_extrai_coordenadas_geojson_do_mapa() -> None:
    """GeoJSON usa longitude antes de latitude."""

    html = """
        <div
            class="markers__marker"
            data-marker-type="organisation"
            data-point="{&quot;type&quot;:&quot;Point&quot;,
                &quot;coordinates&quot;:
                [-0.06023406798549122,51.591316680055165]}"
        >
        </div>
    """

    resultado = extrair_coordenadas_html(
        html,
    )

    assert resultado is not None

    assert resultado.latitude == (51.591316680055165)

    assert resultado.longitude == (-0.06023406798549122)

    assert resultado.evidencia == ("geojson_data_point")


def test_rejeita_coordenadas_fora_dos_limites() -> None:
    """Latitude e longitude impossíveis não podem ser aceitas."""

    html = """
        <div
            data-point='{
                "type": "Point",
                "coordinates": [200, 100]
            }'
        >
        </div>
    """

    resultado = extrair_coordenadas_html(
        html,
    )

    assert resultado is None


def test_pagina_sem_mapa_nao_inventa_coordenadas() -> None:
    """Uma página sem evidência geográfica deve continuar vazia."""

    html = """
        <html>
            <body>
                <p>Vaga em Londres.</p>
            </body>
        </html>
    """

    resultado = extrair_coordenadas_html(
        html,
    )

    assert resultado is None


def test_enriquece_job_location_sem_geo() -> None:
    """As coordenadas encontradas devem entrar em jobLocation.geo."""

    html = """
        <div
            data-marker-type="organization"
            data-point='{
                "type": "Point",
                "coordinates": [-46.6333, -23.5505]
            }'
        >
        </div>
    """

    coordenadas = extrair_coordenadas_html(
        html,
    )

    documento = {
        "@type": "JobPosting",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "São Paulo",
            },
        },
    }

    resultado = enriquecer_job_posting_com_geolocalizacao(
        documento,
        coordenadas,
    )

    local = resultado["jobLocation"]
    geo = local["geo"]

    assert geo == {
        "@type": "GeoCoordinates",
        "latitude": -23.5505,
        "longitude": -46.6333,
    }

    # O documento recebido não deve ser modificado.
    local_original = documento["jobLocation"]

    assert "geo" not in local_original


def test_preserva_coordenadas_validas_do_json_ld() -> None:
    """Coordenadas estruturadas possuem prioridade sobre o HTML."""

    html = """
        <div
            data-point='{
                "type": "Point",
                "coordinates": [-46.6333, -23.5505]
            }'
        >
        </div>
    """

    coordenadas = extrair_coordenadas_html(
        html,
    )

    documento = {
        "@type": "JobPosting",
        "jobLocation": {
            "@type": "Place",
            "geo": {
                "@type": "GeoCoordinates",
                "latitude": -22.9068,
                "longitude": -43.1729,
            },
        },
    }

    resultado = enriquecer_job_posting_com_geolocalizacao(
        documento,
        coordenadas,
    )

    geo = resultado["jobLocation"]["geo"]

    assert geo["latitude"] == -22.9068
    assert geo["longitude"] == -43.1729
