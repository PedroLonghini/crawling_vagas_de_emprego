"""Testes da extração de CNPJ em textos e documentos."""

import pytest

from observatorio_vagas.extraction.cnpj_documento import (
    ErroExtracaoCnpjDocumento,
    extrair_cnpjs_pdf,
    extrair_cnpjs_texto,
    formatar_cnpj,
    validar_cnpj,
)


def test_valida_cnpj_oficial_da_aurum() -> None:
    """O CNPJ publicado pela Aurum deve ser válido."""

    assert validar_cnpj("17.160.849/0001-25") is True
    assert validar_cnpj("17160849000125") is True


def test_rejeita_cnpj_com_digito_incorreto() -> None:
    """Um número estruturalmente parecido não deve ser aceito."""

    assert validar_cnpj("17.160.849/0001-26") is False
    assert validar_cnpj("11.111.111/1111-11") is False


def test_extrai_cnpj_com_evidencia() -> None:
    """O resultado deve preservar página e trecho da origem."""

    texto = (
        "A empresa AURUM SOFTMATIC LTDA está inscrita "
        "no CNPJ nº 17.160.849/0001-25 e possui sede "
        "na cidade de Florianópolis."
    )

    resultados = extrair_cnpjs_texto(
        texto,
        pagina=2,
    )

    assert len(resultados) == 1

    resultado = resultados[0]

    assert resultado.cnpj == "17160849000125"
    assert resultado.formatado == "17.160.849/0001-25"
    assert resultado.pagina == 2
    assert "AURUM SOFTMATIC" in resultado.trecho_evidencia


def test_remove_cnpj_repetido_na_mesma_pagina() -> None:
    """Repetições não devem produzir várias evidências iguais."""

    texto = "CNPJ 17.160.849/0001-25. Cadastro: 17.160.849/0001-25."

    resultados = extrair_cnpjs_texto(texto)

    assert len(resultados) == 1


def test_ignora_numero_com_checksum_invalido() -> None:
    """Um candidato inválido deve ser descartado."""

    resultados = extrair_cnpjs_texto("CNPJ informado: 17.160.849/0001-26.")

    assert resultados == ()


def test_formata_cnpj_normalizado() -> None:
    """O formato armazenado pode ser apresentado com pontuação."""

    assert formatar_cnpj("17160849000125") == ("17.160.849/0001-25")


def test_rejeita_conteudo_que_nao_e_pdf() -> None:
    """Bytes arbitrários não podem ser tratados como PDF."""

    with pytest.raises(
        ErroExtracaoCnpjDocumento,
        match="documento PDF",
    ):
        extrair_cnpjs_pdf(b"conteudo que nao representa um arquivo PDF")
