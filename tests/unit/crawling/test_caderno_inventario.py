"""Testes do caderno JSONL que acelera a leitura do inventário."""

from datetime import datetime
from pathlib import Path

import pytest

from observatorio_vagas.crawling.inventario import (
    ErroInventarioBruto,
    carregar_inventario_bruto,
    carregar_inventario_bruto_de_cadernos,
)
from observatorio_vagas.crawling.pipelines import PipelineArmazenamentoBruto
from observatorio_vagas.crawling.raw_storage import ArmazenamentoBrutoLocal

from .test_raw_storage import DATA_INICIAL, DATA_SEGUINTE, criar_resposta


def _salvar_duas_respostas(base: Path, caderno: Path) -> None:
    armazenamento = ArmazenamentoBrutoLocal(base, caminho_caderno=caderno)
    armazenamento.salvar(criar_resposta(url="https://empresa.example/vagas/1"))
    armazenamento.salvar(
        criar_resposta(url="https://empresa.example/vagas/2", coletado_em=DATA_SEGUINTE)
    )
    armazenamento.fechar()


def test_caderno_produz_os_mesmos_registros_que_os_arquivos(tmp_path: Path) -> None:
    """Ler o caderno deve ser equivalente a ler os JSONs individuais."""

    caderno = tmp_path / "cadernos" / "lote.jsonl"
    _salvar_duas_respostas(tmp_path, caderno)

    pelos_arquivos = carregar_inventario_bruto(tmp_path)
    pelo_caderno = carregar_inventario_bruto_de_cadernos([caderno])

    assert len(pelo_caderno) == 2
    assert pelo_caderno == pelos_arquivos


def test_caderno_descarta_somente_ultima_linha_cortada(tmp_path: Path) -> None:
    """Um processo interrompido pode deixar a última linha pela metade."""

    caderno = tmp_path / "lote.jsonl"
    _salvar_duas_respostas(tmp_path, caderno)

    with caderno.open("ab") as arquivo:
        arquivo.write(b'{"referencia": "respostas/cort')

    assert len(carregar_inventario_bruto_de_cadernos([caderno])) == 2


def test_caderno_recusa_linha_invalida_no_meio(tmp_path: Path) -> None:
    """Corrupção no meio do arquivo não pode ser ignorada em silêncio."""

    caderno = tmp_path / "lote.jsonl"
    _salvar_duas_respostas(tmp_path, caderno)
    linhas = caderno.read_bytes().split(b"\n")
    caderno.write_bytes(b"\n".join([linhas[0], b"{quebrado", *linhas[1:]]))

    with pytest.raises(ErroInventarioBruto, match="linha 2"):
        carregar_inventario_bruto_de_cadernos([caderno])


def test_cadernos_repetidos_nao_duplicam_registros(tmp_path: Path) -> None:
    """A mesma resposta em dois cadernos aparece uma vez só."""

    primeiro = tmp_path / "a.jsonl"
    segundo = tmp_path / "b.jsonl"
    armazenamento = ArmazenamentoBrutoLocal(tmp_path, caminho_caderno=primeiro)
    armazenamento.salvar(criar_resposta(coletado_em=DATA_INICIAL))
    armazenamento.fechar()
    segundo.write_bytes(primeiro.read_bytes())

    assert len(carregar_inventario_bruto_de_cadernos([primeiro, segundo])) == 1


def test_sem_caderno_o_armazenamento_continua_igual(tmp_path: Path) -> None:
    ArmazenamentoBrutoLocal(tmp_path).salvar(criar_resposta())

    assert not list(tmp_path.rglob("*.jsonl"))
    assert len(carregar_inventario_bruto(tmp_path)) == 1


def test_pipeline_grava_caderno_da_configuracao(tmp_path: Path) -> None:
    """O lote informa o caderno ao Scrapy por RAW_INDEX_FILE."""

    from types import SimpleNamespace

    caderno = tmp_path / "lote.jsonl"
    configuracoes = {"RAW_STORAGE_DIRECTORY": str(tmp_path), "RAW_INDEX_FILE": str(caderno)}
    crawler = SimpleNamespace(
        settings=SimpleNamespace(get=lambda chave, padrao=None: configuracoes.get(chave, padrao))
    )

    pipeline = PipelineArmazenamentoBruto.from_crawler(crawler)
    pipeline.process_item(criar_resposta())
    pipeline.close_spider()

    assert len(carregar_inventario_bruto_de_cadernos([caderno])) == 1


def test_releitura_usa_o_indice_e_le_so_o_que_e_novo(tmp_path: Path, monkeypatch) -> None:
    """A segunda releitura não abre de novo os metadados já lidos."""

    from datetime import timedelta

    from observatorio_vagas.crawling import inventario

    _salvar_duas_respostas(tmp_path, tmp_path / "cadernos" / "lote.jsonl")
    desde = DATA_INICIAL - timedelta(days=1)
    monkeypatch.setattr(inventario, "datetime", _RelogioFixo)

    primeira = inventario.carregar_inventario_bruto_desde(tmp_path, desde=desde)
    assert len(primeira) == 2
    assert list((tmp_path / "indices_metadados").rglob("*.jsonl"))

    abertos: list[Path] = []
    original = inventario._ler_metadados
    monkeypatch.setattr(
        inventario, "_ler_metadados", lambda c, b: abertos.append(c) or original(c, b)
    )
    segunda = inventario.carregar_inventario_bruto_desde(tmp_path, desde=desde)

    assert segunda == primeira
    assert abertos == []


class _RelogioFixo(datetime):
    """``datetime.now`` no dia seguinte às respostas de teste."""

    @classmethod
    def now(cls, tz=None):
        from datetime import timedelta

        return DATA_SEGUINTE + timedelta(days=1)
