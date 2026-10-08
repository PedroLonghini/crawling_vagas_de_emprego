"""Detalhe CXS do Workday (resposta real da Cisco, reduzida)."""

import json
from pathlib import Path

from observatorio_vagas.extraction.workday import eh_detalhe_workday, extrair_vaga_workday

URL = (
    "https://cisco.wd5.myworkdayjobs.com/wday/cxs/cisco/Cisco_Careers/job/"
    "San-Jose-California-US/Hardware-Design-Engineer-Technical-Lead_2014522-1"
)
DADOS = json.loads((Path(__file__).parent / "dados_workday_cisco.json").read_text(encoding="utf-8"))


def test_reconhece_e_converte_detalhe_cxs():
    assert eh_detalhe_workday(URL, DADOS)
    assert not eh_detalhe_workday(
        "https://cisco.wd5.myworkdayjobs.com/en-US/x", {"widget": "redirect"}
    )

    vaga = extrair_vaga_workday(DADOS, url=URL)

    assert vaga["identifier"] == "workday-cisco-2014522"
    assert vaga["title"] == "Hardware Design Engineer Technical Lead"
    assert vaga["hiringOrganization"]["name"] == "Cisco Systems, Inc."
    assert "Meet the Team" in vaga["description"]
    assert vaga["jobLocation"]["address"]["addressLocality"] == "San Jose, California, US"
    assert vaga["validThrough"] == "2026-12-01"
    assert vaga["employmentType"] == "Full time"
    assert vaga["jobLocationType"] == "Onsite Only"
    assert "2014522" in vaga["_observatorio_apply_url"]


def test_json_publico_usa_extrator_workday_no_detalhe_cxs():
    from observatorio_vagas.extraction.json_publico import extrair_vagas_json_publico

    resultado = extrair_vagas_json_publico(json.dumps(DADOS).encode(), url=URL)

    assert [vaga["identifier"] for vaga in resultado.vagas] == ["workday-cisco-2014522"]


def test_data_sem_fuso_vira_so_a_data():
    from datetime import date

    from observatorio_vagas.extraction.mapeamento_json_ld import _converter_data

    assert _converter_data("2027-04-10T23:59") == date(2027, 4, 10)


def test_identificador_nunca_corta_o_codigo():
    from observatorio_vagas.extraction.workday import _identificador

    tenant = "averylongtenantnamethatgoesonandon"
    a, b = _identificador(tenant, "JR-2026-0001"), _identificador(tenant, "JR-2026-0002")

    assert a != b and len(a) <= 50 and a.endswith("JR-2026-0001")


def test_nome_mantem_numeros_legitimos():
    from observatorio_vagas.extraction.workday import _nome_empresa

    assert _nome_empresa("020 Cisco Systems, Inc.") == "Cisco Systems, Inc."
    assert _nome_empresa("99 Tecnologia") == "99 Tecnologia"
