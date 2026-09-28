"""Testes da tolerância a falhas do processamento em lote."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from scripts import processar_e_salvar_anuncios, processar_lote

from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.extraction import ResultadoProcessamentoExtracao


def _resultado_vazio() -> ResultadoProcessamentoExtracao:
    """Cria uma extração válida que não encontrou vagas."""

    return ResultadoProcessamentoExtracao(
        anuncios=(),
        paginas_analisadas=0,
        paginas_ignoradas=1,
        paginas_sem_json_ld=0,
        documentos_encontrados=0,
        anuncios_duplicados=0,
        blocos_invalidos=0,
        falhas=(),
    )


def test_lote_compartilha_inventario_sem_subprocesso(tmp_path, monkeypatch) -> None:
    catalogo = _escrever_catalogo(
        tmp_path,
        "primeira,Empresa Um,outra,https://um.example/,true,10,somente_coleta",
        "segunda,Empresa Dois,outra,https://dois.example/,true,10,somente_coleta",
    )
    leituras = []
    chamadas = []
    snapshot = ()

    def inventario(base):
        leituras.append(base)
        return snapshot

    def extrair(**kwargs):
        chamadas.append(kwargs)
        return processar_lote.CODIGO_SEM_ANUNCIOS

    def subprocesso_proibido(**kwargs):
        raise AssertionError("prévia não deve abrir subprocessos")

    monkeypatch.setattr(processar_lote, "carregar_inventario_bruto", inventario)
    monkeypatch.setattr(processar_lote, "executar_extracao", extrair)
    monkeypatch.setattr(processar_lote, "_executar_python", subprocesso_proibido)
    codigo = processar_lote.executar(
        [
            "--catalogo",
            str(catalogo),
            "--diretorio-raw",
            str(tmp_path),
            "--coletado-desde",
            "2026-09-11T00:00:00+00:00",
        ]
    )
    assert codigo == 0
    assert len(leituras) == 1
    assert [c["alvo_id"] for c in chamadas] == ["primeira", "segunda"]
    assert all(c["registros"] is snapshot and not c["confirmar"] for c in chamadas)


def test_extracao_isola_excecao_inesperada(tmp_path, monkeypatch):
    catalogo = _escrever_catalogo(
        tmp_path,
        "alvo,Empresa,outra,https://um.example/,true,10,somente_coleta",
    )

    def falhar(**kwargs):
        raise RuntimeError("falha do adaptador")

    monkeypatch.setattr(processar_lote, "executar_extracao", falhar)
    assert (
        processar_lote._executar_extracao(
            alvo=carregar_alvos_csv(catalogo)[0],
            diretorio_raw=tmp_path,
            coletado_desde=datetime(2026, 9, 11, tzinfo=UTC),
            confirmar=False,
            registros=(),
        )
        == 1
    )


def test_parser_usa_biblioteca_central_por_padrao() -> None:
    """O lote pode ser iniciado sem repetir o caminho do catálogo oficial."""

    opcoes = processar_lote.criar_parser().parse_args(
        [
            "--coletado-desde",
            datetime(2026, 8, 28, tzinfo=UTC).isoformat(),
        ]
    )

    assert opcoes.catalogo == Path("config/catalogo_fontes.csv")
    assert opcoes.alvos_por_coleta == 200
    assert opcoes.trabalhadores_posprocessamento == processar_lote._trabalhadores_padrao()


def test_lote_somente_republicaveis_ignora_fonte_somente_coleta(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """O filtro não permite que uma fonte de análise entre no lote publicado."""

    catalogo = _escrever_catalogo(
        tmp_path,
        "coleta,Coleta,outra,https://coleta.example/,true,10,somente_coleta",
        (
            "publica,Pública,outra,https://publica.example/,true,10,aprovada,"
                "CC BY 4.0,https://creativecommons.org/licenses/by/4.0/,true,true"
        ),
    )
    processados: list[str] = []

    def extrair(**kwargs: object) -> int:
        processados.append(kwargs["alvo"].alvo_id)
        return processar_lote.CODIGO_SEM_ANUNCIOS

    monkeypatch.setattr(processar_lote, "_executar_extracao", extrair)
    codigo = processar_lote.executar(
        [
            "--catalogo",
            str(catalogo),
            "--coletado-desde",
            "2026-09-14T00:00:00+00:00",
            "--somente-republicaveis",
        ]
    )

    assert codigo == 0
    assert processados == ["publica"]


def _escrever_catalogo(
    tmp_path: Path,
    *linhas: str,
) -> Path:
    """Escreve um catálogo completo para o orquestrador."""

    caminho = tmp_path / "fontes_lote.csv"
    cabecalho = (
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica,"
        "licenca_nome,licenca_url,atribuicao_obrigatoria,republicacao_permitida"
    )

    caminho.write_text(
        "\n".join(
            [
                cabecalho,
                *linhas,
                "",
            ]
        ),
        encoding="utf-8",
    )

    return caminho


def test_extrator_pode_sinalizar_resultado_sem_anuncios(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """O lote precisa distinguir resultado vazio de falha técnica."""

    monkeypatch.setattr(
        processar_e_salvar_anuncios,
        "processar_respostas_brutas",
        lambda *args, **kwargs: _resultado_vazio(),
    )

    codigo = processar_e_salvar_anuncios.executar(
        alvo_id="sem_vagas",
        diretorio_raw=tmp_path,
        coletado_desde=None,
        confirmar=False,
        sinalizar_sem_anuncios=True,
    )

    assert codigo == processar_e_salvar_anuncios.CODIGO_SEM_ANUNCIOS


def test_lote_ignora_linha_invalida_e_alvo_sem_anuncios(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    """Problemas esperados não interrompem os demais alvos."""

    catalogo = _escrever_catalogo(
        tmp_path,
        "quebrada,Empresa Ruim,outra,url-invalida,true,10,somente_coleta",
        ("valida,Empresa Válida,gupy,https://valida.gupy.io/,true,10,somente_coleta"),
    )

    monkeypatch.setattr(
        processar_lote,
        "_executar_extracao",
        lambda **kwargs: processar_lote.CODIGO_SEM_ANUNCIOS,
    )

    codigo = processar_lote.executar(
        [
            "--catalogo",
            str(catalogo),
            "--coletado-desde",
            datetime(2026, 8, 28, tzinfo=UTC).isoformat(),
        ]
    )

    saida = capsys.readouterr().out

    assert codigo == 0
    assert "Linhas inválidas ignoradas: 1" in saida
    assert "Alvos sem anúncios extraíveis: 1" in saida
    assert "valida | IGNORADO" in saida


def test_lote_isola_dominio_proibido_e_processa_alvo_seguinte(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    """Uma fonte proibida deve ser explicada sem interromper o lote."""

    catalogo = _escrever_catalogo(
        tmp_path,
        "indeed,Indeed,outra,https://br.indeed.com/jobs,true,10,somente_coleta",
        ("valida,Empresa Válida,gupy,https://valida.gupy.io/,true,10,somente_coleta"),
    )
    processados: list[str] = []

    def executar_extracao_falsa(**kwargs: object) -> int:
        processados.append(kwargs["alvo"].alvo_id)
        return processar_lote.CODIGO_SEM_ANUNCIOS

    monkeypatch.setattr(
        processar_lote,
        "_executar_extracao",
        executar_extracao_falsa,
    )

    codigo = processar_lote.executar(
        [
            "--catalogo",
            str(catalogo),
            "--coletado-desde",
            datetime(2026, 8, 28, tzinfo=UTC).isoformat(),
        ]
    )
    saida = capsys.readouterr().out

    assert codigo == 0
    assert processados == ["valida"]
    assert "Indeed" in saida
    assert "Linhas inválidas ignoradas: 1" in saida
    assert "Alvos sem anúncios extraíveis: 1" in saida


def test_lote_continua_depois_de_formato_incompativel(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    """Uma extração incompatível não impede o alvo seguinte."""

    catalogo = _escrever_catalogo(
        tmp_path,
        "primeira,Empresa Um,outra,https://um.example/,true,10,somente_coleta",
        "segunda,Empresa Dois,outra,https://dois.example/,true,10,somente_coleta",
    )
    processados: list[str] = []

    def executar_extracao_falsa(**kwargs: object) -> int:
        alvo = kwargs["alvo"]
        processados.append(alvo.alvo_id)

        if alvo.alvo_id == "primeira":
            return processar_lote.CODIGO_EXTRACAO_COM_FALHAS

        return 0

    monkeypatch.setattr(
        processar_lote,
        "_executar_extracao",
        executar_extracao_falsa,
    )

    codigo = processar_lote.executar(
        [
            "--catalogo",
            str(catalogo),
            "--coletado-desde",
            datetime(2026, 8, 28, tzinfo=UTC).isoformat(),
        ]
    )

    saida = capsys.readouterr().out

    assert codigo == 0
    assert processados == [
        "primeira",
        "segunda",
    ]
    assert "Alvos incompatíveis ignorados: 1" in saida
    assert "Alvos concluídos: 1" in saida


def test_coleta_fragmenta_e_recupera_bloco_com_erro(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Um bloco com erro é repetido por alvo sem afetar os outros blocos."""

    catalogo = _escrever_catalogo(
        tmp_path,
        "um,Empresa Um,outra,https://um.example/,true,5,somente_coleta",
        "dois,Empresa Dois,outra,https://dois.example/,true,5,somente_coleta",
        "tres,Empresa Três,outra,https://tres.example/,true,5,somente_coleta",
    )
    alvos = carregar_alvos_csv(catalogo)
    chamadas: list[tuple[str, ...]] = []

    def executar_crawler_falso(**kwargs: object) -> int:
        assert kwargs["diretorio_raw"] == tmp_path / "raw isolado"
        alvos_chamada = carregar_alvos_csv(kwargs["catalogo"])
        ids = tuple(alvo.alvo_id for alvo in alvos_chamada)
        chamadas.append(ids)

        if ids == ("um", "dois"):
            return 1

        if ids == ("um",):
            return 1

        return 0

    monkeypatch.setattr(
        processar_lote,
        "_executar_crawler",
        executar_crawler_falso,
    )

    falhas = processar_lote._coletar_em_blocos(
        alvos=alvos,
        tamanho_bloco=2,
        diretorio_raw=tmp_path / "raw isolado",
    )

    assert chamadas == [
        ("um", "dois"),
        ("um",),
        ("dois",),
        ("tres",),
    ]
    assert falhas == {
        "um": 1,
    }


def test_indice_isola_alvos_periodo_e_preserva_ordem():
    inicio = datetime(2026, 9, 11, tzinfo=UTC)
    recente = SimpleNamespace(alvo_id="um", coletado_em=datetime(2026, 9, 12, tzinfo=UTC))
    limite = SimpleNamespace(alvo_id="um", coletado_em=inicio)
    antigo = SimpleNamespace(alvo_id="um", coletado_em=datetime(2026, 9, 10, tzinfo=UTC))
    externo = SimpleNamespace(alvo_id="outro", coletado_em=inicio)
    sem_alvo = SimpleNamespace(alvo_id=None, coletado_em=inicio)
    indice, descartados = processar_lote._indexar_registros_do_lote(
        (recente, externo, limite, antigo, sem_alvo),
        (SimpleNamespace(alvo_id="um"), SimpleNamespace(alvo_id="vazio")),
        inicio,
    )
    assert indice == {"um": (recente, limite), "vazio": ()}
    assert descartados == 3


def test_crawler_recebe_diretorio_raw_com_espacos(tmp_path, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        processar_lote, "_executar_python", lambda **kwargs: chamadas.append(kwargs) or 0
    )
    diretorio = tmp_path / "raw isolado"
    assert (
        processar_lote._executar_crawler(
            catalogo=tmp_path / "catalogo.csv",
            etiqueta="teste",
            limite_respostas=10,
            diretorio_raw=diretorio,
        )
        == 0
    )
    argumentos = chamadas[0]["argumentos"]
    posicao = argumentos.index(f"RAW_STORAGE_DIRECTORY={diretorio}")
    assert argumentos[posicao - 1] == "-s"
