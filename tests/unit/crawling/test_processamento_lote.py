"""Testes da tolerância a falhas do processamento em lote."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import processar_e_salvar_anuncios, processar_lote

from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.extraction import ResultadoProcessamentoExtracao


@pytest.fixture(autouse=True)
def _extracao_no_mesmo_processo(monkeypatch):
    """Processos filhos não enxergam os monkeypatches dos testes."""

    monkeypatch.setattr(processar_lote, "_processos_extracao_padrao", lambda: 1)


@pytest.fixture(autouse=True)
def _inventario_isolado_do_data_raw_real(monkeypatch):
    """Sem isso, testes sem --diretorio-raw liam o histórico real do data/raw."""

    monkeypatch.setattr(
        processar_lote, "carregar_inventario_bruto_desde", lambda base, *, desde, progresso=None: ()
    )


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

    def inventario(base, *, desde, progresso=None):
        leituras.append((base, desde))
        return snapshot

    def extrair(**kwargs):
        chamadas.append(kwargs)
        return processar_lote.CODIGO_SEM_ANUNCIOS

    def subprocesso_proibido(**kwargs):
        raise AssertionError("prévia não deve abrir subprocessos")

    monkeypatch.setattr(processar_lote, "carregar_inventario_bruto_desde", inventario)
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
    assert opcoes.processos_coleta == processar_lote._processos_coleta_padrao()
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
        (
            "valida,Empresa Válida,pagina_carreiras,https://valida.example/vagas,true,10,somente_coleta"
        ),
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
        (
            "valida,Empresa Válida,pagina_carreiras,https://valida.example/vagas,true,10,somente_coleta"
        ),
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
        processos=1,
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


def test_distribuicao_por_dominio_nao_separa_fontes_do_mesmo_site(tmp_path: Path) -> None:
    catalogo = _escrever_catalogo(
        tmp_path,
        "primeira,Empresa Um,outra,https://mesmo.example/vagas/1,true,5,somente_coleta",
        "segunda,Empresa Dois,outra,https://mesmo.example/vagas/2,true,5,somente_coleta",
        "terceira,Empresa Três,outra,https://outro.example/vagas,true,5,somente_coleta",
    )

    filas = processar_lote._distribuir_alvos_por_dominio(
        carregar_alvos_csv(catalogo),
        processos=3,
    )
    filas_por_alvo = {alvo.alvo_id: numero for numero, fila in enumerate(filas) for alvo in fila}

    assert set(filas_por_alvo) == {"primeira", "segunda", "terceira"}
    assert filas_por_alvo["primeira"] == filas_por_alvo["segunda"]


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


def test_extracao_paralela_isola_alvos_em_processos(tmp_path: Path, monkeypatch) -> None:
    """Com vários processos, cada alvo volta com seu código e seu log separado."""

    monkeypatch.setattr(processar_lote, "MINIMO_PAGINAS_PARALELISMO", 0)

    catalogo = _escrever_catalogo(
        tmp_path,
        "primeira,Empresa Um,outra,https://um.example/,true,10,somente_coleta",
        "segunda,Empresa Dois,outra,https://dois.example/,true,10,somente_coleta",
        "terceira,Empresa Tres,outra,https://tres.example/,true,10,somente_coleta",
    )
    alvos = carregar_alvos_csv(catalogo)

    resultados = dict(
        (alvo.alvo_id, codigo)
        for alvo, codigo in processar_lote._extrair_alvos(
            alvos,
            processos=3,
            registros_por_alvo={},
            diretorio_raw=tmp_path / "raw",
            coletado_desde=datetime(2026, 9, 11, tzinfo=UTC),
            confirmar=False,
            publicado_em=None,
        )
    )

    # Sem respostas brutas, todos os alvos terminam sem anúncios, cada um uma vez.
    assert resultados == {
        "primeira": processar_lote.CODIGO_SEM_ANUNCIOS,
        "segunda": processar_lote.CODIGO_SEM_ANUNCIOS,
        "terceira": processar_lote.CODIGO_SEM_ANUNCIOS,
    }


def test_parser_aceita_processos_de_extracao() -> None:
    opcoes = processar_lote.criar_parser().parse_args(
        ["--coletado-desde", "2026-09-11T00:00:00+00:00", "--processos-extracao", "6"]
    )

    assert opcoes.processos_extracao == 6


def test_alvo_grande_dividido_em_pedacos_igual_ao_sequencial(tmp_path: Path, monkeypatch, capsys):
    """O caminho paralelo por pedaços produz o mesmo resumo que o sequencial."""

    from observatorio_vagas.crawling.inventario import carregar_inventario_bruto
    from tests.unit.extraction.test_processador import _salvar_paginas_variadas

    _salvar_paginas_variadas(tmp_path / "raw")
    registros = carregar_inventario_bruto(tmp_path / "raw")
    catalogo = _escrever_catalogo(
        tmp_path,
        "empresa_exemplo,Empresa Exemplo,outra,https://empresa.example/,true,10,somente_coleta",
        "vazio,Empresa Vazia,outra,https://vazia.example/,true,10,somente_coleta",
    )
    alvos = carregar_alvos_csv(catalogo)

    def resumo(processos: int) -> tuple[dict[str, int], str]:
        codigos = dict(
            (alvo.alvo_id, codigo)
            for alvo, codigo in processar_lote._extrair_alvos(
                alvos,
                processos=processos,
                registros_por_alvo={"empresa_exemplo": registros, "vazio": ()},
                diretorio_raw=tmp_path / "raw",
                coletado_desde=datetime(2026, 9, 1, tzinfo=UTC),
                confirmar=False,
                publicado_em=None,
            )
        )
        saida = capsys.readouterr().out
        inicio = saida.index("Resumo da extração")
        return codigos, saida[inicio : saida.index("Anúncios preparados", inicio)]

    sequencial = resumo(1)
    monkeypatch.setattr(processar_lote, "TAMANHO_PEDACO_EXTRACAO", 4)
    monkeypatch.setattr(processar_lote, "MINIMO_PAGINAS_PARALELISMO", 0)
    paralelo = resumo(3)

    assert paralelo == sequencial
    assert sequencial[0] == {"empresa_exemplo": 0, "vazio": processar_lote.CODIGO_SEM_ANUNCIOS}
    assert "Anúncios únicos: 20" in sequencial[1]


def test_estado_incremental_vai_para_o_crawler_e_e_um_por_fila(tmp_path, monkeypatch):
    """Sem isto, o estado incremental era apagado junto com o catálogo temporário."""

    chamadas = []
    monkeypatch.setattr(
        processar_lote, "_executar_python", lambda **kwargs: chamadas.append(kwargs) or 0
    )
    estado = tmp_path / "estado" / "fila_2.json"

    processar_lote._executar_crawler(
        catalogo=tmp_path / "c.csv", etiqueta="t", limite_respostas=10, estado_incremental=estado
    )

    argumentos = chamadas[0]["argumentos"]
    assert argumentos[argumentos.index(f"estado_incremental={estado}") - 1] == "-a"
    assert processar_lote._arquivo_estado(tmp_path, 2) == tmp_path / "fila_2.json"
    assert processar_lote._arquivo_estado(None, 2) is None


def test_varredura_semanal_coleta_todas_as_fontes_sem_adiamento(tmp_path, monkeypatch):
    """A diária adia fontes sem novidade; a varredura semanal não pode adiar nenhuma."""

    chamadas = []
    monkeypatch.setattr(
        processar_lote, "_executar_python", lambda **kwargs: chamadas.append(kwargs) or 0
    )

    processar_lote._executar_crawler(catalogo=tmp_path / "c.csv", etiqueta="d", limite_respostas=10)
    processar_lote._executar_crawler(
        catalogo=tmp_path / "c.csv", etiqueta="s", limite_respostas=10, agendamento=False
    )

    diaria, semanal = (chamada["argumentos"] for chamada in chamadas)
    assert "usar_agendamento_inteligente=false" not in diaria
    assert semanal[semanal.index("usar_agendamento_inteligente=false") - 1] == "-a"


def test_dominios_grandes_ficam_em_filas_separadas(tmp_path: Path) -> None:
    linhas = [
        f"a{n},Empresa,outra,https://grande{g}.example/vagas/{n},true,5,somente_coleta"
        for g in (1, 2, 3)
        for n in range(10 * g, 10 * g + 6 - g)
    ]
    linhas.append("p1,Empresa,outra,https://pequeno.example/vagas,true,5,somente_coleta")
    alvos = carregar_alvos_csv(_escrever_catalogo(tmp_path, *linhas))

    filas = processar_lote._distribuir_alvos_por_dominio(alvos, 3)

    dominios_por_fila = [{alvo.dominio for alvo in fila} for fila in filas]
    grandes = [d for d in dominios_por_fila if any(x.startswith("grande") for x in d)]
    assert len(grandes) == 3
    assert all(sum(x.startswith("grande") for x in d) == 1 for d in dominios_por_fila)


def test_distribuicao_lembra_a_fila_de_cada_dominio(tmp_path: Path) -> None:
    memoria = tmp_path / "distribuicao.json"
    primeiro = carregar_alvos_csv(
        _escrever_catalogo(
            tmp_path,
            "a1,Empresa,outra,https://a.example/1,true,5,somente_coleta",
            "a2,Empresa,outra,https://a.example/2,true,5,somente_coleta",
            "b1,Empresa,outra,https://b.example/1,true,5,somente_coleta",
        )
    )
    antes = processar_lote._distribuir_alvos_por_dominio(primeiro, 2, memoria)

    # Um domínio novo e maior não pode tirar os antigos de suas filas.
    segundo = carregar_alvos_csv(
        _escrever_catalogo(
            tmp_path,
            *[f"c{n},Empresa,outra,https://c.example/{n},true,5,somente_coleta" for n in range(5)],
            "a1,Empresa,outra,https://a.example/1,true,5,somente_coleta",
            "b1,Empresa,outra,https://b.example/1,true,5,somente_coleta",
        )
    )
    depois = processar_lote._distribuir_alvos_por_dominio(segundo, 2, memoria)

    def fila_de(filas, dominio):
        return next(n for n, fila in enumerate(filas) if any(a.dominio == dominio for a in fila))

    assert fila_de(antes, "a.example") == fila_de(depois, "a.example")
    assert fila_de(antes, "b.example") == fila_de(depois, "b.example")


def test_distribuicao_mantem_filas_vazias_para_numerar_o_estado(tmp_path: Path) -> None:
    alvos = carregar_alvos_csv(
        _escrever_catalogo(tmp_path, "a1,Empresa,outra,https://a.example/1,true,5,somente_coleta")
    )

    filas = processar_lote._distribuir_alvos_por_dominio(alvos, 4)

    assert len(filas) == 4
    assert sum(1 for fila in filas if fila) == 1
