"""Testes da separação local de payloads por domínio de origem."""

from scripts.separar_payloads_nao_governamentais import (
    filtrar_payloads_governamentais,
    filtrar_payloads_nao_governamentais,
)


def test_remove_origens_governamentais_e_preserva_as_demais() -> None:
    """A separação deve manter somente os payloads sem domínio .gov."""

    payloads = [
        {"company": {"applyUrl": "https://dados.es.gov.br/vagas.csv"}, "title": "Pública"},
        {
            "company": {"applyUrl": "https://dadosabertos.iftm.edu.br/vagas.csv"},
            "title": "IFTM",
        },
        {
            "company": {"applyUrl": "https://data.queridodiario.ok.org.br/vagas.pdf"},
            "title": "Querido Diário",
        },
        {
            "company": {"applyUrl": "https://data.queridodiario.ok.org.br/vagas.pdf"},
            "title": "Processo seletivo público",
        },
    ]

    resultado = filtrar_payloads_nao_governamentais(payloads)

    assert [payload["title"] for payload in resultado] == ["IFTM", "Querido Diário"]


def test_mantem_apenas_origens_governamentais() -> None:
    """A opção governamental deve ser o complemento da seleção sem .gov."""

    payloads = [
        {"company": {"applyUrl": "https://dados.es.gov.br/vagas.csv"}, "title": "Pública"},
        {"company": {"applyUrl": "https://dadosabertos.iftm.edu.br/vagas.csv"}, "title": "IFTM"},
    ]

    resultado = filtrar_payloads_governamentais(payloads)

    assert [payload["title"] for payload in resultado] == ["Pública"]
