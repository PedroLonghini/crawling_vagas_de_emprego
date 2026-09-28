"""O teste isolado nunca envia outras fontes ao crawler."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import testar_fonte


def test_teste_isola_catalogo_raw_e_limites(tmp_path, monkeypatch):
    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica\n"
        "primeira,Primeira,outra,https://primeira.example/vagas,true,10,somente_coleta\n"
        "segunda,Segunda,outra,https://segunda.example/vagas,true,10,somente_coleta\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(testar_fonte, "RAIZ_PROJETO", tmp_path)

    def executar(comando, **kwargs):
        csv = Path(next(v.split("=", 1)[1] for v in comando if v.startswith("catalogo=")))
        assert "primeira.example" in csv.read_text()
        assert "segunda.example" not in csv.read_text()
        assert "limite_anuncios=7" in comando
        assert "JAVASCRIPT_ENABLED=True" in comando
        raw = Path(
            next(v.split("=", 1)[1] for v in comando if v.startswith("RAW_STORAGE_DIRECTORY="))
        )
        assert raw.parent == csv.parent
        raw.mkdir()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(testar_fonte.subprocess, "run", executar)
    monkeypatch.setattr(
        testar_fonte,
        "processar_respostas_brutas",
        lambda *a, **kw: SimpleNamespace(
            anuncios=(),
            paginas_analisadas=1,
            paginas_sem_json_ld=1,
            falhas=(),
            anuncios_fora_data_publicacao=0,
            anuncios_sem_data_publicacao=0,
        ),
    )
    assert (
        testar_fonte.main(
            [
                "--catalogo",
                str(catalogo),
                "--alvo-id",
                "primeira",
                "--javascript",
                "--anuncios",
                "7",
            ]
        )
        == 0
    )
    relatorio = next(tmp_path.glob("outputs/testes_fontes/*/resumo.json"))
    assert json.loads(relatorio.read_text())["anuncios_unicos"] == 0


def test_id_inexistente_nao_seleciona_outra_fonte():
    with pytest.raises(ValueError, match="ausente"):
        testar_fonte.selecionar_alvo([], "inexistente")


def test_teste_todas_fontes_cria_manifesto_com_resultados_isolados(tmp_path, monkeypatch):
    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica\n"
        "primeira,Primeira,outra,https://primeira.example/vagas,true,10,somente_coleta\n"
        "segunda,Segunda,outra,https://segunda.example/vagas,true,10,somente_coleta\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(testar_fonte, "RAIZ_PROJETO", tmp_path)

    def executar(comando, **kwargs):
        raw = Path(
            next(v.split("=", 1)[1] for v in comando if v.startswith("RAW_STORAGE_DIRECTORY="))
        )
        raw.mkdir()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(testar_fonte.subprocess, "run", executar)
    monkeypatch.setattr(
        testar_fonte,
        "processar_respostas_brutas",
        lambda *a, **kw: SimpleNamespace(
            anuncios=(),
            paginas_analisadas=1,
            paginas_sem_json_ld=1,
            falhas=(),
            anuncios_fora_data_publicacao=0,
            anuncios_sem_data_publicacao=0,
        ),
    )

    assert testar_fonte.main(["--catalogo", str(catalogo), "--todas"]) == 0

    manifesto = next(tmp_path.glob("outputs/testes_fontes/lote_*/manifesto.json"))
    resultado = json.loads(manifesto.read_text(encoding="utf-8"))
    assert resultado["fontes_selecionadas"] == 2
    assert [item["alvo_id"] for item in resultado["resultados"]] == ["primeira", "segunda"]
    assert all(item["resultado"] is not None for item in resultado["resultados"])


def test_metricas_de_extracao_separam_campos_observaveis_de_elegibilidade():
    anuncio_completo = SimpleNamespace(
        titulo_original="Pessoa Desenvolvedora",
        descricao_original="Descrição explícita da vaga.",
        empresa_original="Empresa Teste",
        localidade_original="São Paulo",
        endereco_original=None,
        url_candidatura="https://empresa.example/candidatar",
        expira_em=None,
    )
    anuncio_incompleto = SimpleNamespace(
        titulo_original="Analista",
        descricao_original="",
        empresa_original=None,
        localidade_original=None,
        endereco_original=None,
        url_candidatura=None,
        expira_em=None,
    )
    resultado = SimpleNamespace(
        anuncios=(anuncio_completo, anuncio_incompleto),
        documentos_encontrados=3,
        anuncios_duplicados=1,
        paginas_ignoradas=2,
        paginas_incompativeis=1,
        blocos_invalidos=4,
    )

    metricas = testar_fonte.calcular_metricas_extracao(resultado)

    assert metricas["documentos_encontrados"] == 3
    assert metricas["anuncios_duplicados"] == 1
    assert metricas["campos_observaveis"]["titulo"] == 2
    assert metricas["campos_observaveis"]["empresa"] == 1
    assert metricas["anuncios_com_campos_minimos_observaveis"] == 1
