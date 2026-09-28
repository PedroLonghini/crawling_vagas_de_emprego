import csv
from pathlib import Path


FILA = Path("config/fila_prospeccao_fontes.csv")


def test_fila_tem_evidencia_e_nenhuma_fonte_pendente_e_operacional() -> None:
    with FILA.open(encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))

    assert linhas
    assert {linha["resultado_politica"] for linha in linhas} <= {
        "pending",
        "restritiva",
        "aprovada_para_teste",
    }
    assert all(linha["url_vagas"].startswith("https://") for linha in linhas)
    assert all(linha["url_termos_ou_licenca"].startswith("https://") for linha in linhas)
