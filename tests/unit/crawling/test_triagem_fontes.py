from pathlib import Path

import pytest
from scripts import preparar_lote_urls_licenciadas, triar_catalogo

from observatorio_vagas.crawling.triagem_fontes import classificar, motivo_de_exclusao


@pytest.mark.parametrize(
    "url",
    [
        "https://empresa.example/trabalhe-conosco",
        "https://empresa.example/vagas/analista-de-dados",
        "https://empresa.example/",
        "https://www.vagasbsb.com.br/2026/09/cacau-show-contrata-operadora-de-loja.html",
        "https://otrainee.com.br/2026/09/23/trainee-hypera-pharma-2027/",
        "https://www.vagaspoa.com.br/category/vagas-por-area-2/promotor",
        "https://agrobase.com.br/oportunidades/2026/05/25-vagas-bolsistas-rio-de-janeiro/",
        "https://www.jobijoba.com.br/detail/97/9066a2f55eb15ae157077b6c53d64e55",
        # Curso com palavra de vaga pode ser programa de aprendiz/estágio.
        "https://empresa.example/cursos/jovem-aprendiz-vagas",
    ],
)
def test_nao_exclui_o_que_pode_ser_vaga(url: str) -> None:
    assert motivo_de_exclusao(url) is None


@pytest.mark.parametrize(
    ("url", "trecho"),
    [
        ("https://agrobase.com.br/concursos/edital-348-2026.pdf", "arquivo"),
        ("https://empresa.example/imagens/banner.JPG", "arquivo"),
        ("https://blog.example/category/dicas/", "categoria"),
        ("https://blog.example/tag/marketing", "categoria"),
        ("https://ibsec.com.br/10-carreiras-que-voce-pode-seguir-em-ciberseguranca/", "lista"),
        ("https://www.uol.com.br/esporte/2025/04/25/boxeador-olimpico-vence-luta/", "datado"),
        ("https://www.dismatal.com.br/produto/abracadeira-tipo-borboleta/30748", "produto"),
        ("https://www.profec.com.br/curso/curso-gratuito-de-operador-de-colheitadeira", "curso"),
        ("https://confiseg.com.br/seguranca/?product_cat=haste-estrela", "produto"),
        ("https://cargos.com.br/cargo/taqueiro/", "descrição de cargo"),
    ],
)
def test_exclui_o_que_nunca_e_pagina_de_vagas(url: str, trecho: str) -> None:
    assert trecho in (motivo_de_exclusao(url) or "")


def test_classifica_a_url_para_o_relatorio() -> None:
    assert classificar("https://empresa.example/trabalhe-conosco") == "carreira"
    assert classificar("https://empresa.example/blog/novidades") == "editorial_ou_produto"
    assert classificar("https://empresa.example/") == "home"
    assert classificar("https://empresa.example/quem-somos") == "outra"


def test_preparar_lote_rejeita_na_triagem_e_permite_desligar(tmp_path: Path) -> None:
    entrada = tmp_path / "urls.txt"
    entrada.write_text(
        "https://empresa.example/trabalhe-conosco\nhttps://empresa.example/edital.pdf\n",
        encoding="utf-8",
    )

    aceitas, rejeitadas, relatorio = preparar_lote_urls_licenciadas.preparar_lote(
        entrada=entrada, diretorio_saida=tmp_path / "com", substituir=False
    )
    aceitas_sem, _, _ = preparar_lote_urls_licenciadas.preparar_lote(
        entrada=entrada, diretorio_saida=tmp_path / "sem", substituir=False, triar=False
    )

    assert (aceitas, rejeitadas) == (1, 1)
    assert "triagem" in relatorio.read_text(encoding="utf-8")
    assert aceitas_sem == 2


def test_triar_catalogo_separa_excluidas_sem_tocar_no_original(tmp_path: Path) -> None:
    catalogo = tmp_path / "origem" / "catalogo_fontes.csv"
    catalogo.parent.mkdir()
    catalogo.write_text(
        "url\nhttps://empresa.example/trabalhe-conosco\nhttps://empresa.example/edital.pdf\n",
        encoding="utf-8",
    )
    original = catalogo.read_text(encoding="utf-8")

    resumo = triar_catalogo.triar(catalogo, tmp_path / "saida", com_mongo=False)

    assert (resumo["fontes_mantidas"], resumo["fontes_excluidas"]) == (1, 1)
    assert catalogo.read_text(encoding="utf-8") == original
    excluidas = (tmp_path / "saida" / "excluidas.csv").read_text(encoding="utf-8")
    assert "edital.pdf" in excluidas and "arquivo" in excluidas
    assert "edital.pdf" not in (tmp_path / "saida" / "catalogo_fontes.csv").read_text(
        encoding="utf-8"
    )


def test_com_mongo_tira_noticia_que_nunca_rendeu_vaga(tmp_path: Path, monkeypatch) -> None:
    catalogo = tmp_path / "origem" / "catalogo_fontes.csv"
    catalogo.parent.mkdir()
    catalogo.write_text(
        "url\nhttps://empresa.example/trabalhe-conosco\n"
        "https://jornal.example/noticias/feira-do-livro\n"
        "https://outro.example/noticias/empresa-abre-vagas-de-motorista\n"
        "https://tnh1.example/noticia/senac-abre-processo-seletivo-para-cargos\n",
        encoding="utf-8",
    )
    alvos = triar_catalogo.carregar_alvos_csv_tolerante(catalogo).alvos
    com_anuncio = {a.alvo_id for a in alvos if "outro.example" in a.url_inicial}
    monkeypatch.setattr(triar_catalogo, "_alvos_com_anuncio", lambda: com_anuncio)

    resumo = triar_catalogo.triar(catalogo, tmp_path / "saida", com_mongo=True)

    excluidas = (tmp_path / "saida" / "excluidas.csv").read_text(encoding="utf-8")
    assert "feira-do-livro" in excluidas and "nunca rendeu vaga" in excluidas
    # Carreira sem anúncio, notícia que já rendeu vaga e notícia que fala de seleção ficam.
    assert resumo["fontes_mantidas"] == 3
