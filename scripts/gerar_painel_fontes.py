"""Gera um painel diário por fonte a partir dos relatórios de cobertura do crawler.

O painel usa somente fatos já observados na coleta.  Em particular,
``candidatos_unicos`` e ``detalhes_http_ok`` não são apresentados como vagas
extraídas ou elegíveis: a extração e a validação de republicação ocorrem em
etapas posteriores.
"""

import argparse
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
CHAVES_SOMAVEIS = (
    "paginas_agendadas",
    "paginas_recebidas",
    "listagens_agendadas",
    "candidatos_unicos",
    "detalhes_agendados",
    "detalhes_http_ok",
)


def _data_do_nome(caminho: Path) -> date | None:
    """Obtém a data UTC de ``coleta_YYYYMMDDTHHMMSSffffffZ.json``."""

    nome = caminho.stem
    if not nome.startswith("coleta_"):
        return None
    try:
        return datetime.strptime(nome.removeprefix("coleta_")[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _numero(valor: Any) -> int:
    return valor if isinstance(valor, int) and valor >= 0 else 0


def carregar_ultima_cobertura_por_fonte(diretorio: Path, dia: date) -> dict[str, dict[str, Any]]:
    """Lê a última execução do dia para cada fonte.

    Uma segunda coleta de uma mesma fonte normalmente é retentativa. Por isso
    substituímos a execução anterior em vez de somar candidatos e inflar o
    potencial diário. Fontes distintas, inclusive em lotes distintos, seguem
    aparecendo juntas no painel.
    """

    ultimas: dict[str, dict[str, Any]] = {}
    for caminho in sorted(diretorio.glob("coleta_*.json")):
        if _data_do_nome(caminho) != dia:
            continue
        try:
            documento = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for fonte in documento.get("fontes", []):
            alvo_id = fonte.get("alvo_id")
            if isinstance(alvo_id, str) and alvo_id:
                ultimas[alvo_id] = {**fonte, "arquivo_cobertura": caminho.name}
    return ultimas


def resumir_painel(fontes: dict[str, dict[str, Any]], *, dia: date, meta: int) -> dict[str, Any]:
    """Converte coberturas em indicadores de operação, sem inferir elegibilidade."""

    linhas = []
    totais: Counter[str] = Counter()
    diagnosticos: Counter[str] = Counter()
    for alvo_id, fonte in sorted(fontes.items()):
        linha = {chave: _numero(fonte.get(chave)) for chave in CHAVES_SOMAVEIS}
        erros = fonte.get("erros", {})
        pendentes = fonte.get("detalhes_sem_resposta", [])
        linha.update(
            {
                "alvo_id": alvo_id,
                "fila": (
                    fonte.get("fila")
                    if fonte.get("fila") in {"rapida", "normal", "lenta"}
                    else "normal"
                ),
                "arquivo_cobertura": fonte["arquivo_cobertura"],
                "diagnostico": fonte.get("diagnostico", "relatorio_antigo_sem_diagnostico"),
                "proxima_acao": fonte.get(
                    "proxima_acao", "Reexecute com a versão atual para obter diagnóstico."
                ),
                "erros_download": len(erros) if isinstance(erros, dict) else 0,
                "detalhes_sem_resposta": len(pendentes) if isinstance(pendentes, list) else 0,
                "taxa_http_detalhes": (
                    round(100 * linha["detalhes_http_ok"] / linha["detalhes_agendados"], 1)
                    if linha["detalhes_agendados"]
                    else None
                ),
                "anuncios_extraidos": None,
                "anuncios_elegiveis": None,
            }
        )
        totais.update({chave: linha[chave] for chave in CHAVES_SOMAVEIS})
        totais["erros_download"] += linha["erros_download"]
        totais["detalhes_sem_resposta"] += linha["detalhes_sem_resposta"]
        diagnosticos[linha["diagnostico"]] += 1
        linhas.append(linha)

    confirmacao = (
        round(100 * totais["detalhes_http_ok"] / totais["detalhes_agendados"], 1)
        if totais["detalhes_agendados"]
        else None
    )
    return {
        "versao_schema": 1,
        "dia_utc": dia.isoformat(),
        "meta_diaria_anuncios_elegiveis": meta,
        "aviso": (
            "A meta de anúncios elegíveis ainda não é calculável por cobertura. "
            "Execute a extração e a validação de elegibilidade para preencher essa etapa."
        ),
        "totais": {
            "fontes_coletadas": len(linhas),
            **{chave: totais[chave] for chave in CHAVES_SOMAVEIS},
            "erros_download": totais["erros_download"],
            "detalhes_sem_resposta": totais["detalhes_sem_resposta"],
            "fontes_por_diagnostico": dict(sorted(diagnosticos.items())),
            "taxa_http_detalhes": confirmacao,
            "anuncios_extraidos": None,
            "anuncios_elegiveis": None,
        },
        "fontes": linhas,
    }


def painel_markdown(painel: dict[str, Any]) -> str:
    """Gera uma visão humana do mesmo conteúdo JSON."""

    totais = painel["totais"]
    linhas = [
        f"# Painel diário de fontes — {painel['dia_utc']}",
        "",
        f"Meta: **{painel['meta_diaria_anuncios_elegiveis']} anúncios elegíveis/dia**.",
        "",
        painel["aviso"],
        "",
        "## Cobertura observada",
        "",
        f"- Fontes coletadas: {totais['fontes_coletadas']}",
        f"- Candidatos únicos descobertos: {totais['candidatos_unicos']}",
        f"- Detalhes com HTTP 2xx: {totais['detalhes_http_ok']}",
        f"- Taxa de confirmação dos detalhes: {totais['taxa_http_detalhes'] or 'n/d'}%",
        f"- Erros de download: {totais['erros_download']}",
        "",
        "## Diagnósticos e próxima ação",
        "",
    ]
    for diagnostico, quantidade in totais["fontes_por_diagnostico"].items():
        linhas.append(f"- {diagnostico}: {quantidade}")
    linhas.extend(
        [
            "",
            "## Por fonte",
            "",
            "| Fonte | Diagnóstico | Próxima ação | Candidatos | Detalhes HTTP OK | Erros |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for fonte in painel["fontes"]:
        linhas.append(
            f"| {fonte['alvo_id']} | {fonte['diagnostico']} | {fonte['proxima_acao']} | "
            f"{fonte['candidatos_unicos']} | {fonte['detalhes_http_ok']} | "
            f"{fonte['erros_download']} |"
        )
    return "\n".join(linhas) + "\n"


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dia", type=date.fromisoformat, required=True, help="data UTC (AAAA-MM-DD)"
    )
    parser.add_argument(
        "--diretorio-cobertura", type=Path, default=RAIZ_PROJETO / "outputs/cobertura"
    )
    parser.add_argument("--meta", type=int, default=1000)
    parser.add_argument("--saida-json", type=Path, required=True)
    parser.add_argument("--saida-markdown", type=Path, required=True)
    return parser


def main(argumentos: list[str] | None = None) -> int:
    args = criar_parser().parse_args(argumentos)
    if args.meta < 1:
        raise SystemExit("ERRO: --meta deve ser maior que zero")
    fontes = carregar_ultima_cobertura_por_fonte(args.diretorio_cobertura, args.dia)
    painel = resumir_painel(fontes, dia=args.dia, meta=args.meta)
    for saida, conteudo in (
        (args.saida_json, json.dumps(painel, ensure_ascii=False, indent=2) + "\n"),
        (args.saida_markdown, painel_markdown(painel)),
    ):
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(conteudo, encoding="utf-8")
    print(f"Painel salvo em: {args.saida_markdown}")
    print(f"Dados JSON salvos em: {args.saida_json}")
    print(f"Fontes consolidadas: {painel['totais']['fontes_coletadas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
