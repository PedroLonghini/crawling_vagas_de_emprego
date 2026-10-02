"""Testes da leitura do log feita por scripts/medicao_completa.py."""

from scripts import medicao_completa

LOG = """\
2026-10-02 08:35:46 [scrapy.statscollectors] INFO: Dumping Scrapy stats:
{'downloader/request_count': 18,
 'downloader/response_bytes': 1568042,
 'downloader/response_count': 18,
 'downloader/response_status_count/200': 17,
 'downloader/response_status_count/404': 1,
 'elapsed_time_seconds': 22.5,
 'finish_time': datetime.datetime(2026, 10, 2, 11, 35, 46, tzinfo=datetime.timezone.utc),
 'observatorio/politica/bloqueios/dominio_ausente': 2,
 'retry/count': 1,
 'start_time': datetime.datetime(2026, 10, 2, 11, 35, 24, tzinfo=datetime.timezone.utc)}
2026-10-02 08:35:46 [scrapy.core.engine] INFO: Spider closed (finished)
2026-10-02 08:40:00 [scrapy.statscollectors] INFO: Dumping Scrapy stats:
{'downloader/request_count': 12,
 'downloader/response_status_count/200': 12,
 'elapsed_time_seconds': 40.0}
O bloco terminou com erro. Seus alvos serão repetidos individualmente.

# RESULTADO FINAL DO LOTE

## TEMPO POR FASE
- Coleta pela internet: 26.5s (77%)
- Leitura do inventário: 0.5s (1%)
- Extração: 7.8s (22%)
- Total: 34.8s

Alvos concluídos: 3
Alvos sem anúncios extraíveis: 1
Alvos com falha: 0
"""


def test_le_as_fases_do_final_do_log() -> None:
    assert medicao_completa.ler_fases(LOG) == {
        "Coleta pela internet": 26.5,
        "Leitura do inventário": 0.5,
        "Extração": 7.8,
        "Total": 34.8,
    }


def test_usa_a_ultima_secao_de_fases() -> None:
    texto = "## TEMPO POR FASE\n- Coleta pela internet: 1.0s (10%)\n\n" + LOG

    assert medicao_completa.ler_fases(texto)["Coleta pela internet"] == 26.5


def test_le_as_contagens_do_resultado() -> None:
    assert medicao_completa.ler_contagens(LOG) == {
        "Alvos concluídos": 3,
        "Alvos sem anúncios extraíveis": 1,
        "Alvos com falha": 0,
    }


def test_soma_as_estatisticas_de_todos_os_blocos_do_crawler() -> None:
    totais, blocos = medicao_completa.somar_estatisticas_scrapy(LOG)

    assert blocos == 2
    assert totais["downloader/request_count"] == 30
    assert totais["downloader/response_status_count/200"] == 29
    assert totais["downloader/response_status_count/404"] == 1
    assert totais["retry/count"] == 1
    assert totais["observatorio/politica/bloqueios/dominio_ausente"] == 2
    assert totais["maior_bloco_s"] == 40.0
    assert totais["soma_tempo_dos_blocos_s"] == 62.5


def test_conta_problemas_do_log() -> None:
    problemas = medicao_completa.contar_problemas(
        LOG + "\nTraceback (most recent call last):\nERRO X"
    )

    assert problemas["Traceback"] == 1
    assert problemas["ERRO"] == 1
    assert problemas["Bloco com erro"] == 1


def test_formata_duracao() -> None:
    assert medicao_completa._hms(3725) == "1h 02min 05s"
