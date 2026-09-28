"""Testes das regras de autorização de fontes externas."""

import pytest

from observatorio_vagas.domain.enums import StatusPoliticaFonte
from observatorio_vagas.domain.politica_fonte import (
    PoliticaFonte,
    encontrar_restricao_dominio,
)

DOMINIOS_RESTRITOS = (
    ("empregos.com.br", "dominio_empregos", "Empregos"),
    ("partner.empregos.com.br", "dominio_empregos", "Empregos"),
    ("br.indeed.com", "dominio_indeed", "Indeed"),
    ("www.indeed.com.br", "dominio_indeed", "Indeed"),
    ("vagas.infojobs.com.br", "dominio_infojobs", "InfoJobs"),
    ("www.catho.com.br", "dominio_catho", "Catho"),
)


def test_fonte_nova_comeca_pendente_e_sem_permissoes() -> None:
    """Uma fonte desconhecida deve começar bloqueada por segurança."""

    # O domínio contém espaços, letras maiúsculas e ponto final
    # para também testarmos a normalização automática.
    politica = PoliticaFonte(dominio="  EMPRESA.EXAMPLE.  ")

    assert politica.dominio == "empresa.example"
    assert politica.status is StatusPoliticaFonte.PENDENTE
    assert politica.permite_coleta is False
    assert politica.permite_publicacao is False


def test_fonte_aprovada_permite_coleta_e_publicacao() -> None:
    """Aprovação com licença aberta deve liberar as duas operações."""

    politica = PoliticaFonte(
        dominio="empresa.example",
        status=StatusPoliticaFonte.APROVADA,
        licenca_nome="CC BY 4.0",
        licenca_url="https://creativecommons.org/licenses/by/4.0/",
        atribuicao_obrigatoria=True,
        republicacao_permitida=True,
    )

    assert politica.permite_coleta is True
    assert politica.permite_publicacao is True


def test_autorizacao_escrita_privada_permite_publicacao_sem_fingir_cc() -> None:
    """Uma autorização documental privada é diferente de licença aberta."""

    politica = PoliticaFonte(
        dominio="empresa.example",
        status=StatusPoliticaFonte.APROVADA,
        atribuicao_obrigatoria=True,
        republicacao_permitida=True,
        autorizacao_escrita=True,
        referencia_autorizacao="Autorização arquivada pelo titular do projeto",
    )

    assert politica.permite_publicacao is True
    assert "Autorização escrita confirmada" in politica.montar_credito_publicacao(
        url_origem="https://empresa.example/vagas/1"
    )


def test_status_aprovado_sem_licenca_nao_permite_publicacao() -> None:
    """Um rótulo no CSV não substitui a comprovação da licença."""

    politica = PoliticaFonte(
        dominio="empresa.example",
        status=StatusPoliticaFonte.APROVADA,
    )

    assert politica.permite_coleta is True
    assert politica.permite_publicacao is False


def test_republicacao_exige_licenca_completa() -> None:
    """A confirmação explícita sem nome e URL da licença deve falhar."""

    with pytest.raises(
        ValueError,
        match="licença pública completa ou autorização escrita",
    ):
        PoliticaFonte(
            dominio="empresa.example",
            status=StatusPoliticaFonte.APROVADA,
            republicacao_permitida=True,
        )


def test_republicacao_exige_status_aprovado() -> None:
    """Uma fonte apenas coletável não pode declarar republicação."""

    with pytest.raises(
        ValueError,
        match="status_politica=aprovada",
    ):
        PoliticaFonte(
            dominio="empresa.example",
            status=StatusPoliticaFonte.SOMENTE_COLETA,
            licenca_nome="CC BY 4.0",
            licenca_url="https://creativecommons.org/licenses/by/4.0/",
            republicacao_permitida=True,
        )


def test_somente_coleta_nao_permite_publicacao() -> None:
    """Coletar para análise não significa poder republicar a vaga."""

    politica = PoliticaFonte(
        dominio="empresa.example",
        status=StatusPoliticaFonte.SOMENTE_COLETA,
    )

    assert politica.permite_coleta is True
    assert politica.permite_publicacao is False


@pytest.mark.parametrize(
    "status",
    [
        StatusPoliticaFonte.PENDENTE,
        StatusPoliticaFonte.BLOQUEADA,
        StatusPoliticaFonte.DESATIVADA,
    ],
)
def test_estados_restritos_nao_permitem_operacoes(
    status: StatusPoliticaFonte,
) -> None:
    """Estados restritos devem negar coleta e publicação."""

    politica = PoliticaFonte(
        dominio="empresa.example",
        status=status,
    )

    assert politica.permite_coleta is False
    assert politica.permite_publicacao is False


@pytest.mark.parametrize(
    ("dominio", "codigo", "nome"),
    DOMINIOS_RESTRITOS,
)
@pytest.mark.parametrize(
    "status",
    [
        StatusPoliticaFonte.APROVADA,
        StatusPoliticaFonte.SOMENTE_COLETA,
    ],
)
def test_dominio_restrito_nao_pode_ser_habilitado_para_coleta(
    dominio: str,
    codigo: str,
    nome: str,
    status: StatusPoliticaFonte,
) -> None:
    """Nenhuma configuração pode liberar um domínio da lista central."""

    del codigo

    with pytest.raises(
        ValueError,
        match=nome,
    ):
        PoliticaFonte(
            dominio=dominio,
            status=status,
        )


@pytest.mark.parametrize(
    ("dominio", "codigo", "nome"),
    DOMINIOS_RESTRITOS,
)
def test_dominio_restrito_pode_ser_registrado_como_bloqueado(
    dominio: str,
    codigo: str,
    nome: str,
) -> None:
    """Um domínio proibido pode constar no catálogo para auditoria."""

    del codigo, nome

    politica = PoliticaFonte(
        dominio=dominio,
        status=StatusPoliticaFonte.BLOQUEADA,
    )

    assert politica.permite_coleta is False
    assert politica.permite_publicacao is False


@pytest.mark.parametrize(
    ("dominio", "codigo", "nome"),
    DOMINIOS_RESTRITOS,
)
def test_encontra_restricao_de_raiz_ou_subdominio(
    dominio: str,
    codigo: str,
    nome: str,
) -> None:
    """A consulta central deve retornar a regra e o motivo auditável."""

    restricao = encontrar_restricao_dominio(dominio)

    assert restricao is not None
    assert restricao.codigo == codigo
    assert restricao.nome == nome
    assert restricao.motivo
    assert restricao.referencia.startswith("https://")


def test_nao_confunde_sufixo_malicioso_com_dominio_restrito() -> None:
    """Um nome apenas parecido não deve produzir falso positivo."""

    assert encontrar_restricao_dominio("indeed.com.exemplo-seguro.test") is None


@pytest.mark.parametrize(
    "dominio_invalido",
    [
        "",
        "empresa",
        "https://empresa.example",
        "empresa.example/carreiras",
        "empresa.example:443",
    ],
)
def test_rejeita_dominio_invalido(
    dominio_invalido: str,
) -> None:
    """A política deve receber somente um nome de domínio completo."""

    with pytest.raises(ValueError):
        PoliticaFonte(dominio=dominio_invalido)


def test_rejeita_status_em_texto_comum() -> None:
    """O status deve usar o enum para impedir erros de digitação."""

    with pytest.raises(
        TypeError,
        match="StatusPoliticaFonte",
    ):
        PoliticaFonte(
            dominio="empresa.example",
            status="aprovada",  # type: ignore[arg-type]
        )
