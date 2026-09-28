"""Testes do cache incremental de páginas de carreira."""

from datetime import date, timedelta
from pathlib import Path

from scrapy import Request
from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.estado_incremental import EstadoIncrementalLocal


def _resposta(
    *,
    url: str,
    corpo: bytes,
    status: int = 200,
    headers: dict[bytes, bytes] | None = None,
):
    return HtmlResponse(
        url=url,
        request=Request(url),
        body=corpo,
        status=status,
        encoding="utf-8",
        headers=headers or {},
    )


def test_primeira_resposta_eh_registrada_sem_ser_inalterada(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")

    inalterada = estado.resposta_inalterada(
        alvo_id="empresa",
        url="https://empresa.example/carreiras",
        resposta=_resposta(
            url="https://empresa.example/carreiras",
            corpo=b"<h1>Vagas</h1>",
            headers={b"ETag": b'"versao-1"', b"Last-Modified": b"Tue, 24 Sep 2026 10:00:00 GMT"},
        ),
    )

    assert inalterada is False
    assert estado.cabecalhos_condicionais(
        alvo_id="empresa", url="https://empresa.example/carreiras"
    ) == {
        "If-None-Match": '"versao-1"',
        "If-Modified-Since": "Tue, 24 Sep 2026 10:00:00 GMT",
    }


def test_mesmo_conteudo_eh_reconhecido_apos_reabrir_cache(tmp_path: Path) -> None:
    caminho = tmp_path / "cache.json"
    url = "https://empresa.example/carreiras"
    primeira = EstadoIncrementalLocal(caminho)
    primeira.resposta_inalterada(
        alvo_id="empresa", url=url, resposta=_resposta(url=url, corpo=b"<h1>Vagas</h1>")
    )

    segunda = EstadoIncrementalLocal(caminho)
    assert segunda.resposta_inalterada(
        alvo_id="empresa", url=url, resposta=_resposta(url=url, corpo=b"<h1>Vagas</h1>")
    ) is True


def test_304_reaproveita_registro_anterior(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    url = "https://empresa.example/carreiras"
    estado.resposta_inalterada(
        alvo_id="empresa", url=url, resposta=_resposta(url=url, corpo=b"<h1>Vagas</h1>")
    )

    assert estado.resposta_inalterada(
        alvo_id="empresa", url=url, resposta=_resposta(url=url, corpo=b"", status=304)
    ) is True


def test_detalhe_so_eh_conhecido_depois_de_resposta_com_sucesso(tmp_path: Path) -> None:
    caminho = tmp_path / "cache.json"
    estado = EstadoIncrementalLocal(caminho)
    url = "https://empresa.example/vagas/123"

    assert estado.detalhe_conhecido(alvo_id="empresa", url=url) is False
    estado.registrar_detalhe_sucesso(alvo_id="empresa", url=url)
    estado.salvar()

    recarregado = EstadoIncrementalLocal(caminho)
    assert recarregado.detalhe_conhecido(alvo_id="empresa", url=url) is True
    assert recarregado.prioridade("empresa")[0] > 0


def test_fonte_instavel_recebe_timeout_curto_e_sem_retry(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    for _ in range(3):
        estado.registrar_falha(alvo_id="instavel")

    assert estado.configuracao_download("instavel") == (8.0, 0)
    assert estado.fila("instavel") == "lenta"


def test_fonte_lenta_e_bem_sucedida_recebe_tolerancia_maior(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    for _ in range(3):
        resposta = _resposta(url="https://lenta.example/carreiras", corpo=b"ok")
        resposta.request.meta["download_latency"] = 12.0
        estado.registrar_resposta(
            alvo_id="lenta",
            resposta=resposta,
        )

    assert estado.configuracao_download("lenta") == (30.0, 2)


def test_agendamento_so_espaca_fonte_apos_tres_coletas_sem_novidade(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    hoje = date(2026, 9, 24)
    for _ in range(3):
        estado.registrar_execucao(alvo_id="sem_vagas", detalhes_novos=0, hoje=hoje)

    assert estado.deve_coletar_hoje("sem_vagas", hoje=hoje) is False
    assert estado.deve_coletar_hoje("sem_vagas", hoje=date(2026, 9, 27)) is True


def test_vaga_nova_mantem_fonte_na_rotina_diaria(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    hoje = date(2026, 9, 24)
    estado.registrar_execucao(alvo_id="produtiva", detalhes_novos=4, hoje=hoje)

    assert estado.deve_coletar_hoje("produtiva", hoje=date(2026, 9, 25)) is True


def test_circuit_breaker_adia_fonte_apos_tres_falhas(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    hoje = date(2026, 9, 25)
    for _ in range(3):
        estado.registrar_falha(alvo_id="indisponivel")

    estado.registrar_execucao(alvo_id="indisponivel", detalhes_novos=0, hoje=hoje)

    assert estado.fila("indisponivel") == "lenta"
    assert estado.deve_coletar_hoje("indisponivel", hoje=hoje) is False
    assert estado.deve_coletar_hoje("indisponivel", hoje=hoje + timedelta(days=1)) is True


def test_vaga_ausente_por_14_dias_vira_candidata_a_revisao(tmp_path: Path) -> None:
    estado = EstadoIncrementalLocal(tmp_path / "cache.json")
    url = "https://empresa.example/vagas/123"
    estado.registrar_detalhe_sucesso(alvo_id="empresa", url=url)

    assert estado.possiveis_encerradas(alvo_id="empresa", hoje=date.today()) == ()
    assert estado.possiveis_encerradas(
        alvo_id="empresa", hoje=date.today() + timedelta(days=14)
    ) == (url,)
