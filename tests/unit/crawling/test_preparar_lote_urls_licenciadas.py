"""Testes da preparação de lotes grandes de URLs licenciadas."""

import json

from scripts import preparar_lote_urls_licenciadas


def test_preparar_lote_normaliza_deduplica_e_isola_dominio_bloqueado(tmp_path) -> None:
    entrada = tmp_path / "urls.txt"
    entrada.write_text(
        "\n".join(
            (
                "https://empresa.example/vagas#ignorar-fragmento",
                "https://empresa.example/vagas",
                "https://outra.example/carreiras",
                "https://br.indeed.com/jobs",
                "url-invalida",
            )
        ),
        encoding="utf-8",
    )
    saida = tmp_path / "lote"

    aceitas, rejeitadas, relatorio = preparar_lote_urls_licenciadas.preparar_lote(
        entrada=entrada,
        diretorio_saida=saida,
        substituir=False,
    )

    assert aceitas == 2
    assert rejeitadas == 3
    assert (saida / "catalogo_fontes.csv").read_text(encoding="utf-8") == (
        "url\nhttps://empresa.example/vagas\nhttps://outra.example/carreiras\n"
    )
    assert (saida / "fontes_autorizadas.csv").read_text(encoding="utf-8") == (
        "url\nhttps://empresa.example/vagas\nhttps://outra.example/carreiras\n"
    )
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    assert dados["urls_prontas_para_coleta_e_publicacao"] == 2
    assert dados["urls_rejeitadas"] == 3


def test_preparar_lote_exige_confirmacao_para_substituir_saida(tmp_path) -> None:
    entrada = tmp_path / "urls.csv"
    entrada.write_text("url\nhttps://empresa.example/vagas\n", encoding="utf-8")
    saida = tmp_path / "lote"

    preparar_lote_urls_licenciadas.preparar_lote(
        entrada=entrada,
        diretorio_saida=saida,
        substituir=False,
    )

    try:
        preparar_lote_urls_licenciadas.preparar_lote(
            entrada=entrada,
            diretorio_saida=saida,
            substituir=False,
        )
    except ValueError as erro:
        assert "--substituir" in str(erro)
    else:
        raise AssertionError("a saída existente deveria exigir confirmação")
