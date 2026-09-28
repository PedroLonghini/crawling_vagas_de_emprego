"""Testes da conversão entre modelos de domínio e BSON."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from bson.decimal128 import Decimal128

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    Fonte,
    NaturezaSalario,
    PeriodoSalario,
)
from observatorio_vagas.domain.vaga import (
    SalarioNormalizado,
    VagaCanonica,
)
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)


def test_empresa_pode_ir_e_voltar_do_mongodb() -> None:
    """Empresa deve manter IDs, URLs, nomes e datas."""

    empresa = Empresa(
        razao_social="Tecnologia Atlas S.A.",
        nome_fantasia="Atlas",
        cnpj="12.345.678/0001-90",
        dominio="https://www.atlas.example.com/",
        site="https://atlas.example.com",
        pagina_carreiras="https://atlas.example.com/carreiras",
        nomes_alternativos=[
            "Atlas Tecnologia",
            "Grupo Atlas",
        ],
        setor="Tecnologia",
        cidade="São Paulo",
        estado="SP",
    )

    # Transformamos o modelo em um documento BSON.
    documento = modelo_para_documento(empresa)

    # O ID interno deve virar o _id principal do MongoDB.
    assert documento["_id"] == empresa.id
    assert "id" not in documento

    # URLs precisam ser armazenadas como texto.
    assert documento["site"] == "https://atlas.example.com/"
    assert documento["pagina_carreiras"] == ("https://atlas.example.com/carreiras")

    # Tuplas são armazenadas como listas BSON.
    assert documento["nomes_alternativos"] == [
        "Atlas Tecnologia",
        "Grupo Atlas",
    ]

    # Reconstruímos o modelo usando o documento.
    empresa_restaurada = documento_para_modelo(
        documento,
        Empresa,
    )

    # Todas as informações precisam continuar iguais.
    assert empresa_restaurada == empresa


def test_salario_decimal_utiliza_decimal128() -> None:
    """Salários não podem ser convertidos silenciosamente para float."""

    salario = SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        periodo=PeriodoSalario.MES,
        minimo=Decimal("5000.50"),
        maximo=Decimal("7000.75"),
        mensal_minimo=Decimal("5000.50"),
        mensal_maximo=Decimal("7000.75"),
        regra_normalizacao="valor publicado mensalmente",
    )

    vaga = VagaCanonica(
        empresa_id=uuid4(),
        titulo_normalizado="Analista de Dados",
        salario=salario,
        tecnologias=[
            "Python",
            "SQL",
        ],
    )

    documento = modelo_para_documento(vaga)

    # O salário está dentro de um documento interno.
    salario_bson = documento["salario"]

    assert isinstance(salario_bson, dict)

    # Os valores precisam utilizar o decimal nativo do BSON.
    assert isinstance(
        salario_bson["minimo"],
        Decimal128,
    )
    assert isinstance(
        salario_bson["maximo"],
        Decimal128,
    )

    # Restauramos a vaga e confirmamos que os valores
    # voltaram a ser Decimal.
    vaga_restaurada = documento_para_modelo(
        documento,
        VagaCanonica,
    )

    assert vaga_restaurada.salario is not None
    assert vaga_restaurada.salario.minimo == Decimal("5000.50")
    assert vaga_restaurada.salario.maximo == Decimal("7000.75")
    assert vaga_restaurada.tecnologias == ("Python", "SQL")


def test_data_simples_vira_datetime_utc() -> None:
    """Uma data de publicação deve ser armazenada como datetime BSON."""

    anuncio = AnuncioVaga(
        fonte=Fonte.EMPREGOS,
        id_externo="vaga-data-123",
        url="https://www.empregos.com.br/vaga/123",
        titulo_original="Analista de Dados",
        publicado_em=date(2026, 8, 20),
        expira_em=date(2026, 9, 20),
        hash_conteudo="a" * 64,
        referencia_bruta="empregos/2026/08/20/vaga-123.json",
    )

    documento = modelo_para_documento(anuncio)

    # MongoDB armazena data como meia-noite em UTC.
    assert documento["publicado_em"] == datetime(
        2026,
        8,
        20,
        tzinfo=UTC,
    )

    assert documento["expira_em"] == datetime(
        2026,
        9,
        20,
        tzinfo=UTC,
    )

    # A fonte deve ser armazenada pelo texto do enum.
    assert documento["fonte"] == "empregos"

    anuncio_restaurado = documento_para_modelo(
        documento,
        AnuncioVaga,
    )

    # Dependendo da seleção feita pelo Pydantic para a união
    # date | datetime, o valor pode voltar como um dos dois tipos.
    publicado_restaurado = anuncio_restaurado.publicado_em

    if isinstance(publicado_restaurado, datetime):
        assert publicado_restaurado.date() == date(2026, 8, 20)
    else:
        assert publicado_restaurado == date(2026, 8, 20)


def test_conversor_rejeita_datetime_sem_fuso() -> None:
    """Datas ambíguas não devem ser gravadas no banco."""

    anuncio = AnuncioVaga(
        fonte=Fonte.OUTRA,
        id_externo="vaga-data-invalida",
        url="https://example.com/vagas/1",
        titulo_original="Pessoa Desenvolvedora",
        hash_conteudo="b" * 64,
        referencia_bruta="outra/vaga-1.json",
        # campos_estruturados aceita informações adicionais.
        #
        # Colocamos propositalmente uma data sem fuso
        # para confirmar que o conversor a rejeitará.
        campos_estruturados={
            "data_invalida": datetime(2026, 8, 20, 12, 0),
        },
    )

    with pytest.raises(
        ValueError,
        match="sem fuso horário",
    ):
        modelo_para_documento(anuncio)


def test_documento_rejeita_ids_conflitantes() -> None:
    """Um documento não pode possuir id e _id diferentes."""

    documento = {
        "_id": uuid4(),
        "id": uuid4(),
        "razao_social": "Empresa com conflito",
    }

    with pytest.raises(
        ValueError,
        match="conflito entre id e _id",
    ):
        documento_para_modelo(
            documento,
            Empresa,
        )
