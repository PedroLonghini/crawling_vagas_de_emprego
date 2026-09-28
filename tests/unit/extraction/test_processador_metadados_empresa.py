"""Teste da integração entre processamento e metadados da empresa."""

from datetime import UTC, datetime
from pathlib import Path

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
from observatorio_vagas.extraction.processador import (
    processar_respostas_brutas,
)


def test_processador_enriquece_hiring_organization(
    tmp_path: Path,
) -> None:
    """O processador deve preservar no anúncio os metadados HTML."""

    html = """
        <html>
            <body>
                <div class="header-with-logo-logo">
                    <img src="/logos/empresa.png">
                </div>

                <section id="company-overview">
                    <a
                        data-link-type="company_website"
                        href="https://empresa.example.com"
                    >
                        Site da empresa
                    </a>

                    <p>
                        Empresa especializada em tecnologia,
                        produtos digitais e desenvolvimento
                        de sistemas corporativos.
                    </p>
                </section>

                <script type="application/ld+json">
                    {
                        "@context": "https://schema.org",
                        "@type": "JobPosting",
                        "identifier": {
                            "value": "vaga-metadados-001"
                        },
                        "title": "Pessoa Desenvolvedora Python",
                        "description": "Desenvolvimento de aplicações.",
                        "url": "https://vagas.example.com/jobs/001",
                        "hiringOrganization": {
                            "@type": "Organization",
                            "name": "Empresa Exemplo",
                            "description": null
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

    resultado = processar_respostas_brutas(
        diretorio_raw,
    )

    assert resultado.falhas == ()
    assert len(resultado.anuncios) == 1

    anuncio = resultado.anuncios[0]

    organizacao = anuncio.campos_estruturados["hiringOrganization"]

    assert organizacao["name"] == ("Empresa Exemplo")

    assert organizacao["description"] == (
        "Empresa especializada em tecnologia, "
        "produtos digitais e desenvolvimento "
        "de sistemas corporativos."
    )

    assert organizacao["sameAs"] == ("https://empresa.example.com")

    assert organizacao["logo"] == ("https://vagas.example.com/logos/empresa.png")
