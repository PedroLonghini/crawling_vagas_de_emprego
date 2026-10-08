"""Varredura semanal: só as partes sem MongoDB, rede ou subprocessos."""

import os
from datetime import datetime

from scripts.rotina_semanal import Passo, Varredura, coberturas_desde


def test_usa_so_os_relatorios_gravados_por_esta_coleta(tmp_path):
    antigo = tmp_path / "coleta_20261001T000000Z.json"
    novo = tmp_path / "coleta_20261008T120000Z.json"
    for caminho in (antigo, novo):
        caminho.write_text("{}", encoding="utf-8")
    inicio = datetime(2026, 10, 8, 9, 0)
    os.utime(antigo, (inicio.timestamp() - 3600,) * 2)
    os.utime(novo, (inicio.timestamp() + 60,) * 2)

    assert coberturas_desde(tmp_path, inicio) == [novo]


def test_varredura_so_esta_ok_se_todos_os_passos_deram_certo():
    varredura = Varredura(dia="2026-10-08", inicio=datetime(2026, 10, 8, 2, 0))
    varredura.passos.append(Passo("coleta", 0, ""))
    assert varredura.ok
    varredura.passos.append(Passo("conferência", 1, "MongoDB caiu"))
    assert not varredura.ok
