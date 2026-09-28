"""Teste integrado da geolocalização de uma vaga."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from observatorio_vagas.crawling.contracts import (
    RespostaBruta,
)
from observatorio_vagas.crawling.raw_storage import (
    ArmazenamentoBrutoLocal,
)
from observatorio_vagas.domain.enums import (
    Fonte,
    TipoPaginaColeta,
)
from observatorio_vagas.extraction import (
    converter_anuncio_em_vaga_canonica,
    processar_respostas_brutas,
)


def test_processa_e_normaliza_geolocalizacao(
    tmp_path: Path,
) -> None:
    """As coordenadas do mapa devem chegar à vaga canônica."""

    html = """
        <html>
            <body>
                <div
                    data-marker-type="organisation"
                    data-point='{
                        "type": "Point",
                        "coordinates": [
                            -0.06023406798549122,
                            51.591316680055165
                        ]
                    }'
                >
                </div>

                <script type="application/ld+json">
                    {
                        "@context": "https://schema.org",
                        "@type": "JobPosting",
                        "identifier": {
                            "value": "vaga-geo-001"
                        },
                        "title": "Senior Science Technician",
                        "description": "Descrição completa da vaga.",
                        "url": "https://vagas.example.com/jobs/001",
                        "jobLocation": {
                            "@type": "Place",
                            "address": {
                                "@type": "PostalAddress",
                                "streetAddress": "Ashley Road",
                                "addressLocality": "London",
                                "addressRegion": "London",
                                "postalCode": "N17 9LN",
                                "addressCountry": "GB"
                            }
                        },
                        "hiringOrganization": {
                            "@type": "Organization",
                            "name": "Empresa Exemplo"
                        }
                    }
                </script>
            </body>
        </html>
    """.encode()

    diretorio_raw = tmp_path / "raw"

    armazenamento = ArmazenamentoBrutoLocal(
        diretorio_raw,
    )

    armazenamento.salvar(
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada=("https://vagas.example.com/jobs/001"),
            url_final=("https://vagas.example.com/jobs/001"),
            status_http=200,
            corpo=html,
            alvo_id="empresa_exemplo",
            empresa_nome="Empresa Exemplo",
            numero_pagina=1,
            tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
            tipo_conteudo=("text/html; charset=utf-8"),
            codificacao="utf-8",
            coletado_em=datetime(
                2026,
                8,
                26,
                12,
                0,
                tzinfo=UTC,
            ),
        )
    )

    processamento = processar_respostas_brutas(
        diretorio_raw,
    )

    assert processamento.falhas == ()
    assert len(processamento.anuncios) == 1

    anuncio = processamento.anuncios[0]

    geo = anuncio.campos_estruturados["jobLocation"]["geo"]

    assert geo["latitude"] == (51.591316680055165)

    assert geo["longitude"] == (-0.06023406798549122)

    # A normalização exige uma empresa associada.
    anuncio_associado = anuncio.model_copy(
        update={
            "empresa_id": uuid4(),
        }
    )

    vaga = converter_anuncio_em_vaga_canonica(
        anuncio_associado,
    )

    assert vaga.latitude == (51.591316680055165)

    assert vaga.longitude == (-0.06023406798549122)
