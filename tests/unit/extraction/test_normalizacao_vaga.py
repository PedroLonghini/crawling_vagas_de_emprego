"""Testes da conversão de anúncios em vagas canônicas."""

from decimal import Decimal
from uuid import uuid4

import pytest

from observatorio_vagas.domain.anuncio import (
    AnuncioVaga,
)
from observatorio_vagas.domain.enums import (
    Fonte,
    ModalidadeTrabalho,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.extraction.normalizacao_vaga import (
    ErroNormalizacaoVaga,
    converter_anuncio_em_vaga_canonica,
)


def criar_anuncio(
    *,
    associado: bool = True,
) -> AnuncioVaga:
    """Cria um anúncio semelhante à vaga britânica coletada."""

    return AnuncioVaga(
        fonte=Fonte.OUTRA,
        id_externo="140935",
        url=("https://teaching-vacancies.service.gov.uk/jobs/140935"),
        empresa_id=(uuid4() if associado else None),
        titulo_original=("Senior Science Technician"),
        descricao_original=("<p>Apoiar o laboratório.</p><p>Organizar materiais.</p>"),
        empresa_original=("Harris Academy Tottenham"),
        regime_original=("FULL_TIME, TEMPORARY"),
        beneficios_original=("Pension scheme"),
        hash_conteudo="b" * 64,
        referencia_bruta=("corpos/140935.bin"),
        campos_estruturados={
            "@type": "JobPosting",
            "totalJobOpenings": 2,
            "jobLocationType": ("TELECOMMUTE"),
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": ("PostalAddress"),
                    "streetAddress": ("Ashley Road"),
                    "addressLocality": ("London"),
                    "addressRegion": ("England"),
                    "postalCode": ("N17 9LN"),
                    "addressCountry": "GB",
                },
            },
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "GBP",
                "value": {
                    "@type": ("QuantitativeValue"),
                    "minValue": 25000,
                    "maxValue": 30000,
                    "unitText": "YEAR",
                },
            },
            "qualifications": ("Experiência em laboratório"),
            "responsibilities": ("Preparar equipamentos"),
        },
    )


def test_exige_empresa_associada() -> None:
    """Uma vaga não pode existir sem empresa canônica."""

    with pytest.raises(
        ErroNormalizacaoVaga,
        match="empresa_id",
    ):
        converter_anuncio_em_vaga_canonica(criar_anuncio(associado=False))


def test_conversao_gera_uuid_deterministico() -> None:
    """Reprocessar o anúncio não deve duplicar a vaga."""

    anuncio = criar_anuncio()

    primeira = converter_anuncio_em_vaga_canonica(anuncio)

    segunda = converter_anuncio_em_vaga_canonica(anuncio)

    assert primeira.id == segunda.id

    assert primeira.empresa_id == anuncio.empresa_id


def test_converte_campos_principais() -> None:
    """Localização e descrição devem ser normalizadas."""

    vaga = converter_anuncio_em_vaga_canonica(criar_anuncio())

    assert vaga.titulo_normalizado == "Senior Science Technician"

    assert vaga.descricao_normalizada == ("Apoiar o laboratório.\n\nOrganizar materiais.")

    assert vaga.cidade == "London"
    assert vaga.estado == "England"
    assert vaga.pais == "GB"

    assert vaga.endereco == ("Ashley Road, London, England")

    assert vaga.cep == "N17 9LN"
    assert vaga.quantidade_vagas == 2

    assert vaga.senioridade is Senioridade.SENIOR

    assert vaga.modalidade is ModalidadeTrabalho.REMOTO

    assert vaga.regime is RegimeContratacao.TEMPORARIO

    assert "Preparar equipamentos" in vaga.responsabilidades

    assert "Experiência em laboratório" in vaga.requisitos

    assert "Pension scheme" in vaga.beneficios


def test_localizacao_sem_pais_nao_assume_brasil() -> None:
    """Uma cidade estrangeira sem addressCountry deve continuar desconhecida."""

    anuncio = criar_anuncio()
    documento = dict(anuncio.campos_estruturados)
    local = dict(documento["jobLocation"])
    endereco = dict(local["address"])
    endereco.pop("addressCountry")
    endereco["addressLocality"] = "Buenos Aires"
    endereco["addressRegion"] = None
    local["address"] = endereco
    documento["jobLocation"] = local

    vaga = converter_anuncio_em_vaga_canonica(
        anuncio.model_copy(update={"campos_estruturados": documento})
    )

    assert vaga.pais == ""


def test_localizacao_brasileira_sem_pais_e_reconhecida_por_uf() -> None:
    """A ausência de addressCountry não descarta uma UF brasileira explícita."""

    anuncio = criar_anuncio()
    documento = dict(anuncio.campos_estruturados)
    local = dict(documento["jobLocation"])
    endereco = dict(local["address"])
    endereco.pop("addressCountry")
    endereco["addressLocality"] = "São Paulo"
    endereco["addressRegion"] = "SP"
    local["address"] = endereco
    documento["jobLocation"] = local

    vaga = converter_anuncio_em_vaga_canonica(
        anuncio.model_copy(update={"campos_estruturados": documento})
    )

    assert vaga.pais == "BR"


def test_extrai_cidade_e_estado_da_localidade_simples_brasileira() -> None:
    """ATS pode entregar apenas ``Cidade - UF`` fora do Schema.org."""

    anuncio = criar_anuncio().model_copy(
        update={
            "localidade_original": "Uberlândia - MG",
            "endereco_original": None,
            "campos_estruturados": {},
        }
    )

    vaga = converter_anuncio_em_vaga_canonica(anuncio)

    assert vaga.cidade == "Uberlândia"
    assert vaga.estado == "MG"
    assert vaga.pais == "BR"


def test_normaliza_salario_publicado() -> None:
    """A faixa anual deve continuar identificada como anual."""

    vaga = converter_anuncio_em_vaga_canonica(criar_anuncio())

    assert vaga.salario is not None
    assert vaga.salario.moeda == "GBP"

    assert vaga.salario.periodo is PeriodoSalario.ANO

    assert vaga.salario.minimo == Decimal("25000")

    assert vaga.salario.maximo == Decimal("30000")

    assert vaga.salario.mensal_minimo == (Decimal("25000") / Decimal("12"))

    assert vaga.salario.mensal_maximo == Decimal("2500")


def test_full_time_estrangeiro_nao_vira_clt() -> None:
    """Um contrato estrangeiro não pode ser rotulado como CLT."""

    anuncio = criar_anuncio().model_copy(
        update={
            "regime_original": "FULL_TIME",
        }
    )

    vaga = converter_anuncio_em_vaga_canonica(anuncio)

    assert vaga.regime is RegimeContratacao.OUTRO


def test_normaliza_faixa_salarial_publicada_com_simbolos() -> None:
    """Uma faixa monetária textual não deve ser descartada."""

    anuncio = criar_anuncio()

    # Criamos uma cópia para não alterar o anúncio original.
    campos_estruturados = dict(anuncio.campos_estruturados)

    # Este é o formato encontrado na vaga real.
    campos_estruturados["baseSalary"] = {
        "@type": "MonetaryAmount",
        "currency": "GBP",
        "value": {
            "@type": "QuantitativeValue",
            "value": ("£30,288 - £32,070"),
            "unitText": "YEAR",
        },
    }

    anuncio_com_faixa = anuncio.model_copy(
        update={
            "campos_estruturados": (campos_estruturados),
        }
    )

    vaga = converter_anuncio_em_vaga_canonica(anuncio_com_faixa)

    assert vaga.salario is not None
    assert vaga.salario.moeda == "GBP"

    assert vaga.salario.periodo is PeriodoSalario.ANO

    assert vaga.salario.minimo == Decimal("30288")

    assert vaga.salario.maximo == Decimal("32070")

    assert vaga.salario.mensal_minimo == (Decimal("30288") / Decimal("12"))

    assert vaga.salario.mensal_maximo == (Decimal("32070") / Decimal("12"))


def test_normaliza_modalidade_e_senioridade_da_gupy() -> None:
    """Sinais públicos da Gupy devem ser normalizados."""

    anuncio = criar_anuncio()

    campos_estruturados = dict(anuncio.campos_estruturados)

    campos_estruturados.pop(
        "jobLocationType",
        None,
    )

    local_original = campos_estruturados["jobLocation"]

    assert isinstance(local_original, dict)

    local = dict(local_original)

    local["additionalProperty"] = {
        "@type": "PropertyValue",
        "value": "TELECOMMUTE",
    }

    campos_estruturados["jobLocation"] = local

    anuncio_gupy = anuncio.model_copy(
        update={
            "titulo_original": ("Pessoa Desenvolvedora Backend Pl | Remoto"),
            "modalidade_original": None,
            "senioridade_original": None,
            "campos_estruturados": campos_estruturados,
        }
    )

    vaga = converter_anuncio_em_vaga_canonica(anuncio_gupy)

    assert vaga.modalidade is ModalidadeTrabalho.REMOTO
    assert vaga.senioridade is Senioridade.PLENO


def test_descricao_preserva_paragrafos_listas_e_quebras() -> None:
    """HTML da descrição deve virar texto com parágrafos e itens."""

    anuncio = criar_anuncio().model_copy(
        update={
            "descricao_original": (
                "<style>.x{color:red}</style>"
                "<h2>Sobre a vaga</h2><p>Atuar no   laboratório.<br>Turno diurno.</p>"
                "<ul><li>Organizar materiais</li><li>Preparar&nbsp;equipamentos</li></ul>"
                "<script>alert(1)</script>"
            ),
        },
    )

    vaga = converter_anuncio_em_vaga_canonica(anuncio)

    assert vaga.descricao_normalizada == (
        "Sobre a vaga\n\n"
        "Atuar no laboratório.\nTurno diurno.\n\n"
        "- Organizar materiais\n- Preparar equipamentos"
    )


def test_descricao_texto_puro_mantem_quebras_de_linha() -> None:
    """Descrições já em texto puro não devem perder parágrafos."""

    anuncio = criar_anuncio().model_copy(
        update={"descricao_original": "Primeiro parágrafo.\n\n\n\nSegundo   parágrafo."},
    )

    vaga = converter_anuncio_em_vaga_canonica(anuncio)

    assert vaga.descricao_normalizada == "Primeiro parágrafo.\n\nSegundo parágrafo."
