"""Testes da integração entre catálogo e política de fontes."""

from pathlib import Path

import pytest

from observatorio_vagas.crawling.catalog import (
    ErroCatalogoFontes,
    carregar_alvos_csv,
)
from observatorio_vagas.domain.enums import StatusPoliticaFonte

CABECALHO_ANTIGO = "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas"

CABECALHO_COM_POLITICA = f"{CABECALHO_ANTIGO},status_politica"

CABECALHO_COM_LICENCA = (
    f"{CABECALHO_COM_POLITICA},licenca_nome,licenca_url,"
    "atribuicao_obrigatoria,republicacao_permitida"
)


def escrever_catalogo(
    tmp_path: Path,
    cabecalho: str,
    linha: str,
) -> Path:
    """Cria um catálogo temporário com uma única fonte."""

    caminho = tmp_path / "fontes_politica.csv"

    caminho.write_text(
        f"{cabecalho}\n{linha}\n",
        encoding="utf-8",
    )

    return caminho


def test_catalogo_antigo_sem_politica_fica_pendente(
    tmp_path: Path,
) -> None:
    """A ausência da coluna nunca deve aprovar a fonte automaticamente."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_ANTIGO,
        "teste,Empresa,outra,https://empresa.example,true,1",
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.politica.status is StatusPoliticaFonte.PENDENTE
    assert alvo.habilitado_para_coleta is False
    assert alvo.habilitado_para_publicacao is False


def test_fonte_aprovada_sem_licenca_nao_publica(
    tmp_path: Path,
) -> None:
    """Catálogo antigo aprovado continua coletável, mas não republica."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_POLITICA,
        "teste,Empresa,outra,https://empresa.example,true,1,aprovada",
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.politica.status is StatusPoliticaFonte.APROVADA
    assert alvo.habilitado_para_coleta is True
    assert alvo.habilitado_para_publicacao is False


def test_fonte_aprovada_com_licenca_fica_habilitada(
    tmp_path: Path,
) -> None:
    """Licença comprovada e permissão explícita liberam publicação."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_LICENCA,
        (
            "teste,Empresa,outra,https://empresa.example,true,1,aprovada,"
            "CC BY 4.0,https://creativecommons.org/licenses/by/4.0/,true,true"
        ),
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.politica.licenca_nome == "CC BY 4.0"
    assert alvo.politica.atribuicao_obrigatoria is True
    assert alvo.habilitado_para_publicacao is True


def test_fonte_aprovada_com_autorizacao_escrita_fica_habilitada(
    tmp_path: Path,
) -> None:
    """O catálogo registra autorização privada sem exigir URL pública falsa."""

    caminho = escrever_catalogo(
        tmp_path,
        f"{CABECALHO_COM_LICENCA},autorizacao_escrita,referencia_autorizacao",
        (
            "teste,Empresa,outra,https://empresa.example,true,1,aprovada,,,true,true,"
            "true,Autorização arquivada pelo titular"
        ),
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.politica.autorizacao_escrita is True
    assert alvo.habilitado_para_publicacao is True


def test_republicacao_explicita_sem_licenca_rejeita_linha(
    tmp_path: Path,
) -> None:
    """Não é possível liberar uma fonte sem a prova verificável."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_LICENCA,
        "teste,Empresa,outra,https://empresa.example,true,1,aprovada,,,false,true",
    )

    with pytest.raises(
        ErroCatalogoFontes,
        match="licença pública completa ou autorização escrita",
    ):
        carregar_alvos_csv(caminho)


def test_somente_coleta_nao_habilita_publicacao(
    tmp_path: Path,
) -> None:
    """A autorização interna não permite republicar a vaga."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_POLITICA,
        ("teste,Empresa,outra,https://empresa.example,true,1,somente_coleta"),
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.habilitado_para_coleta is True
    assert alvo.habilitado_para_publicacao is False


@pytest.mark.parametrize(
    "status",
    [
        "pendente",
        "bloqueada",
        "desativada",
    ],
)
def test_status_restrito_nao_habilita_coleta(
    tmp_path: Path,
    status: str,
) -> None:
    """Uma fonte ativa também precisa possuir autorização de coleta."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_POLITICA,
        (f"teste,Empresa,outra,https://empresa.example,true,1,{status}"),
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.habilitado_para_coleta is False
    assert alvo.habilitado_para_publicacao is False


def test_status_desconhecido_produz_erro_claro(
    tmp_path: Path,
) -> None:
    """Erros de digitação no CSV devem impedir o início da coleta."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_POLITICA,
        "teste,Empresa,outra,https://empresa.example,true,1,liberada",
    )

    with pytest.raises(
        ErroCatalogoFontes,
        match="status_politica inválido",
    ):
        carregar_alvos_csv(caminho)


@pytest.mark.parametrize(
    ("linha", "nome"),
    [
        (
            "empregos,Empregos,empregos,https://www.empregos.com.br,true,1,aprovada",
            "Empregos",
        ),
        (
            "indeed,Indeed,outra,https://br.indeed.com/jobs,true,1,somente_coleta",
            "Indeed",
        ),
        (
            "infojobs,InfoJobs,outra,https://www.infojobs.com.br,true,1,aprovada",
            "InfoJobs",
        ),
        (
            "catho,Catho,outra,https://www.catho.com.br,true,1,somente_coleta",
            "Catho",
        ),
    ],
)
def test_dominio_restrito_nao_pode_ser_habilitado_no_catalogo(
    tmp_path: Path,
    linha: str,
    nome: str,
) -> None:
    """O catálogo não pode contornar a lista central de proibições."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_POLITICA,
        linha,
    )

    with pytest.raises(
        ErroCatalogoFontes,
        match=nome,
    ):
        carregar_alvos_csv(caminho)


@pytest.mark.parametrize(
    "linha",
    [
        "empregos,Empregos,empregos,https://www.empregos.com.br,true,1,bloqueada",
        "indeed,Indeed,outra,https://br.indeed.com/jobs,true,1,bloqueada",
        "infojobs,InfoJobs,outra,https://www.infojobs.com.br,true,1,bloqueada",
        "catho,Catho,outra,https://www.catho.com.br,true,1,bloqueada",
    ],
)
def test_dominio_restrito_pode_constar_como_bloqueado(
    tmp_path: Path,
    linha: str,
) -> None:
    """O registro bloqueado documenta explicitamente a proibição."""

    caminho = escrever_catalogo(
        tmp_path,
        CABECALHO_COM_POLITICA,
        linha,
    )

    alvo = carregar_alvos_csv(caminho)[0]

    assert alvo.habilitado_para_coleta is False
    assert alvo.habilitado_para_publicacao is False
