"""Conferência de vagas que saíram da origem, sem MongoDB nem rede."""

import json
from datetime import date

from scripts.conferir_vagas_removidas import (
    avaliar,
    carregar_coberturas,
    carregar_vistos,
    verificar_suspeitas,
)

DIA = date(2026, 10, 8)
LISTAGEM = "https://empresa.com.br/vagas"


def url(numero):
    return f"https://empresa.com.br/vagas/cargo-{numero}"


def cobertura(**mudancas):
    fonte = {
        "alvo_id": "empresa",
        "janela_horas": None,
        "janela_encerrada": False,
        "motivos_fim_navegacao": [],
        "listagens_nao_visitadas": 0,
        "listagens_sem_resposta": 0,
        "listagens_descartadas": 0,
        "listagens_inalteradas": 0,
        "encerramento_coleta": "finished",
        "resultados": {LISTAGEM: {"tipo_pagina": "inicial", "status_http": 200}},
    }
    fonte.update(mudancas)
    return {"empresa": fonte}


def anuncios(*numeros, status="descoberto"):
    return {"empresa": [{"_id": n, "url": url(n), "status": status} for n in numeros]}


def vistos(*numeros, dia=DIA):
    """Vagas 1 a 9 já apareceram numa listagem antes; ``numeros`` aparecem no dia."""

    anteriores = {url(n): "2026-10-01" for n in range(1, 10)}
    return {"empresa": {**anteriores, **{url(n): dia.isoformat() for n in numeros}}}


def acoes(resultado):
    return {d.anuncio_id: (d.acao, d.ausencias_seguidas) for d in resultado.decisoes}


def test_vaga_que_sumiu_ganha_primeira_ausencia():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2, 3),
        anuncios_por_alvo=anuncios(1, 2, 3, 4),
        presencas={},
        dia=DIA,
    )
    assert acoes(resultado) == {4: ("ausente", 1)}
    assert resultado.fontes[0]["completa"] is True


def test_segunda_ausencia_vira_suspeita_e_mesmo_dia_nao_conta_duas_vezes():
    presencas = {4: {"ausencias_seguidas": 1, "ultima_ausencia_em": "2026-10-07"}}
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2, 3),
        anuncios_por_alvo=anuncios(1, 2, 3, 4),
        presencas=presencas,
        dia=DIA,
    )
    assert acoes(resultado) == {4: ("suspeita", 2)}

    presencas = {4: {"ausencias_seguidas": 2, "ultima_ausencia_em": DIA.isoformat()}}
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2, 3),
        anuncios_por_alvo=anuncios(1, 2, 3, 4),
        presencas=presencas,
        dia=DIA,
    )
    assert acoes(resultado) == {4: ("suspeita", 2)}


def test_vaga_ausente_que_reaparece_volta_a_ativa():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1),
        anuncios_por_alvo=anuncios(1, status="ausente_aguardando_confirmacao"),
        presencas={1: {"ausencias_seguidas": 1}},
        dia=DIA,
    )
    assert acoes(resultado) == {1: ("vista", 0)}


def test_coleta_com_janela_nao_gera_ausencia():
    resultado = avaliar(
        coberturas=cobertura(janela_horas=24),
        vistos=vistos(1),
        anuncios_por_alvo=anuncios(1, 2),
        presencas={},
        dia=DIA,
    )
    assert resultado.decisoes == []
    assert resultado.fontes[0]["motivo"] == "coleta_com_janela_de_horas"


def test_queda_brusca_nao_gera_ausencia_em_massa():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1),
        anuncios_por_alvo=anuncios(1, 2, 3, 4, 5),
        presencas={},
        dia=DIA,
    )
    assert resultado.decisoes == []
    assert resultado.fontes[0]["motivo"] == "queda_brusca_de_vagas_vistas"


def test_link_visto_em_outro_dia_conta_como_ausente():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos={"empresa": {url(1): DIA.isoformat(), url(2): "2026-10-01"}},
        anuncios_por_alvo=anuncios(1, 2),
        presencas={},
        dia=DIA,
    )
    assert acoes(resultado) == {2: ("ausente", 1)}


def test_vaga_cujo_link_nunca_foi_listado_nao_e_comparada():
    """Anúncio lido da própria listagem (JSON-LD, CKAN) não vira ausente toda semana."""

    lida_da_listagem = {"_id": "lista", "url": LISTAGEM, "status": "descoberto"}
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2),
        anuncios_por_alvo={"empresa": [*anuncios(1, 2)["empresa"], lida_da_listagem]},
        presencas={},
        dia=DIA,
    )
    assert resultado.decisoes == []
    assert resultado.fontes[0]["vagas_ativas"] == 2


def test_listagem_inalterada_nao_gera_ausencia():
    resultado = avaliar(
        coberturas=cobertura(listagens_inalteradas=1),
        vistos=vistos(1, 2, 3),
        anuncios_por_alvo=anuncios(1, 2, 3, 4),
        presencas={},
        dia=DIA,
    )
    assert resultado.decisoes == []
    assert resultado.fontes[0]["motivo"] == "listagem_inalterada"


def test_encerrada_que_segue_na_listagem_e_reaberta():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2),
        anuncios_por_alvo={
            "empresa": [
                *anuncios(1)["empresa"],
                *anuncios(2, 3, status="encerrado")["empresa"],
            ]
        },
        presencas={},
        dia=DIA,
    )
    assert acoes(resultado) == {2: ("reaberta", 0)}


def test_link_visto_depois_da_meia_noite_conta_como_visto():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos={"empresa": {url(1): DIA.isoformat(), url(2): "2026-10-09"}},
        anuncios_por_alvo=anuncios(1, 2),
        presencas={},
        dia=DIA,
    )
    assert resultado.decisoes == []


def test_verificacao_troca_suspeita_pela_conclusao():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2, 6),
        anuncios_por_alvo=anuncios(1, 2, 3, 4, 5, 6),
        presencas={n: {"ausencias_seguidas": 1} for n in (3, 4, 5)},
        dia=DIA,
    )
    respostas = {
        url(3): (404, url(3), ""),
        url(4): (200, url(4), "Cargo 4. Envie seu currículo."),
        url(5): (429, url(5), ""),
    }
    abertas = verificar_suspeitas(resultado.decisoes, buscar=respostas.__getitem__, maximo=10)

    assert abertas == 3
    assert acoes(resultado) == {3: ("encerrada", 2), 4: ("ativa", 0), 5: ("indeterminada", 2)}


def test_verificacao_respeita_maximo():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2),
        anuncios_por_alvo=anuncios(1, 2, 3),
        presencas={3: {"ausencias_seguidas": 5}},
        dia=DIA,
    )
    assert verificar_suspeitas(resultado.decisoes, buscar=None, maximo=0) == 0
    assert acoes(resultado) == {3: ("suspeita", 6)}


def test_conferidas_ha_mais_tempo_vao_primeiro():
    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2, 3),
        anuncios_por_alvo=anuncios(1, 2, 3, 4, 5),
        presencas={
            4: {"ausencias_seguidas": 3, "verificado_em": "2026-10-01T00:00:00"},
            5: {"ausencias_seguidas": 3},
        },
        dia=DIA,
    )
    abertas = []
    verificar_suspeitas(
        resultado.decisoes,
        buscar=lambda u: abertas.append(u) or (200, u, "Vaga aberta"),
        maximo=1,
    )
    assert abertas == [url(5)]


class ColecaoGravacao:
    def __init__(self):
        self.operacoes = []

    def bulk_write(self, operacoes, ordered):
        self.operacoes.extend(operacoes)


def test_so_a_pagina_confirmada_muda_o_status():
    from scripts.conferir_vagas_removidas import DecisaoAnuncio, gravar

    def decisao(numero, acao, status="descoberto"):
        return DecisaoAnuncio(numero, "empresa", url(numero), status, acao, 1)

    banco = {"anuncios": ColecaoGravacao(), "presenca_anuncios": ColecaoGravacao()}
    alterados = gravar(
        banco,
        [
            decisao(1, "ausente"),
            decisao(2, "suspeita"),
            decisao(3, "indeterminada"),
            decisao(4, "ativa"),
            decisao(5, "encerrada"),
            decisao(6, "reaberta", "encerrado"),
            decisao(7, "vista", "ausente_aguardando_confirmacao"),
        ],
        DIA,
    )

    mudancas = {op._filter["_id"]: op._doc["$set"]["status"] for op in banco["anuncios"].operacoes}
    assert mudancas == {5: "encerrado", 6: "reaberto", 7: "ativo"}
    assert alterados == 3
    assert len(banco["presenca_anuncios"].operacoes) == 7


def test_toda_vaga_publicada_e_conferida_e_vem_antes_das_outras():
    from scripts.conferir_vagas_removidas import incluir_publicadas

    resultado = avaliar(
        coberturas=cobertura(),
        vistos=vistos(1, 2, 3),
        anuncios_por_alvo=anuncios(1, 2, 3, 4),
        presencas={4: {"ausencias_seguidas": 1}},  # 4 vira suspeita (2ª ausência)
        dia=DIA,
    )
    publicadas = [
        {"_id": 2, "url": url(2), "status": "descoberto", "alvo_id": "empresa"},  # foi vista
        {
            "_id": 50,
            "url": url(50),
            "status": "descoberto",
            "alvo_id": "outra",
        },  # fonte não comparável
        {"_id": 51, "url": url(51), "status": "encerrado", "alvo_id": "outra"},  # já encerrada
    ]

    assert incluir_publicadas(resultado, publicadas) == 2
    abertas = []
    verificar_suspeitas(
        resultado.decisoes,
        buscar=lambda u: abertas.append(u) or (200, u, "Vaga aberta"),
        maximo=2,
    )

    assert sorted(abertas) == sorted([url(2), url(50)])  # publicadas antes da suspeita 4
    assert acoes(resultado)[4] == ("suspeita", 2)


def test_conferencia_so_olha_anuncios_que_ainda_podem_ser_publicados():
    """Últimos 30 dias (mesmo prazo da publicação) ou sem data; --todas-as-idades desliga."""

    from datetime import datetime

    from scripts.conferir_vagas_removidas import filtro_de_idade

    assert filtro_de_idade(DIA) == {
        "$or": [
            {"publicado_em": {"$gte": datetime(2026, 9, 8)}},
            {"publicado_em": None},
        ]
    }
    assert filtro_de_idade(DIA, todas=True) == {}


def test_texto_sem_charset_preserva_acentos():
    from scripts.conferir_vagas_removidas import _texto_visivel

    html = "<html><body><nav>x</nav><p>Esta vaga não está mais disponível</p></body></html>"
    assert "disponível" in _texto_visivel(html.encode("utf-8"), None)
    assert "disponível" in _texto_visivel(html.encode("cp1252"), None)


def test_texto_ignora_menu_e_barra_lateral():
    from scripts.conferir_vagas_removidas import _texto_visivel

    html = (
        "<html><body><header>Topo</header><nav>Vagas</nav><main>Analista de Dados</main>"
        "<aside>Esta vaga foi encerrada</aside><footer>Rodapé</footer></body></html>"
    )
    assert _texto_visivel(html.encode("utf-8"), "utf-8") == "Analista de Dados"


def test_carregar_arquivos(tmp_path):
    (tmp_path / "coleta_20261008T010000Z.json").write_text(
        json.dumps({"fontes": [{"alvo_id": "a", "n": 1}]}), encoding="utf-8"
    )
    (tmp_path / "coleta_20261008T020000Z.json").write_text(
        json.dumps({"encerramento": "finished", "fontes": [{"alvo_id": "a", "n": 2}]}),
        encoding="utf-8",
    )
    fonte = carregar_coberturas(sorted(tmp_path.glob("coleta_*.json")))["a"]
    assert fonte["n"] == 2
    assert fonte["encerramento_coleta"] == "finished"

    (tmp_path / "fila_1.json").write_text(
        json.dumps({"vistos": {"a": {"u": "2026-10-07"}}}), encoding="utf-8"
    )
    (tmp_path / "fila_2.json").write_text(
        json.dumps({"vistos": {"a": {"u": "2026-10-08"}}}), encoding="utf-8"
    )
    assert carregar_vistos(tmp_path) == {"a": {"u": "2026-10-08"}}
