"""Testes do importador de URLs com padrões seguros."""

from scripts.importar_urls_fontes import (
    carregar_linhas_catalogo,
    carregar_urls_catalogos_conhecidos,
    importar_urls,
    inferir_empresa_nome,
    inferir_fonte,
    normalizar_url,
)

from observatorio_vagas.domain.enums import Fonte


def test_normaliza_url_sem_fragmento_e_porta_padrao() -> None:
    """Diferenças superficiais não devem criar duas fontes."""

    assert normalizar_url(" HTTPS://Empresa.Example:443//vagas/#topo ") == (
        "https://empresa.example/vagas/"
    )


def test_identifica_gupy_e_nome_do_tenant() -> None:
    """Uma página Gupy deve selecionar automaticamente o adaptador existente."""

    url = "https://grupo-exemplo.gupy.io/"

    assert inferir_fonte(url) is Fonte.GUPY
    assert inferir_empresa_nome(url) == "Grupo Exemplo"


def test_url_nova_entra_pendente_inativa_e_sem_republicacao() -> None:
    """A URL sozinha nunca pode ser convertida em autorização jurídica."""

    resultado = importar_urls(
        [],
        ["https://empresa-exemplo.com.br/trabalhe-conosco"],
    )

    linha = resultado.linhas[0]

    assert resultado.adicionadas == 1
    assert linha["fonte"] == "pagina_carreiras"
    assert linha["ativa"] == "false"
    assert linha["status_politica"] == "pendente"
    assert linha["republicacao_permitida"] == "false"
    assert linha["limite_paginas"] == "10000"


def test_url_repetida_nao_altera_configuracao_existente() -> None:
    """Uma segunda importação deve ser idempotente."""

    existente = {
        "alvo_id": "empresa_aprovada",
        "empresa_nome": "Empresa Aprovada",
        "fonte": "outra",
        "url_inicial": "https://dados.example/vagas",
        "ativa": "true",
        "limite_paginas": "5",
        "status_politica": "aprovada",
        "licenca_nome": "CC BY 4.0",
        "licenca_url": "https://creativecommons.org/licenses/by/4.0/",
        "atribuicao_obrigatoria": "true",
        "republicacao_permitida": "true",
    }

    resultado = importar_urls(
        [existente],
        ["https://dados.example/vagas/"],
    )

    assert resultado.adicionadas == 0
    assert resultado.repetidas == 1
    assert resultado.linhas == (existente,)


def test_url_de_outro_catalogo_nao_volta_como_pendente(tmp_path) -> None:
    """Uma URL aprovada fora do catálogo central continua sendo conhecida."""

    caminho = tmp_path / "catalogo_republicaveis.csv"
    caminho.write_text(
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica\n"
        "fonte,Empresa,outra,https://empresa.example/vagas,true,10,aprovada\n",
        encoding="utf-8",
    )

    resultado = importar_urls(
        [],
        ["https://empresa.example/vagas"],
        urls_conhecidas=carregar_urls_catalogos_conhecidos(tmp_path),
    )

    assert resultado.adicionadas == 0
    assert resultado.repetidas == 1


def test_url_invalida_e_isolada_sem_descartar_url_valida() -> None:
    """Erros em uma linha não podem interromper listas grandes."""

    resultado = importar_urls(
        [],
        ["url-invalida", "https://empresa.example/carreiras"],
        limite_paginas=20,
    )

    assert resultado.adicionadas == 1
    assert len(resultado.falhas) == 1
    assert resultado.falhas[0].numero_linha == 1
    assert resultado.linhas[0]["fonte"] == "pagina_carreiras"
    assert resultado.linhas[0]["limite_paginas"] == "20"


def test_dominio_proibido_e_documentado_como_bloqueado() -> None:
    """O importador pode registrar a URL proibida, mas nunca ativá-la."""

    resultado = importar_urls(
        [],
        ["https://br.indeed.com/jobs"],
    )

    linha = resultado.linhas[0]

    assert linha["ativa"] == "false"
    assert linha["status_politica"] == "bloqueada"
    assert linha["republicacao_permitida"] == "false"


def test_padronizacao_pode_reduzir_limite_existente(
    tmp_path,
) -> None:
    """O teto reduz excessos sem aumentar limites menores."""

    caminho = tmp_path / "catalogo.csv"
    caminho.write_text(
        (
            "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas\n"
            "um,Empresa Um,outra,https://um.example,true,100\n"
            "dois,Empresa Dois,outra,https://dois.example,true,5\n"
        ),
        encoding="utf-8",
    )

    linhas = carregar_linhas_catalogo(caminho, limite_maximo=10)

    assert [linha["limite_paginas"] for linha in linhas] == ["10", "5"]
