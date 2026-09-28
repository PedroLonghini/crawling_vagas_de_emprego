"""Testes das empresas canônicas e identidades por fonte."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from observatorio_vagas.domain.empresa import (
    Empresa,
    EmpresaFonte,
    EvidenciaCadastralEmpresa,
)
from observatorio_vagas.domain.enums import Fonte


def test_empresa_normaliza_cnpj_dominio_e_nomes() -> None:
    empresa = Empresa(
        razao_social="Empresa Exemplo S.A.",
        nome_fantasia="Exemplo",
        cnpj="12.345.678/0001-90",
        dominio="https://www.exemplo.com.br/",
        nomes_alternativos=["Exemplo SA", "exemplo sa", "  Grupo Exemplo  "],
    )

    assert empresa.cnpj == "12345678000190"
    assert empresa.dominio == "exemplo.com.br"
    assert empresa.nomes_alternativos == ("Exemplo SA", "Grupo Exemplo")
    assert empresa.nome_exibicao == "Exemplo"


def test_empresa_aceita_cnpj_alfanumerico() -> None:
    empresa = Empresa(razao_social="Empresa Nova", cnpj="00.000.000/E08G-12")

    assert empresa.cnpj == "00000000E08G12"


def test_empresa_rejeita_cnpj_invalido() -> None:
    with pytest.raises(ValidationError, match="CNPJ deve possuir 14 posições"):
        Empresa(razao_social="Empresa Inválida", cnpj="123")


def test_empresa_rejeita_dominio_com_caminho() -> None:
    with pytest.raises(ValidationError, match="não deve conter caminho"):
        Empresa(razao_social="Empresa Inválida", dominio="exemplo.com/vagas")


def test_empresa_fonte_exige_identificador_ou_url() -> None:
    with pytest.raises(ValidationError, match="id_externo ou url_na_fonte"):
        EmpresaFonte(empresa_id=uuid4(), fonte=Fonte.EMPREGOS)


def test_empresa_fonte_guarda_identidade_externa() -> None:
    identidade = EmpresaFonte(
        empresa_id=uuid4(),
        fonte=Fonte.EMPREGOS,
        id_externo="empresa-123",
        nome_na_fonte="Empresa Exemplo",
    )

    assert identidade.id_externo == "empresa-123"
    assert identidade.ativa


def test_empresa_guarda_informacoes_institucionais() -> None:
    """Verifica os campos institucionais usados na futura publicação."""

    empresa = Empresa(
        # Razão social continua sendo o único nome obrigatório
        # para criar uma empresa no domínio.
        razao_social="Tecnologia Atlas S.A.",
        # Este é o endereço principal da empresa.
        site="https://tecnologia-atlas.example.com",
        # Este endereço aponta especificamente para oportunidades.
        pagina_carreiras=("https://tecnologia-atlas.example.com/carreiras"),
        # Este endereço aponta para uma imagem pública.
        logo_url=("https://tecnologia-atlas.example.com/logo.png"),
        # Esta descrição poderá alimentar futuramente
        # company.description no payload do Empregos.
        descricao=("Empresa fictícia especializada em tecnologia e desenvolvimento de sistemas."),
        # O setor poderá ser usado em company.industries.
        setor="Tecnologia",
    )

    # HttpUrl é um tipo especial do Pydantic.
    #
    # Por isso usamos str() para comparar o endereço como texto.
    assert str(empresa.site) == ("https://tecnologia-atlas.example.com/")

    assert str(empresa.pagina_carreiras) == ("https://tecnologia-atlas.example.com/carreiras")

    assert str(empresa.logo_url) == ("https://tecnologia-atlas.example.com/logo.png")

    assert empresa.descricao == (
        "Empresa fictícia especializada em tecnologia e desenvolvimento de sistemas."
    )

    assert empresa.setor == "Tecnologia"


def test_empresa_guarda_evidencia_cadastral() -> None:
    """A empresa deve preservar a origem pública do CNPJ."""

    evidencia = EvidenciaCadastralEmpresa(
        campo="cnpj",
        valor_extraido="17160849000125",
        url_fonte=("https://publico.aurum.com.br/contratos/politica-de-privacidade.pdf"),
        referencia_bruta=(
            "corpos/cb/cb0d8e5de1850c48621d0330bb93316ae447573b5b02df5462cccdedccff5984.bin"
        ),
        hash_conteudo=("cb0d8e5de1850c48621d0330bb93316ae447573b5b02df5462cccdedccff5984"),
        pagina=1,
        trecho_evidencia=("AURUM SOFTMATIC, inscrita no CNPJ nº 17.160.849/0001-25"),
        coletado_em=datetime(
            2026,
            8,
            27,
            tzinfo=UTC,
        ),
    )

    empresa = Empresa(
        razao_social="Aurum",
        cnpj="17.160.849/0001-25",
        evidencias_cadastrais=(evidencia,),
    )

    assert empresa.cnpj == "17160849000125"
    assert len(empresa.evidencias_cadastrais) == 1
    assert empresa.evidencias_cadastrais[0].campo == "cnpj"
    assert empresa.evidencias_cadastrais[0].pagina == 1
