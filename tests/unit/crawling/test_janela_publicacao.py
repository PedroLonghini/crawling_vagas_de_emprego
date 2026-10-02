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


def test_fonte_so_encerra_com_tres_velhas_seguidas() -> None:
    janela = JanelaPublicacao(horas=24)
    velha = DataPublicacao(datetime(2026, 9, 1, tzinfo=UTC), True)
    nova = DataPublicacao(datetime.now(UTC), True)

    assert janela.registrar("a", velha) and janela.registrar("a", velha)
    assert not janela.encerrada("a")
    assert not janela.registrar("a", nova)  # reinicia a contagem
    assert janela.registrar("a", velha) and janela.registrar("a", velha)
    assert not janela.encerrada("a")
    assert janela.registrar("a", velha)
    assert janela.encerrada("a")


def test_pagina_sem_data_nunca_encerra() -> None:
    janela = JanelaPublicacao(horas=24)

    for _ in range(10):
        assert janela.registrar("a", None) is False

    assert not janela.encerrada("a")


def test_fontes_sao_independentes() -> None:
    janela = JanelaPublicacao(horas=24, velhas_seguidas_para_encerrar=1)
    janela.registrar("a", DataPublicacao(datetime(2026, 9, 1, tzinfo=UTC), True))

    assert janela.encerrada("a")
    assert not janela.encerrada("b")


def test_middleware_descarta_requisicoes_de_fonte_encerrada() -> None:
    class Estatisticas:
        def inc_value(self, *_: object) -> None: ...

    class Crawler:
        stats = Estatisticas()
        spider = None

    janela = JanelaPublicacao(horas=24, velhas_seguidas_para_encerrar=1)
    janela.registrar("a", DataPublicacao(datetime(2026, 9, 1, tzinfo=UTC), True))

    class Spider:
        janela_publicacao = janela

    middleware = EncerramentoPorIdadeDownloaderMiddleware(Crawler())  # type: ignore[arg-type]

    with pytest.raises(IgnoreRequest):
        middleware.process_request(
            Request("https://exemplo.com/1", meta={"observatorio_alvo_id": "a"}), Spider()
        )  # type: ignore[arg-type]

    middleware.process_request(
        Request("https://exemplo.com/2", meta={"observatorio_alvo_id": "b"}), Spider()
    )  # type: ignore[arg-type]
