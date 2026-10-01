"""Liga a leitura completa ao fluxo: extração (grava) e preparação (aplica).

Na extração, cada anúncio recebe ``campos_estruturados["_leitura"]`` com os
campos da API e o diagnóstico. Na preparação do payload, esses valores entram
nos modelos antes da prontidão, para que as validações da API continuem
valendo sobre os valores novos.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.domain.vaga import SalarioNormalizado, VagaCanonica
from observatorio_vagas.extraction.leitura.camadas import InventarioPagina
from observatorio_vagas.extraction.leitura.interpretacao import ler_vaga

CHAVE = "_leitura"

_MODALIDADE = {
    "Remote": ModalidadeTrabalho.REMOTO,
    "Hybrid": ModalidadeTrabalho.HIBRIDO,
    "On-site": ModalidadeTrabalho.PRESENCIAL,
}
_REGIME = {
    "FULL_TIME": RegimeContratacao.CLT,
    "CONTRACT": RegimeContratacao.PJ,
    "INTERNSHIP": RegimeContratacao.ESTAGIO,
    "TEMPORARY": RegimeContratacao.TEMPORARIO,
}
_NIVEL = {
    "INTERNSHIP": Senioridade.ESTAGIO,
    "ENTRY_LEVEL": Senioridade.JUNIOR,
    "MID_SENIOR_LEVEL": Senioridade.PLENO,
    "DIRECTOR": Senioridade.LIDERANCA,
}
_PERIODO = {
    "mensal": PeriodoSalario.MES,
    "month": PeriodoSalario.MES,
    "hora": PeriodoSalario.HORA,
    "hour": PeriodoSalario.HORA,
    "anual": PeriodoSalario.ANO,
    "year": PeriodoSalario.ANO,
}


def ler_anuncio(
    anuncio: AnuncioVaga,
    *,
    documento: dict[str, Any],
    inventario: InventarioPagina | None,
    url_pagina: str,
    url_candidatura_html: str | None,
    varias_vagas_na_pagina: bool = False,
) -> dict[str, Any]:
    """Resultado da leitura em JSON simples, pronto para o MongoDB."""

    atributos = documento.get("_observatorio_atributos")
    url_vaga = url_pagina if inventario is not None else str(anuncio.url)

    try:
        leitura = ler_vaga(
            inventario or InventarioPagina(url=url_vaga),
            titulo=anuncio.titulo_original,
            id_externo=anuncio.id_externo,
            url_vaga=url_vaga,
            documento=documento,
            atributos_plataforma=atributos if isinstance(atributos, dict) else None,
            url_candidatura_html=url_candidatura_html,
            varias_vagas_na_pagina=varias_vagas_na_pagina,
            # Datas sem ano ("até 10/01") são lidas em relação à coleta.
            hoje=anuncio.ultima_observacao_em.date(),
        )
    except Exception as erro:  # a leitura nova nunca derruba a extração
        return {"erro": f"{type(erro).__name__}: {erro}"}

    # Período do salário fica fora dos campos da API, mas vale guardar.
    extras = leitura.extras

    return json.loads(
        json.dumps(
            {"campos": leitura.campos, "diagnostico": leitura.diagnostico, "extras": extras},
            default=str,
        )
    )


def aplicar_leitura(
    *,
    empresa: Empresa,
    anuncio: AnuncioVaga,
    vaga: VagaCanonica,
) -> tuple[Empresa, AnuncioVaga, VagaCanonica, dict[str, Any] | None]:
    """Coloca nos modelos os valores lidos; devolve também o diagnóstico.

    Campos que a leitura deixou vazios de propósito (data calculada, salário
    0, modalidade/vínculo/nível sem evidência) são limpos. Descrição,
    endereço e dados da empresa só são trocados quando a leitura tem valor.
    """

    leitura = (anuncio.campos_estruturados or {}).get(CHAVE)
    if not isinstance(leitura, dict) or "campos" not in leitura:
        return empresa, anuncio, vaga, None

    campos = leitura["campos"]
    diagnostico = leitura.get("diagnostico") or {}
    empresa_lida = campos.get("company") or {}
    local = campos.get("location") or {}

    atualizacao_anuncio: dict[str, Any] = {
        "regime_original": campos.get("employmentStatus"),
    }
    if empresa_lida.get("applyUrl"):
        atualizacao_anuncio["url_candidatura"] = empresa_lida["applyUrl"]

    expira = campos.get("expireAt")
    if expira is None:
        atualizacao_anuncio["expira_em"] = None
    else:
        anterior = anuncio.expira_em
        if anterior is None or str(anterior)[:10] != expira[:10]:
            atualizacao_anuncio["expira_em"] = date.fromisoformat(expira[:10])

    salario = None
    if campos.get("salary"):
        periodo = (leitura.get("extras") or {}).get("salary_periodo")
        salario = SalarioNormalizado(
            natureza=NaturezaSalario.PUBLICADO,
            periodo=_PERIODO.get(str(periodo).casefold(), PeriodoSalario.NAO_INFORMADO),
            minimo=Decimal(campos["salary"]["min"]),
            maximo=Decimal(campos["salary"]["max"]) if campos["salary"].get("max") else None,
            regra_normalizacao="leitura_completa",
        )

    atualizacao_vaga: dict[str, Any] = {
        "salario": salario,
        "modalidade": _MODALIDADE.get(
            campos.get("workplaceTypes"), ModalidadeTrabalho.NAO_INFORMADO
        ),
        "regime": _REGIME.get(campos.get("employmentStatus"), RegimeContratacao.NAO_INFORMADO),
        "senioridade": _NIVEL.get(campos.get("experienceLevel"), Senioridade.NAO_INFORMADA),
    }
    if campos.get("description"):
        atualizacao_vaga["descricao_normalizada"] = campos["description"]
    if local.get("address"):
        atualizacao_vaga["endereco"] = local["address"]
    if local.get("postalCode"):
        atualizacao_vaga["cep"] = local["postalCode"]

    atualizacao_empresa: dict[str, Any] = {}
    for campo_api, campo_modelo in (
        ("name", "nome_fantasia"),
        ("logoUrl", "logo_url"),
        ("description", "descricao"),
        ("industries", "setor"),
        ("nationalRegister", "cnpj"),
    ):
        if empresa_lida.get(campo_api):
            atualizacao_empresa[campo_modelo] = empresa_lida[campo_api]

    # Sem o nome que a vaga exibe, a vaga não é válida: não herdamos o nome da
    # conta/consultoria. Nome vazio deixa o campo obrigatório ausente, e a
    # prontidão bloqueia o payload.
    if not empresa_lida.get("name"):
        atualizacao_empresa["nome_fantasia"] = None
        atualizacao_empresa["razao_social"] = ""

    motivo_logo = (diagnostico.get("campos", {}).get("company.logoUrl") or {}).get("motivo") or ""
    if "não da contratante" in motivo_logo:
        atualizacao_empresa["logo_url"] = None

    return (
        empresa.model_copy(update=atualizacao_empresa),
        anuncio.model_copy(update=atualizacao_anuncio),
        vaga.model_copy(update=atualizacao_vaga),
        diagnostico,
    )
