"""Testes do contrato de respostas brutas."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.domain.enums import Fonte


def criar_resposta_valida() -> RespostaBruta:
    """Cria uma resposta HTTP válida para os testes."""

    corpo = b"<html><body>Vaga Python</body></html>"

    return RespostaBruta(
        fonte=Fonte.PAGINA_CARREIRAS,
        url_solicitada="https://empresa.example/vagas/123",
        url_final="https://empresa.example/carreiras/vaga-123",
        status_http=200,
        corpo=corpo,
        tipo_conteudo="text/html",
        codificacao="utf-8",
        cabecalhos=(
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-cache"),
        ),
        coletado_em=datetime(
            2026,
            8,
            20,
            15,
            0,
            tzinfo=UTC,
        ),
    )


def test_resposta_calcula_metadados() -> None:
    """Tamanho, hash e sucesso devem ser calculados corretamente."""

    resposta = criar_resposta_valida()

    assert resposta.sucesso is True
    assert resposta.tamanho_bytes == len(resposta.corpo)
    assert resposta.hash_conteudo == sha256(resposta.corpo).hexdigest()


def test_cabecalho_ignora_maiusculas() -> None:
    """A busca de cabeçalho não deve depender de letras maiúsculas."""

    resposta = criar_resposta_valida()

    assert resposta.buscar_cabecalho("content-type") == "text/html; charset=utf-8"

    assert resposta.buscar_cabecalho("CONTENT-TYPE") is not None
    assert resposta.buscar_cabecalho("inexistente") is None


def test_redirecionamento_preserva_as_duas_urls() -> None:
    """A URL solicitada e a URL final podem ser diferentes."""

    resposta = criar_resposta_valida()

    assert resposta.url_solicitada == ("https://empresa.example/vagas/123")

    assert resposta.url_final == ("https://empresa.example/carreiras/vaga-123")


@pytest.mark.parametrize(
    "status_invalido",
    [
        0,
        99,
        600,
        999,
    ],
)
def test_status_http_invalido_e_rejeitado(
    status_invalido: int,
) -> None:
    """O status HTTP deve permanecer entre 100 e 599."""

    with pytest.raises(
        ValueError,
        match="entre 100 e 599",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada="https://empresa.example/vaga",
            url_final="https://empresa.example/vaga",
            status_http=status_invalido,
            corpo=b"",
        )


@pytest.mark.parametrize(
    "url_invalida",
    [
        "",
        "empresa.example/vaga",
        "ftp://empresa.example/vaga",
        "file:///vaga.html",
    ],
)
def test_url_invalida_e_rejeitada(
    url_invalida: str,
) -> None:
    """Somente URLs HTTP ou HTTPS devem ser aceitas."""

    with pytest.raises(
        ValueError,
        match="HTTP ou HTTPS",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada=url_invalida,
            url_final="https://empresa.example/vaga",
            status_http=200,
            corpo=b"",
        )


def test_corpo_precisa_ser_bytes() -> None:
    """Texto convertido prematuramente deve ser rejeitado."""

    with pytest.raises(
        TypeError,
        match="corpo deve ser armazenado como bytes",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada="https://empresa.example/vaga",
            url_final="https://empresa.example/vaga",
            status_http=200,
            corpo="conteúdo HTML",
        )


def test_data_sem_fuso_e_rejeitada() -> None:
    """A data da coleta precisa identificar seu fuso horário."""

    with pytest.raises(
        ValueError,
        match="possuir fuso horário",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada="https://empresa.example/vaga",
            url_final="https://empresa.example/vaga",
            status_http=200,
            corpo=b"",
            coletado_em=datetime(2026, 8, 20, 15, 0),
        )


@pytest.mark.parametrize(
    ("tipo_conteudo", "codificacao"),
    [
        ("", None),
        ("   ", None),
        (None, ""),
        (None, "   "),
    ],
)
def test_metadado_vazio_e_rejeitado(
    tipo_conteudo: str | None,
    codificacao: str | None,
) -> None:
    """Metadados presentes não podem conter somente espaços."""

    with pytest.raises(
        ValueError,
        match="não pode ser vaz",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada="https://empresa.example/vaga",
            url_final="https://empresa.example/vaga",
            status_http=200,
            corpo=b"",
            tipo_conteudo=tipo_conteudo,
            codificacao=codificacao,
        )
