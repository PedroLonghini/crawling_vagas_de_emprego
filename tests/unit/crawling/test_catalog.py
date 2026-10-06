"""Testes do catálogo de fontes do crawler."""

from pathlib import Path

import pytest

from observatorio_vagas.crawling.catalog import (
    ErroCatalogoFontes,
    carregar_alvos_csv,
    carregar_alvos_csv_tolerante,
)
from observatorio_vagas.domain.enums import Fonte

# Cabeçalho completo exigido pelo leitor do catálogo.
CABECALHO = "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas"


def criar_catalogo(
    tmp_path: Path,
    *linhas: str,
) -> Path:
    """Cria um CSV temporário utilizado por um teste."""

    caminho = tmp_path / "fontes.csv"

    # Montamos um arquivo contendo:
    # - o cabeçalho obrigatório;
    # - as linhas recebidas pelo teste;
    # - uma quebra de linha final.
    conteudo = "\n".join(
        [
            CABECALHO,
            *linhas,
            "",
        ]
    )

    caminho.write_text(
        conteudo,
        encoding="utf-8",
    )

    return caminho


def test_carrega_multiplos_alvos(
    tmp_path: Path,
) -> None:
    """O catálogo deve preservar a ordem e os dados das fontes."""

    caminho = criar_catalogo(
        tmp_path,
        ("empresa_ativa,Empresa Ativa,pagina_carreiras,https://ativa.example/vagas,true,10"),
        (
            "empresa_carreiras,Empresa Carreiras,pagina_carreiras,"
            "https://empresa.example/carreiras,false,5"
        ),
    )

    alvos = carregar_alvos_csv(caminho)

    assert len(alvos) == 2

    # Primeiro alvo: fonte ativa (a Gupy foi bloqueada em 06/10/2026).
    assert alvos[0].alvo_id == "empresa_ativa"
    assert alvos[0].fonte is Fonte.PAGINA_CARREIRAS
    assert alvos[0].ativa is True
    assert alvos[0].limite_paginas == 10
    assert alvos[0].dominio == "ativa.example"

    # Segundo alvo: página de carreiras desativada.
    assert alvos[1].alvo_id == "empresa_carreiras"
    assert alvos[1].fonte is Fonte.PAGINA_CARREIRAS
    assert alvos[1].ativa is False


def test_ignora_linhas_vazias(
    tmp_path: Path,
) -> None:
    """Linhas vazias não devem produzir alvos falsos."""

    caminho = criar_catalogo(
        tmp_path,
        "",
        ("empresa_teste,Empresa Teste,outra,https://empresa.example,true,1"),
        "",
    )

    alvos = carregar_alvos_csv(caminho)

    assert len(alvos) == 1
    assert alvos[0].alvo_id == "empresa_teste"


def test_rejeita_coluna_obrigatoria_ausente(
    tmp_path: Path,
) -> None:
    """Um CSV incompleto deve falhar antes de iniciar o crawler."""

    caminho = tmp_path / "incompleto.csv"

    # Este CSV não possui a coluna limite_paginas.
    caminho.write_text(
        (
            "alvo_id,empresa_nome,fonte,url_inicial,ativa\n"
            "teste,Empresa,outra,https://empresa.example,true\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ErroCatalogoFontes,
        match="limite_paginas",
    ):
        carregar_alvos_csv(caminho)


@pytest.mark.parametrize(
    "linha_invalida",
    [
        # URL sem protocolo.
        ("teste,Empresa,outra,endereco-sem-protocolo,true,1"),
        # Valor booleano inválido.
        ("teste,Empresa,outra,https://empresa.example,talvez,1"),
        # Limite menor que 1.
        ("teste,Empresa,outra,https://empresa.example,true,0"),
        # Fonte que não existe no enum.
        ("teste,Empresa,fonte_desconhecida,https://empresa.example,true,1"),
    ],
)
def test_rejeita_linha_invalida_e_informa_numero(
    tmp_path: Path,
    linha_invalida: str,
) -> None:
    """Um valor inválido deve indicar a linha que precisa ser corrigida."""

    caminho = criar_catalogo(
        tmp_path,
        linha_invalida,
    )

    with pytest.raises(
        ErroCatalogoFontes,
        match="linha 2",
    ):
        carregar_alvos_csv(caminho)


def test_rejeita_alvo_id_duplicado(
    tmp_path: Path,
) -> None:
    """O mesmo identificador não pode representar duas configurações."""

    caminho = criar_catalogo(
        tmp_path,
        ("empresa_1,Empresa Um,outra,https://um.example,true,1"),
        ("EMPRESA_1,Empresa Dois,outra,https://dois.example,true,1"),
    )

    # A comparação ignora maiúsculas e minúsculas.
    with pytest.raises(
        ErroCatalogoFontes,
        match="alvo_id duplicado",
    ):
        carregar_alvos_csv(caminho)


def test_arquivo_inexistente_produz_erro_claro(
    tmp_path: Path,
) -> None:
    """O usuário deve receber uma mensagem compreensível."""

    caminho_inexistente = tmp_path / "nao_existe.csv"

    with pytest.raises(
        ErroCatalogoFontes,
        match="não foi possível ler o catálogo",
    ):
        carregar_alvos_csv(caminho_inexistente)


def test_modo_tolerante_preserva_linhas_validas(
    tmp_path: Path,
) -> None:
    """Uma linha inválida não deve impedir o carregamento das demais."""

    caminho = criar_catalogo(
        tmp_path,
        "invalida,Empresa,outra,url-sem-protocolo,true,10",
        "valida,Empresa Válida,pagina_carreiras,https://valida.example/vagas,true,20",
        "outra,Outra Empresa,outra,https://outra.example,true,5",
    )

    resultado = carregar_alvos_csv_tolerante(caminho)

    assert [alvo.alvo_id for alvo in resultado.alvos] == [
        "valida",
        "outra",
    ]
    assert len(resultado.falhas) == 1
    assert resultado.falhas[0].numero_linha == 2
    assert resultado.falhas[0].alvo_id == "invalida"
    assert "URL HTTP" in resultado.falhas[0].mensagem


def test_modo_tolerante_isola_id_duplicado(
    tmp_path: Path,
) -> None:
    """O primeiro ID válido permanece e a repetição é relatada."""

    caminho = criar_catalogo(
        tmp_path,
        "empresa,Empresa Um,outra,https://um.example,true,5",
        "EMPRESA,Empresa Dois,outra,https://dois.example,true,5",
        "terceira,Empresa Três,outra,https://tres.example,true,5",
    )

    resultado = carregar_alvos_csv_tolerante(caminho)

    assert [alvo.alvo_id for alvo in resultado.alvos] == [
        "empresa",
        "terceira",
    ]
    assert len(resultado.falhas) == 1
    assert "duplicado" in resultado.falhas[0].mensagem
