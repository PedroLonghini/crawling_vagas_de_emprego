"""Testes do pipeline que armazena respostas brutas."""

from datetime import UTC, datetime
from pathlib import Path

from pytest import TempPathFactory
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.settings import Settings

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.pipelines import (
    PipelineArmazenamentoBruto,
)
from observatorio_vagas.domain.enums import Fonte

# Data fixa para manter os testes previsíveis.
DATA_COLETA = datetime(
    2026,
    8,
    21,
    12,
    0,
    tzinfo=UTC,
)


# Corpo HTML falso utilizado somente nos testes.
CORPO_HTML = b"<html><body><h1>Vaga Python</h1></body></html>"


class SpiderTeste(Spider):
    """Spider mínimo usado somente pelos testes."""

    name = "spider_teste_pipeline"


def criar_resposta_bruta() -> RespostaBruta:
    """Cria uma resposta válida para os testes."""

    return RespostaBruta(
        fonte=Fonte.PAGINA_CARREIRAS,
        url_solicitada=("https://empresa.example/vagas/123"),
        url_final=("https://empresa.example/vagas/123"),
        status_http=200,
        corpo=CORPO_HTML,
        tipo_conteudo=("text/html; charset=utf-8"),
        codificacao="utf-8",
        coletado_em=DATA_COLETA,
    )


def test_pipeline_salva_resposta_bruta(
    tmp_path: Path,
) -> None:
    """Uma resposta bruta deve ser armazenada e devolvida."""

    # Cria o pipeline apontando para a pasta temporária.
    pipeline = PipelineArmazenamentoBruto(
        diretorio_base=tmp_path,
    )

    # Cria o objeto que será enviado ao pipeline.
    resposta = criar_resposta_bruta()

    # A nova assinatura recebe somente o item.
    item_devolvido = pipeline.process_item(
        item=resposta,
    )

    # O pipeline deve devolver o mesmo objeto.
    assert item_devolvido is resposta

    # O corpo original deve ser salvo.
    assert len(list(tmp_path.rglob("*.bin*"))) == 1

    # Os metadados devem ser salvos.
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_pipeline_ignora_outros_tipos_de_item(
    tmp_path: Path,
) -> None:
    """Itens diferentes de RespostaBruta seguem sem alteração."""

    # Cada teste precisa criar seu próprio pipeline.
    pipeline = PipelineArmazenamentoBruto(
        diretorio_base=tmp_path,
    )

    # Este dicionário representa outro tipo de item.
    outro_item = {
        "titulo": "Pessoa Desenvolvedora Python",
    }

    item_devolvido = pipeline.process_item(
        item=outro_item,
    )

    # O objeto deve passar sem alteração.
    assert item_devolvido is outro_item

    # Nenhum arquivo deve ser criado.
    assert not list(tmp_path.rglob("*.bin*"))

    assert not list(tmp_path.rglob("*.json"))


def test_pipeline_usa_diretorio_configurado_no_scrapy(
    tmp_path_factory: TempPathFactory,
) -> None:
    """O pipeline deve respeitar a configuração do Scrapy."""

    diretorio_configurado = tmp_path_factory.mktemp("p")

    configuracoes = Settings(
        {
            "RAW_STORAGE_DIRECTORY": str(diretorio_configurado),
        }
    )

    # SpiderTeste ainda é necessário aqui para criar o Crawler.
    crawler = Crawler(
        spidercls=SpiderTeste,
        settings=configuracoes,
    )

    pipeline = PipelineArmazenamentoBruto.from_crawler(
        crawler=crawler,
    )

    pipeline.process_item(
        item=criar_resposta_bruta(),
    )

    assert len(list(diretorio_configurado.rglob("*.bin*"))) == 1

    assert len(list(diretorio_configurado.rglob("*.json"))) == 1
