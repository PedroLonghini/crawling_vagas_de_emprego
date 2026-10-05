"""Testes do gerador do payload da API do Empregos."""

import json

import pytest

from observatorio_vagas.domain.prontidao import (
    CAMPOS_API_EMPREGOS,
    CampoProntidao,
    ProblemaProntidao,
    RelatorioProntidao,
    SeveridadeProntidao,
    SituacaoCampoProntidao,
)
from observatorio_vagas.integrations.empregos import (
    PayloadEmpregosInvalido,
    gerar_json_empregos,
    gerar_payload_empregos,
)

# Valores fictícios usados pelos testes.
#
# O dicionário possui os 24 caminhos conhecidos pelo projeto.
VALORES_COMPLETOS: dict[str, object] = {
    "company.applyUrl": ("https://candidaturas.example.com/vaga-001"),
    "company.name": "Tecnologia Atlas",
    "company.logoUrl": ("https://tecnologia-atlas.example.com/logo.png"),
    "company.description": ("Empresa brasileira de tecnologia."),
    "company.industries": "Tecnologia",
    "company.companyId": "EMPREGOS-EMPRESA-001",
    "company.recruiterId": "RECRUTADOR-001",
    "company.recruiterName": "Maria da Silva",
    "company.recruiterEmail": "maria@example.com",
    "company.nationalRegister": "12.345.678/0001-90",
    "externalJobPostingId": "VAGA-001",
    "jobPostingOperationType": "CREATE",
    "title": "Pessoa Desenvolvedora Python",
    "description": ("Descrição completa da oportunidade com mais de cem caracteres."),
    "location.address": "São Paulo, SP",
    "location.postalCode": "01045-000",
    "location.geolocation": "-23.5440722,-46.6450811",
    "salary.min": 6500,
    "salary.max": 8500,
    "workplaceTypes": "Hybrid",
    "employmentStatus": "FULL_TIME",
    "experienceLevel": "MID_SENIOR_LEVEL",
    "trackingPixelUrl": ("https://tracking.example.com/pixel/vaga-001"),
    "expireAt": "2026-09-30T23:59:59+00:00",
}


def criar_relatorio(
    *,
    valores: dict[str, object] | None = None,
    problemas: tuple[ProblemaProntidao, ...] = (),
    remover_campos: tuple[str, ...] = (),
) -> RelatorioProntidao:
    """Monta um relatório completo para os testes do payload."""

    # Copiamos o dicionário para que um teste não altere outro.
    valores_finais = dict(VALORES_COMPLETOS)

    if valores is not None:
        valores_finais.update(valores)

    campos: list[CampoProntidao] = []

    for nome, obrigatorio in CAMPOS_API_EMPREGOS:
        if nome in remover_campos:
            continue

        valor = valores_finais.get(nome)

        # O teste considera valores existentes como extraídos
        # e valores None como ausentes.
        situacao = (
            SituacaoCampoProntidao.EXTRAIDO if valor is not None else SituacaoCampoProntidao.AUSENTE
        )

        campos.append(
            CampoProntidao(
                campo=nome,
                valor=valor,
                obrigatorio=obrigatorio,
                situacao=situacao,
            )
        )

    return RelatorioProntidao(
        campos=tuple(campos),
        problemas=problemas,
    )


def test_gera_payload_com_objetos_aninhados() -> None:
    """Os caminhos devem virar company, location e salary."""

    payload = gerar_payload_empregos(criar_relatorio())

    assert payload["company"] == {
        "applyUrl": ("https://candidaturas.example.com/vaga-001"),
        "name": "Tecnologia Atlas",
        "logoUrl": ("https://tecnologia-atlas.example.com/logo.png"),
        "description": "Empresa brasileira de tecnologia.",
        "industries": "Tecnologia",
        "companyId": "EMPREGOS-EMPRESA-001",
        "recruiterId": "RECRUTADOR-001",
        "recruiterName": "Maria da Silva",
        "recruiterEmail": "maria@example.com",
        "nationalRegister": "00.000.000/0000-00",
    }

    assert payload["location"] == {
        "address": "São Paulo, SP",
        "postalCode": "01045-000",
        "geolocation": "-23.5440722,-46.6450811",
    }

    assert payload["salary"] == {
        "min": 6500,
        "max": 8500,
    }

    assert payload["externalJobPostingId"] == "VAGA-001"
    assert payload["jobPostingOperationType"] == "CREATE"
    assert payload["workplaceTypes"] == "Hybrid"


def test_omite_campos_opcionais_ausentes() -> None:
    """Um opcional ausente não deve aparecer como null."""

    relatorio = criar_relatorio(
        valores={
            "company.logoUrl": None,
            "company.description": None,
            "company.companyId": None,
            "company.recruiterId": None,
            "company.recruiterName": None,
            "company.recruiterEmail": None,
            "company.nationalRegister": None,
            "salary.min": None,
            "salary.max": None,
            "trackingPixelUrl": None,
            "expireAt": None,
        }
    )

    payload = gerar_payload_empregos(relatorio)

    assert "logoUrl" not in payload["company"]
    assert "description" not in payload["company"]
    assert "companyId" not in payload["company"]
    assert "recruiterId" not in payload["company"]
    assert payload["company"]["nationalRegister"] == "00.000.000/0000-00"

    # Como min e max estão ausentes, o objeto salary
    # inteiro não precisa ser incluído.
    assert "salary" not in payload

    assert "trackingPixelUrl" not in payload
    assert "expireAt" not in payload


def test_recusa_campo_obrigatorio_ausente() -> None:
    """O payload não pode nascer sem um obrigatório."""

    relatorio = criar_relatorio(
        valores={
            "company.applyUrl": None,
        }
    )

    with pytest.raises(PayloadEmpregosInvalido) as captura:
        gerar_payload_empregos(relatorio)

    assert captura.value.campos == ("company.applyUrl",)

    assert "ainda não está pronta" in str(captura.value)


def test_recusa_relatorio_com_erro() -> None:
    """Um erro deve bloquear até um relatório preenchido."""

    problema = ProblemaProntidao(
        campo="company.recruiter",
        mensagem="O recrutador está inativo.",
        severidade=SeveridadeProntidao.ERRO,
    )

    relatorio = criar_relatorio(problemas=(problema,))

    with pytest.raises(PayloadEmpregosInvalido) as captura:
        gerar_payload_empregos(relatorio)

    assert captura.value.campos == ("company.recruiter",)


def test_recusa_relatorio_sem_os_24_campos() -> None:
    """Um contrato incompleto deve falhar antes do JSON."""

    relatorio = criar_relatorio(remover_campos=("trackingPixelUrl",))

    with pytest.raises(PayloadEmpregosInvalido) as captura:
        gerar_payload_empregos(relatorio)

    assert captura.value.campos == ("trackingPixelUrl",)

    assert "não contém todos" in str(captura.value)


def test_gera_json_legivel_sem_escapar_acentos() -> None:
    """O JSON deve preservar caracteres em português."""

    texto_json = gerar_json_empregos(
        criar_relatorio(),
        indentacao=2,
    )

    # Lemos novamente o texto para provar que é um JSON válido.
    payload_lido = json.loads(texto_json)

    assert '"Pessoa Desenvolvedora Python"' in texto_json
    assert "Descrição" in texto_json
    assert "\\u00e7" not in texto_json

    assert payload_lido["company"]["name"] == "Tecnologia Atlas"


def test_todo_payload_leva_o_cnpj_zerado_mesmo_com_cnpj_real() -> None:
    payload = gerar_payload_empregos(criar_relatorio())

    assert payload["company"]["nationalRegister"] == "00.000.000/0000-00"


def test_confidential_sai_com_c_minusculo() -> None:
    from observatorio_vagas.integrations.empregos.payload import _ajustar_para_a_api

    for escrita in ("Confidential", "CONFIDENTIAL", " confidential ", "Confidencial"):
        payload = {"company": {"name": escrita}}

        _ajustar_para_a_api(payload)

        assert payload["company"]["name"] == "confidential"

    outro = {"company": {"name": "Confidential Pharma"}}
    _ajustar_para_a_api(outro)
    assert outro["company"]["name"] == "Confidential Pharma"
