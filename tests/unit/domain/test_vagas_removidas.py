"""Regras que decidem se uma vaga saiu do site de origem."""

import pytest

from observatorio_vagas.domain.vagas_removidas import (
    SituacaoDetalhe,
    avaliar_coleta_da_fonte,
    classificar_detalhe,
    proporcao_vista_suficiente,
)

URL = "https://empresa.com.br/vagas/analista-de-dados/123"


def fonte_completa(**mudancas):
    fonte = {
        "alvo_id": "empresa",
        "janela_horas": None,
        "janela_encerrada": False,
        "limite_anuncios": None,
        "motivos_fim_navegacao": ["sem_links_adicionais"],
        "listagens_nao_visitadas": 0,
        "listagens_sem_resposta": 0,
        "listagens_descartadas": 0,
        "listagens_inalteradas": 0,
        "encerramento_coleta": "finished",
        "resultados": {
            "https://empresa.com.br/vagas": {"tipo_pagina": "inicial", "status_http": 200},
            "https://empresa.com.br/vagas?page=9": {"tipo_pagina": "inicial", "status_http": 404},
            URL: {"tipo_pagina": "detalhe_vaga", "status_http": 503},
        },
    }
    fonte.update(mudancas)
    return fonte


@pytest.mark.parametrize("status", [404, 410])
def test_pagina_inexistente_encerra(status):
    situacao, motivo = classificar_detalhe(
        status_http=status, url_pedida=URL, url_final=URL, texto=""
    )
    assert situacao is SituacaoDetalhe.ENCERRADA
    assert motivo == f"http_{status}"


@pytest.mark.parametrize("status", [None, 403, 429, 500, 503])
def test_bloqueio_ou_falha_nao_decide(status):
    situacao, _ = classificar_detalhe(status_http=status, url_pedida=URL, url_final=URL, texto="")
    assert situacao is SituacaoDetalhe.INDETERMINADA


@pytest.mark.parametrize(
    "url_final",
    ["https://empresa.com.br/", "https://www.empresa.com.br/vagas", "https://empresa.com.br"],
)
def test_redirecionar_para_listagem_ou_home_encerra(url_final):
    situacao, motivo = classificar_detalhe(
        status_http=200, url_pedida=URL, url_final=url_final, texto="Vagas abertas"
    )
    assert situacao is SituacaoDetalhe.ENCERRADA
    assert motivo == "redirecionou_para_listagem"


def test_redirecionar_para_outro_dominio_nao_encerra():
    situacao, _ = classificar_detalhe(
        status_http=200,
        url_pedida=URL,
        url_final="https://ats.exemplo.com/login",
        texto="Entre na sua conta",
    )
    assert situacao is SituacaoDetalhe.ATIVA


@pytest.mark.parametrize(
    "texto",
    [
        "Analista de Dados — Esta vaga foi encerrada. Veja outras oportunidades.",
        "Esta vaga não está mais disponível",
        "Este processo seletivo foi encerrado",
        "AS INSCRIÇÕES FORAM ENCERRADAS",
        "This job is no longer available",
        "We are no longer accepting applications for this role",
    ],
)
def test_aviso_de_encerramento_encerra(texto):
    situacao, motivo = classificar_detalhe(
        status_http=200, url_pedida=URL, url_final=URL, texto=texto
    )
    assert situacao is SituacaoDetalhe.ENCERRADA
    assert motivo == "aviso_de_encerramento"


@pytest.mark.parametrize(
    "texto",
    [
        "Página não encontrada - Empresa X",
        "Ops! A página que você procura não existe ou foi removida.",
        "Erro 404 | Voltar para a home",
        "404 Not Found",
        "Vaga não encontrada. Veja outras oportunidades.",
        "Esta vaga não foi localizada",
        "Não foi possível encontrar a vaga solicitada",
        "Oops! This page doesn't exist.",
        "Job not found",
        "Página no encontrada",
    ],
)
def test_404_disfarcado_com_resposta_200_encerra(texto):
    """Site que responde 200 mas mostra uma página de erro no lugar da vaga."""

    situacao, motivo = classificar_detalhe(
        status_http=200, url_pedida=URL, url_final=URL, texto=texto
    )
    assert situacao is SituacaoDetalhe.ENCERRADA
    assert motivo == "pagina_nao_encontrada"


@pytest.mark.parametrize(
    "texto",
    [
        "Analista de Dados. Não encontrou a vaga ideal? Cadastre seu currículo.",
        "Analista de Dados. Requisitos: 404 horas de experiência em SQL.",
        "Analista de Dados. Requisitos: SQL. Candidate-se. Menu: vagas encerradas",
        "Analista de Dados. Outras vagas: Auxiliar — Vaga encerrada; Técnico — aberta",
        "Analista de Dados. Inscrições encerradas em 30/11. Candidate-se já.",
        "Processo seletivo encerrado (veja os resultados anteriores)",
    ],
)
def test_rotulo_solto_ou_prazo_futuro_nao_encerra(texto):
    situacao, _ = classificar_detalhe(status_http=200, url_pedida=URL, url_final=URL, texto=texto)
    assert situacao is SituacaoDetalhe.ATIVA


@pytest.mark.parametrize(
    "url_final",
    [
        "https://empresa.com.br/vagas/analista-de-dados",  # slug sem o id
        "https://empresa.com.br/vagas/analista-de-dados/123/",  # barra final
        "https://empresa.com.br/vagas/123",  # outro caminho, com número
    ],
)
def test_redirecionamento_canonico_nao_encerra(url_final):
    situacao, _ = classificar_detalhe(
        status_http=200, url_pedida=URL, url_final=url_final, texto="Analista de Dados"
    )
    assert situacao is SituacaoDetalhe.ATIVA


def test_coleta_completa_aceita_404_no_fim_da_paginacao():
    assert avaliar_coleta_da_fonte(fonte_completa()) == (True, "completa")


@pytest.mark.parametrize(
    ("mudancas", "motivo"),
    [
        ({"janela_horas": 24}, "coleta_com_janela_de_horas"),
        ({"janela_encerrada": True}, "fonte_encerrada_antes_do_fim"),
        ({"motivos_fim_navegacao": ["limite_atingido"]}, "limite_de_paginas_atingido"),
        ({"listagens_nao_visitadas": 3}, "listagens_nao_visitadas"),
        ({"encerramento_coleta": "closespider_timeout"}, "coleta_interrompida"),
        ({"encerramento_coleta": None}, "coleta_interrompida"),
        ({"listagens_inalteradas": 1}, "listagem_inalterada"),
        ({"listagens_sem_resposta": 2}, "listagem_sem_resposta_ou_descartada"),
        ({"listagens_descartadas": 1}, "listagem_sem_resposta_ou_descartada"),
        (
            {"resultados": {"u": {"tipo_pagina": "inicial", "status_http": 400}}},
            "erro_http_400_na_listagem",
        ),
        ({"resultados": {}}, "sem_listagem"),
        (
            {"resultados": {"u": {"tipo_pagina": "inicial", "status_http": 429}}},
            "erro_http_429_na_listagem",
        ),
        (
            {"resultados": {"u": {"tipo_pagina": "inicial", "status_http": None, "erro": "x"}}},
            "falha_de_rede_na_listagem",
        ),
        (
            {"resultados": {"u": {"tipo_pagina": "inicial", "status_http": 404}}},
            "nenhuma_listagem_respondeu",
        ),
    ],
)
def test_coleta_incompleta_nao_conta_ausencia(mudancas, motivo):
    assert avaliar_coleta_da_fonte(fonte_completa(**mudancas)) == (False, motivo)


def test_relatorio_antigo_sem_campos_nao_e_completo():
    fonte = fonte_completa()
    del fonte["listagens_sem_resposta"]
    assert avaliar_coleta_da_fonte(fonte) == (False, "relatorio_sem_dados_de_completude")


@pytest.mark.parametrize(
    ("vistas", "ativas", "esperado"),
    [(0, 0, True), (0, 2, False), (1, 3, True), (5, 10, True), (4, 10, False)],
)
def test_proporcao_vista(vistas, ativas, esperado):
    assert proporcao_vista_suficiente(vistas=vistas, ativas=ativas) is esperado
