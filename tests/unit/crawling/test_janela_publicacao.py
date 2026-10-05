from datetime import UTC, datetime

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request

from observatorio_vagas.crawling.janela_publicacao import (
    DataPublicacao,
    EncerramentoPorIdadeDownloaderMiddleware,
    JanelaPublicacao,
    esta_velha,
    extrair_data_publicacao,
    interpretar_data,
)

AGORA = datetime(2026, 10, 2, 13, 0, tzinfo=UTC)  # 10:00 em Brasília


def _resposta(html: str) -> HtmlResponse:
    return HtmlResponse(url="https://exemplo.com/vaga", body=html.encode(), encoding="utf-8")


def test_le_data_do_json_ld_inclusive_aninhada() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"JobPosting","datePosted":"2026-10-02T08:30:00-03:00"}]}'
        "</script>"
    )

    data = extrair_data_publicacao(_resposta(html))

    assert data is not None and data.com_hora
    assert data.momento == datetime(2026, 10, 2, 11, 30, tzinfo=UTC)


def test_le_data_das_metatags() -> None:
    html = '<meta property="article:published_time" content="2026-10-01">'

    data = extrair_data_publicacao(_resposta(html))

    assert data is not None and not data.com_hora


def test_sem_data_devolve_none() -> None:
    assert extrair_data_publicacao(_resposta("<html></html>")) is None


@pytest.mark.parametrize(
    ("texto", "com_hora"),
    [("2026-10-01", False), ("2026-10-01T10:00:00Z", True), ("01/10/2026", False)],
)
def test_interpreta_formatos(texto: str, com_hora: bool) -> None:
    data = interpretar_data(texto)

    assert data is not None
    assert data.com_hora is com_hora


def test_lixo_nao_vira_data() -> None:
    assert interpretar_data("ontem") is None
    assert interpretar_data("31/02/2026") is None


def test_data_com_hora_usa_24_horas_exatas() -> None:
    recente = interpretar_data("2026-10-01T11:00:00-03:00")  # 23 h antes
    antiga = interpretar_data("2026-10-01T09:00:00-03:00")  # 25 h antes

    assert not esta_velha(recente, horas=24, agora=AGORA)
    assert esta_velha(antiga, horas=24, agora=AGORA)


def test_data_sem_hora_so_e_velha_por_dia_inteiro() -> None:
    ontem = interpretar_data("2026-10-01")
    anteontem = interpretar_data("2026-09-30")

    assert not esta_velha(ontem, horas=24, agora=AGORA)
    assert esta_velha(anteontem, horas=24, agora=AGORA)


VELHA = DataPublicacao(datetime(2026, 9, 1, tzinfo=UTC), True)


def _nova() -> DataPublicacao:
    return DataPublicacao(datetime.now(UTC), True)


def test_listagem_esgota_com_tres_velhas_seguidas_na_ordem_da_lista() -> None:
    janela = JanelaPublicacao(horas=24)

    for posicao in range(5):
        assert janela.registrar("a", VELHA, lista="L", posicao=posicao)
    assert not janela.lista_esgotada("L")
    assert janela.registrar("a", VELHA, lista="L", posicao=5)

    assert janela.limite_da_lista("L") == 6
    assert not janela.encerrada("a")  # a fonte continua: outras listagens seguem


def test_ordem_de_chegada_nao_esgota_a_listagem() -> None:
    """Respostas fora de ordem (0, 5, 9) não são 'seguidas' na listagem."""

    janela = JanelaPublicacao(horas=24)
    for posicao in (0, 5, 9):
        janela.registrar("a", VELHA, lista="L", posicao=posicao)

    assert not janela.lista_esgotada("L")


def test_vaga_nova_depois_das_velhas_impede_esgotar() -> None:
    """Lista que não está em ordem de data: uma nova mais abaixo cancela o corte."""

    janela = JanelaPublicacao(horas=24)
    assert not janela.registrar("a", _nova(), lista="L", posicao=6)
    for posicao in (0, 1, 2):
        janela.registrar("a", VELHA, lista="L", posicao=posicao)

    assert not janela.lista_esgotada("L")


def test_listagens_sao_independentes() -> None:
    janela = JanelaPublicacao(horas=24)
    for posicao in range(6):
        janela.registrar("a", VELHA, lista="antiga", posicao=posicao)

    assert janela.lista_esgotada("antiga")
    assert not janela.lista_esgotada("outubro")


def test_vaga_sem_lista_nao_esgota_nada() -> None:
    janela = JanelaPublicacao(horas=24)
    for _ in range(5):
        assert janela.registrar("a", VELHA)

    assert not janela.encerrada("a")


def test_pagina_sem_data_nunca_encerra() -> None:
    janela = JanelaPublicacao(horas=24)

    for _ in range(10):
        assert janela.registrar("a", None) is False

    assert not janela.encerrada("a")


def _middleware(janela: JanelaPublicacao):
    class Estatisticas:
        def inc_value(self, *_: object) -> None: ...

    class Crawler:
        stats = Estatisticas()
        spider = None

    class Spider:
        janela_publicacao = janela

    return EncerramentoPorIdadeDownloaderMiddleware(Crawler()), Spider()  # type: ignore[arg-type]


def test_middleware_descarta_o_resto_da_lista_esgotada_e_a_pagina_seguinte() -> None:
    janela = JanelaPublicacao(horas=24)
    for posicao in range(6):
        janela.registrar("a", VELHA, lista="L", posicao=posicao)
    middleware, spider = _middleware(janela)

    def pedido(**meta: object) -> Request:
        return Request("https://exemplo.com/x", meta={"observatorio_alvo_id": "a", **meta})

    with pytest.raises(IgnoreRequest):
        middleware.process_request(pedido(observatorio_lista="L", observatorio_posicao=7), spider)
    with pytest.raises(IgnoreRequest):
        middleware.process_request(pedido(observatorio_lista_origem="L"), spider)
    # Outras listagens e posições anteriores continuam.
    middleware.process_request(pedido(observatorio_lista="M", observatorio_posicao=7), spider)
    middleware.process_request(pedido(observatorio_lista="L", observatorio_posicao=1), spider)


def test_middleware_descarta_fonte_encerrada_sem_vagas() -> None:
    janela = JanelaPublicacao()
    janela.encerrar("a")
    middleware, spider = _middleware(janela)

    with pytest.raises(IgnoreRequest):
        middleware.process_request(
            Request("https://exemplo.com/1", meta={"observatorio_alvo_id": "a"}), spider
        )
    middleware.process_request(
        Request("https://exemplo.com/2", meta={"observatorio_alvo_id": "b"}), spider
    )


def test_vaga_ja_gravada_e_descartada_sem_encerrar_a_fonte() -> None:
    janela = JanelaPublicacao(horas=24)

    assert janela.registrar("a", None, conhecida=True) is True
    assert janela.registrar("a", _nova(), conhecida=True) is True
    assert not janela.registrar("a", _nova(), conhecida=False)
    assert not janela.encerrada("a")


def test_fonte_sem_data_so_e_classificada_apos_tres_leituras() -> None:
    janela = JanelaPublicacao()

    janela.registrar("a", None)
    janela.registrar("a", None)
    assert not janela.sem_data("a")
    janela.registrar("a", None)
    assert janela.sem_data("a")


def test_fonte_que_mostrou_data_nunca_e_sem_data() -> None:
    janela = JanelaPublicacao()
    janela.registrar("a", DataPublicacao(datetime.now(UTC), True))
    for _ in range(5):
        janela.registrar("a", None)

    assert not janela.sem_data("a")


def _middleware_com(janela: JanelaPublicacao):
    class Estatisticas:
        def inc_value(self, *_: object) -> None: ...

    class Crawler:
        stats = Estatisticas()
        spider = None

    class Spider:
        janela_publicacao = janela

    return EncerramentoPorIdadeDownloaderMiddleware(Crawler()), Spider()  # type: ignore[arg-type]


def test_fonte_sem_data_so_le_as_tres_primeiras_listagens() -> None:
    janela = JanelaPublicacao()
    for _ in range(3):
        janela.registrar("a", None)
    middleware, spider = _middleware_com(janela)

    def listagem(numero: int, alvo: str = "a") -> Request:
        return Request(
            f"https://exemplo.com/?p={numero}",
            meta={
                "observatorio_alvo_id": alvo,
                "observatorio_tipo_pagina": "inicial",
                "observatorio_numero_pagina": numero,
            },
        )

    middleware.process_request(listagem(3), spider)  # type: ignore[arg-type]
    with pytest.raises(IgnoreRequest):
        middleware.process_request(listagem(4), spider)  # type: ignore[arg-type]
    middleware.process_request(listagem(4, alvo="b"), spider)  # type: ignore[arg-type]


def test_carrega_urls_conhecidas(tmp_path) -> None:
    from observatorio_vagas.crawling.janela_publicacao import carregar_urls_conhecidas

    arquivo = tmp_path / "urls.txt"
    arquivo.write_text("https://a.com/1\n\nhttps://a.com/2\n", encoding="utf-8")

    assert carregar_urls_conhecidas(arquivo) == {"https://a.com/1", "https://a.com/2"}
    assert carregar_urls_conhecidas(None) == frozenset()
    assert carregar_urls_conhecidas(tmp_path / "nao_existe.txt") == frozenset()


def test_destaques_velhos_no_topo_nao_esgotam_a_listagem() -> None:
    """As 3 primeiras posições podem ser vagas fixas antigas; só elas não bastam."""

    janela = JanelaPublicacao(horas=24)
    for posicao in (0, 1, 2):
        janela.registrar("a", VELHA, lista="L", posicao=posicao)

    assert not janela.lista_esgotada("L")


def test_depois_de_uma_vaga_nova_tres_velhas_seguidas_esgotam() -> None:
    janela = JanelaPublicacao(horas=24)
    janela.registrar("a", _nova(), lista="L", posicao=0)
    for posicao in (1, 2, 3):
        janela.registrar("a", VELHA, lista="L", posicao=posicao)

    assert janela.limite_da_lista("L") == 4
