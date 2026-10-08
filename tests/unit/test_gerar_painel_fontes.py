import json
from datetime import date

from scripts.gerar_painel_fontes import (
    carregar_ultima_cobertura_por_fonte,
    painel_markdown,
    resumir_painel,
)


def _salvar_cobertura(diretorio, nome, fontes):
    (diretorio / nome).write_text(
        json.dumps({"encerramento": "finished", "fontes": fontes}), encoding="utf-8"
    )


def test_seleciona_ultima_execucao_da_mesma_fonte_sem_somar_retentativa(tmp_path):
    _salvar_cobertura(
        tmp_path,
        "coleta_20260915T080000000000Z.json",
        [{"alvo_id": "fonte_a", "candidatos_unicos": 3, "detalhes_agendados": 3}],
    )
    _salvar_cobertura(
        tmp_path,
        "coleta_20260915T090000000000Z.json",
        [
            {"alvo_id": "fonte_a", "candidatos_unicos": 2, "detalhes_agendados": 2},
            {"alvo_id": "fonte_b", "candidatos_unicos": 5, "detalhes_agendados": 4},
        ],
    )

    fontes = carregar_ultima_cobertura_por_fonte(tmp_path, date(2026, 9, 15))

    assert fontes["fonte_a"]["candidatos_unicos"] == 2
    assert fontes["fonte_b"]["candidatos_unicos"] == 5


def test_painel_preserva_distincao_entre_candidato_e_anuncio_elegivel():
    painel = resumir_painel(
        {
            "fonte_a": {
                "arquivo_cobertura": "coleta_20260915T090000000000Z.json",
                "paginas_agendadas": 4,
                "paginas_recebidas": 3,
                "candidatos_unicos": 2,
                "detalhes_agendados": 2,
                "detalhes_http_ok": 1,
                "diagnostico": "detalhes_com_falha_de_acesso",
                "proxima_acao": "Validar o detalhe.",
                "erros": {"https://exemplo.test/erro": {}},
                "detalhes_sem_resposta": ["https://exemplo.test/pendente"],
            }
        },
        dia=date(2026, 9, 15),
        meta=1000,
    )

    assert painel["totais"]["candidatos_unicos"] == 2
    assert painel["totais"]["detalhes_http_ok"] == 1
    assert painel["totais"]["anuncios_elegiveis"] is None
    assert painel["fontes"][0]["erros_download"] == 1
    assert painel["totais"]["fontes_por_diagnostico"] == {
        "detalhes_com_falha_de_acesso": 1
    }
    assert "detalhes_com_falha_de_acesso" in painel_markdown(painel)
    assert "ainda não é calculável" in painel_markdown(painel)
