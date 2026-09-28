"""Gera um payload fictício para conferir a caixa de saída do Empregos.

Não acessa o MongoDB nem a API. Os dados deste arquivo são apenas de teste.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from observatorio_vagas.domain.prontidao import (
    CAMPOS_API_EMPREGOS,
    CampoProntidao,
    RelatorioProntidao,
    SituacaoCampoProntidao,
)
from observatorio_vagas.integrations.empregos import gerar_payload_empregos

VALORES_DE_TESTE: dict[str, object] = {
    "company.applyUrl": "https://carreiras.empresa-exemplo.com.br/vaga/001",
    "company.name": "Empresa Exemplo Tecnologia Ltda.",
    "company.logoUrl": "https://carreiras.empresa-exemplo.com.br/logo.png",
    "company.description": "Empresa fictícia usada somente para testar a saída local.",
    "company.industries": "Tecnologia",
    "company.companyId": "EMPRESA-TESTE-001",
    "company.recruiterId": "RECRUTADOR-TESTE-001",
    "company.recruiterName": "Equipe de Recrutamento",
    "company.recruiterEmail": "recrutamento@empresa-exemplo.com.br",
    "company.nationalRegister": "12345678000190",
    "externalJobPostingId": "teste:empresa-exemplo:001",
    "jobPostingOperationType": "CREATE",
    "title": "Pessoa Desenvolvedora Python — TESTE",
    "description": (
        "Vaga fictícia para validar a exportação do payload. Não representa uma "
        "oportunidade real e nunca deve ser enviada para a API."
    ),
    "location.address": "São Paulo, SP",
    "location.postalCode": "01000-000",
    "location.geolocation": "-23.5505,-46.6333",
    "salary.min": 6500,
    "salary.max": 8500,
    "workplaceTypes": "Hybrid",
    "employmentStatus": "FULL_TIME",
    "experienceLevel": "MID_SENIOR_LEVEL",
    "trackingPixelUrl": "https://empresa-exemplo.com.br/pixel/teste-001",
    "expireAt": "2026-12-31T23:59:59+00:00",
}

# Os sete obrigatórios e dois opcionais frequentes. Representa uma fonte que
# ainda não conseguiu fornecer os outros quinze valores da API.
CAMPOS_NOVE_DE_TESTE = frozenset(
    {
        "company.applyUrl",
        "company.name",
        "company.description",
        "company.nationalRegister",
        "externalJobPostingId",
        "title",
        "description",
        "location.address",
        "employmentStatus",
    }
)


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera um payload fictício local.")
    parser.add_argument(
        "--diretorio-payloads",
        type=Path,
        default=Path("outputs/payloads_teste_empregos"),
        help="pasta onde o lote fictício será criado",
    )
    parser.add_argument(
        "--nove-campos",
        action="store_true",
        help="gera somente 9 dos 24 campos, incluindo todos os obrigatórios",
    )
    return parser


def _criar_relatorio(*, nove_campos: bool) -> RelatorioProntidao:
    return RelatorioProntidao(
        campos=tuple(
            CampoProntidao(
                campo=campo,
                valor=(
                    VALORES_DE_TESTE[campo]
                    if not nove_campos or campo in CAMPOS_NOVE_DE_TESTE
                    else None
                ),
                obrigatorio=obrigatorio,
                situacao=(
                    SituacaoCampoProntidao.GERADO
                    if not nove_campos or campo in CAMPOS_NOVE_DE_TESTE
                    else SituacaoCampoProntidao.AUSENTE
                ),
            )
            for campo, obrigatorio in CAMPOS_API_EMPREGOS
        )
    )


def executar(argumentos: list[str] | None = None) -> int:
    opcoes = criar_parser().parse_args(argumentos)
    gerado_em = datetime.now(UTC)
    pasta_lote = opcoes.diretorio_payloads / f"lote-teste-{gerado_em:%Y%m%dT%H%M%SZ}"
    pasta_payloads = pasta_lote / "payloads"
    pasta_payloads.mkdir(parents=True, exist_ok=False)

    payload = gerar_payload_empregos(_criar_relatorio(nove_campos=opcoes.nove_campos))
    nome_arquivo = "teste_empresa_exemplo_001.json"
    (pasta_payloads / nome_arquivo).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifesto = {
        "versao_schema": 1,
        "gerado_em": gerado_em.isoformat(),
        "modo": "SIMULACAO_LOCAL_SEM_MONGODB_SEM_API",
        "aviso": "Payload completamente fictício; não enviar à API.",
        "resumo": {
            "campos_avaliados": len(CAMPOS_API_EMPREGOS),
            "campos_preenchidos": len(CAMPOS_NOVE_DE_TESTE)
            if opcoes.nove_campos
            else len(CAMPOS_API_EMPREGOS),
            "payloads_exportados": 1,
        },
        "itens": [{"titulo": payload["title"], "arquivo": f"payloads/{nome_arquivo}"}],
    }
    (pasta_lote / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Payload fictício criado em: {pasta_lote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
