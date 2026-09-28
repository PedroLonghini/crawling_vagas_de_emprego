"""Testes dos anúncios originais e vagas canônicas."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import (
    Fonte,
    NaturezaSalario,
    PeriodoSalario,
    Senioridade,
)
from observatorio_vagas.domain.vaga import SalarioNormalizado, VagaCanonica


def test_anuncio_preserva_campos_originais() -> None:
    anuncio = AnuncioVaga(
        fonte=Fonte.EMPREGOS,
        id_externo="vaga-123",
        url="https://www.empregos.com.br/vaga/123",
        titulo_original="Analista de Dados",
        descricao_original="Descrição completa",
        salario_original="R$ 5.000 a R$ 7.000 por mês",
        hash_conteudo="a" * 64,
        referencia_bruta="empregos/2026/08/19/vaga-123.json",
    )

    assert anuncio.chave_fonte == (Fonte.EMPREGOS, "vaga-123")
    assert anuncio.salario_original == "R$ 5.000 a R$ 7.000 por mês"
    assert anuncio.hash_conteudo == "a" * 64


def test_anuncio_exige_hash_sha256() -> None:
    with pytest.raises(ValidationError, match="SHA-256"):
        AnuncioVaga(
            fonte=Fonte.GUPY,
            id_externo="123",
            url="https://empresa.gupy.io/jobs/123",
            titulo_original="Pessoa Desenvolvedora",
            hash_conteudo="hash-curto",
            referencia_bruta="gupy/123.html",
        )


def test_salario_normalizado_valida_faixa() -> None:
    with pytest.raises(ValidationError, match="mínimo não pode ser maior"):
        SalarioNormalizado(
            natureza=NaturezaSalario.PUBLICADO,
            periodo=PeriodoSalario.MES,
            minimo=Decimal("7000"),
            maximo=Decimal("5000"),
        )


def test_salario_mensal_exige_regra_de_normalizacao() -> None:
    with pytest.raises(ValidationError, match="regra_normalizacao"):
        SalarioNormalizado(
            natureza=NaturezaSalario.CALCULADO,
            periodo=PeriodoSalario.ANO,
            minimo=Decimal("60000"),
            mensal_minimo=Decimal("5000"),
        )


def test_vaga_canonica_remove_tecnologias_duplicadas() -> None:
    salario = SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        periodo=PeriodoSalario.MES,
        minimo=Decimal("5000"),
        maximo=Decimal("7000"),
    )
    vaga = VagaCanonica(
        empresa_id=uuid4(),
        titulo_normalizado="Analista de Dados",
        senioridade=Senioridade.PLENO,
        salario=salario,
        tecnologias=["Python", " python ", "SQL"],
    )

    assert vaga.tecnologias == ("Python", "SQL")
    assert vaga.salario is not None
    assert vaga.salario.minimo == Decimal("5000")


def test_anuncio_preserva_campos_para_futura_publicacao() -> None:
    """Verifica os campos que poderão alimentar o payload do Empregos."""

    anuncio = AnuncioVaga(
        # Identificação da origem.
        fonte=Fonte.PAGINA_CARREIRAS,
        id_externo="VAGA-EXTERNA-123",
        # Página utilizada para descobrir e ler a vaga.
        url=("https://tecnologia-atlas.example.com/carreiras/vaga-123"),
        # Página para a qual o candidato será direcionado.
        url_candidatura=("https://candidaturas.example.com/apply/vaga-123"),
        # Conteúdo original.
        titulo_original="Desenvolvedor Python Pleno",
        descricao_original=(
            "Buscamos uma pessoa desenvolvedora para trabalhar com Python, APIs e bancos de dados."
        ),
        empresa_original="Tecnologia Atlas",
        localidade_original="São Paulo, SP",
        endereco_original="Avenida Exemplo, 100, São Paulo, SP",
        cep_original="01000-000",
        geolocalizacao_original="-23.5505,-46.6333",
        salario_original="R$ 6.500 a R$ 8.500 por mês",
        modalidade_original="Trabalho híbrido",
        regime_original="CLT",
        senioridade_original="Pleno",
        responsabilidades_original=("Desenvolver APIs e manter sistemas existentes."),
        requisitos_original=("Conhecimento em Python, SQL e desenvolvimento de APIs."),
        beneficios_original=("Vale-refeição, plano de saúde e auxílio home office."),
        numero_vagas_original=2,
        publicado_em=date(2026, 8, 19),
        expira_em=date(2026, 9, 19),
        # Hash e referência continuam obrigatórios porque permitem
        # detectar alterações e recuperar o conteúdo original.
        hash_conteudo="b" * 64,
        referencia_bruta=("pagina_carreiras/2026/08/19/vaga-123.html"),
    )

    # HttpUrl é convertido para um tipo especial do Pydantic.
    assert str(anuncio.url_candidatura) == ("https://candidaturas.example.com/apply/vaga-123")

    assert anuncio.cep_original == "01000-000"
    assert anuncio.geolocalizacao_original == ("-23.5505,-46.6333")
    assert anuncio.senioridade_original == "Pleno"
    assert anuncio.numero_vagas_original == 2
    assert anuncio.expira_em == date(2026, 9, 19)

    # Confirmamos que informações importantes não foram perdidas.
    assert "Python" in anuncio.requisitos_original
    assert "plano de saúde" in anuncio.beneficios_original


def test_vaga_canonica_guarda_informacoes_normalizadas() -> None:
    """Verifica os novos campos normalizados da vaga."""

    vaga = VagaCanonica(
        empresa_id=uuid4(),
        titulo_normalizado="Desenvolvedor Python Pleno",
        descricao_normalizada=(
            "Desenvolvimento e manutenção de APIs utilizando Python e bancos de dados relacionais."
        ),
        cidade="São Paulo",
        estado="SP",
        pais="BR",
        endereco="Avenida Exemplo, 100, São Paulo, SP",
        cep="01000-000",
        latitude=-23.5505,
        longitude=-46.6333,
        quantidade_vagas=2,
        tecnologias=[
            "Python",
            "SQL",
            " python ",
        ],
        requisitos=[
            "Experiência com Python",
            "Conhecimento em SQL",
        ],
        responsabilidades=[
            "Desenvolver APIs",
            "Manter sistemas existentes",
            " desenvolver APIs ",
        ],
        beneficios=[
            "Plano de saúde",
            "Vale-refeição",
        ],
    )

    # O limpador existente remove duplicações,
    # ignorando diferenças entre maiúsculas e minúsculas.
    assert vaga.tecnologias == (
        "Python",
        "SQL",
    )

    assert vaga.responsabilidades == (
        "Desenvolver APIs",
        "Manter sistemas existentes",
    )

    assert vaga.quantidade_vagas == 2
    assert vaga.latitude == -23.5505
    assert vaga.longitude == -46.6333
    assert "Plano de saúde" in vaga.beneficios


def test_vaga_exige_latitude_e_longitude_juntas() -> None:
    """Impede uma geolocalização incompleta."""

    with pytest.raises(
        ValidationError,
        match="latitude e longitude devem ser informadas juntas",
    ):
        VagaCanonica(
            empresa_id=uuid4(),
            titulo_normalizado="Analista de Dados",
            # Informamos latitude, mas esquecemos a longitude.
            latitude=-23.5505,
        )
