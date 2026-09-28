"""Testes do extrator genérico de vagas em JSON-LD."""

import json

from observatorio_vagas.extraction.json_ld import (
    extrair_job_postings_json_ld,
)


def test_lista_aninhada_extrai_varias_vagas_sem_transformar_breadcrumb_em_vaga() -> None:
    dados = {
        "@graph": [
            {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "name": "Home"}]},
            {
                "@type": "ItemList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "item": {
                            "@type": "JobPosting",
                            "title": titulo,
                            "url": f"https://empresa.example/{numero}",
                        },
                    }
                    for numero, titulo in enumerate(("Analista", "Técnico", "Estagiário"))
                ],
            },
        ]
    }
    resultado = extrair_job_postings_json_ld(
        f'<script type="application/ld+json">{json.dumps(dados)}</script>'
    )
    assert [vaga["title"] for vaga in resultado.vagas] == ["Analista", "Técnico", "Estagiário"]


def test_extrai_job_posting_simples() -> None:
    """Uma vaga estruturada deve ser encontrada dentro do HTML."""

    html = """
    <html>
        <body>
            <script type="application/ld+json">
                {
                    "@context": "https://schema.org",
                    "@type": "JobPosting",
                    "title": "Pessoa Desenvolvedora Python",
                    "datePosted": "2026-08-24"
                }
            </script>
        </body>
    </html>
    """

    resultado = extrair_job_postings_json_ld(html)

    assert resultado.blocos_encontrados == 1
    assert resultado.blocos_invalidos == 0
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Pessoa Desenvolvedora Python"


def test_ignora_documento_que_nao_representa_vaga() -> None:
    """Uma empresa estruturada não deve ser confundida com uma vaga."""

    html = """
    <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "Empresa Exemplo"
        }
    </script>
    """

    resultado = extrair_job_postings_json_ld(html)

    assert resultado.blocos_encontrados == 1
    assert resultado.blocos_invalidos == 0
    assert resultado.vagas == ()


def test_encontra_vaga_dentro_de_graph() -> None:
    """O extrator deve entender documentos agrupados em @graph."""

    html = """
    <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Organization",
                    "name": "Empresa Exemplo"
                },
                {
                    "@type": ["Thing", "JobPosting"],
                    "title": "Analista de Dados"
                }
            ]
        }
    </script>
    """

    resultado = extrair_job_postings_json_ld(html)

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Analista de Dados"


def test_bloco_invalido_nao_impede_outro_bloco_valido() -> None:
    """Um JSON quebrado não deve inutilizar as demais vagas da página."""

    html = """
    <script type="application/ld+json">
        {"@type": "JobPosting",
    </script>

    <script type="application/ld+json">
        {
            "@type": "JobPosting",
            "title": "Engenheira de Software"
        }
    </script>
    """

    resultado = extrair_job_postings_json_ld(html)

    assert resultado.blocos_encontrados == 2
    assert resultado.blocos_invalidos == 1
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Engenheira de Software"


def test_aceita_conteudo_em_bytes() -> None:
    """O extrator deve aceitar o formato usado no armazenamento bruto."""

    html = """
    <script type="application/ld+json">
        {
            "@type": "JobPosting",
            "title": "Técnica de Laboratório"
        }
    </script>
    """.encode()

    resultado = extrair_job_postings_json_ld(
        html,
        codificacao="utf-8",
    )

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Técnica de Laboratório"


def test_aceita_json_ld_codificado_com_entidades_html() -> None:
    """O formato HTML escapado usado pela Gupy deve ser aceito."""

    html = """
    <script type="application/ld+json">
        {&quot;@context&quot;:&quot;https://schema.org&quot;,
        &quot;@type&quot;:&quot;JobPosting&quot;,
        &quot;title&quot;:&quot;Pessoa Desenvolvedora Backend&quot;,
        &quot;employmentType&quot;:&quot;FULL_TIME&quot;}
    </script>
    """

    resultado = extrair_job_postings_json_ld(
        html,
    )

    assert resultado.blocos_encontrados == 1
    assert resultado.blocos_invalidos == 0
    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == ("Pessoa Desenvolvedora Backend")
    assert resultado.vagas[0]["employmentType"] == "FULL_TIME"
