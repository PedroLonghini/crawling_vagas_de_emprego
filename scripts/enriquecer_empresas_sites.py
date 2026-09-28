"""Enriquece várias empresas a partir de uma planilha de URLs oficiais.

A planilha é deliberadamente simples: cada linha contém uma empresa já
existente no MongoDB e uma URL oficial conferida por uma pessoa. O comando não
procura URLs, não segue links de agregadores e não altera dados sem a opção
``--confirmar``.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from enriquecer_empresa_site import (
    ErroEnriquecimentoEmpresa,
    baixar_pagina_oficial,
    converter_uuid,
    extrair_cnpj_unico_html,
    preparar_empresa_atualizada,
    salvar_evidencia_bruta,
)

from observatorio_vagas.config import get_settings
from observatorio_vagas.extraction.metadados_empresa import (
    extrair_metadados_site_institucional_html,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioEmpresasMongoDB,
    RepositorioEmpresasMongoDB,
)


def criar_parser() -> argparse.ArgumentParser:
    """Define os argumentos do processamento em lote."""

    parser = argparse.ArgumentParser(
        description=(
            "Enriquece empresas usando URLs oficiais preenchidas em CSV. "
            "Sem --confirmar, somente mostra e grava o relatório local."
        )
    )
    parser.add_argument(
        "--arquivo", type=Path, required=True, help="CSV com empresa_id e url_oficial"
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=100,
        help="máximo de empresas do arquivo a analisar (padrão: 100)",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="autoriza salvar as evidências brutas e empresas atualizadas",
    )
    parser.add_argument(
        "--saida-json",
        type=Path,
        default=Path("outputs/enriquecimento_empresas_sites.json"),
        help="relatório detalhado da execução",
    )
    return parser


def carregar_linhas(caminho: Path, *, limite: int) -> tuple[dict[str, str], ...]:
    """Valida o CSV e conserva somente linhas prontas para processamento."""

    if limite < 1 or limite > 10_000:
        raise ErroEnriquecimentoEmpresa("limite deve estar entre 1 e 10000")

    try:
        with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
            leitor = csv.DictReader(arquivo)

            if leitor.fieldnames is None or not {"empresa_id", "url_oficial"} <= set(
                leitor.fieldnames
            ):
                raise ErroEnriquecimentoEmpresa(
                    "o CSV precisa possuir as colunas empresa_id e url_oficial"
                )

            return tuple(
                {
                    "empresa_id": (linha.get("empresa_id") or "").strip(),
                    "empresa_nome": (linha.get("empresa_nome") or "").strip(),
                    "url_oficial": (linha.get("url_oficial") or "").strip(),
                }
                for linha in leitor
                if (linha.get("empresa_id") or "").strip()
                and (linha.get("url_oficial") or "").strip()
            )[:limite]
    except OSError as erro:
        raise ErroEnriquecimentoEmpresa(f"não foi possível ler o CSV: {erro}") from erro


def _resultado_base(linha: dict[str, str]) -> dict[str, Any]:
    """Preserva informação suficiente para corrigir uma linha com falha."""

    return {
        "empresa_id": linha["empresa_id"],
        "empresa_nome_csv": linha["empresa_nome"],
        "url_oficial": linha["url_oficial"],
    }


def executar(argumentos: list[str] | None = None) -> int:
    """Processa cada linha de forma independente, sem abortar o lote inteiro."""

    opcoes = criar_parser().parse_args(argumentos)

    try:
        linhas = carregar_linhas(opcoes.arquivo, limite=opcoes.limite)
        configuracoes = get_settings()
    except ErroEnriquecimentoEmpresa as erro:
        print(f"ERRO: {erro}")
        return 2

    resultados: list[dict[str, Any]] = []
    sucessos = 0
    inalteradas = 0
    falhas = 0

    try:
        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio = RepositorioEmpresasMongoDB(conexao.banco)

            for numero, linha in enumerate(linhas, start=1):
                item = _resultado_base(linha)

                try:
                    empresa_id = converter_uuid(linha["empresa_id"], nome="empresa_id")
                    empresa = repositorio.buscar_por_id(empresa_id)

                    if empresa is None:
                        raise ErroEnriquecimentoEmpresa("empresa não encontrada no MongoDB")

                    corpo, url_final, tipo_conteudo = baixar_pagina_oficial(
                        linha["url_oficial"],
                        timeout=configuracoes.request_timeout_seconds,
                    )
                    metadados = extrair_metadados_site_institucional_html(
                        corpo,
                        url_base=url_final,
                    )

                    if metadados is None:
                        raise ErroEnriquecimentoEmpresa(
                            "página sem dados institucionais reconhecíveis"
                        )

                    atualizada, evidencias, alterado = preparar_empresa_atualizada(
                        empresa=empresa,
                        url_final=url_final,
                        metadados=metadados,
                        corpo=corpo,
                        cnpj_encontrado=extrair_cnpj_unico_html(corpo),
                    )
                    item.update(
                        {
                            "situacao": "atualizaria" if alterado else "inalterada",
                            "empresa_nome": empresa.nome_exibicao,
                            "url_final": url_final,
                            "descricao_encontrada": atualizada.descricao is not None,
                            "cnpj_encontrado": atualizada.cnpj,
                            "evidencias": [evidencia.campo for evidencia in evidencias],
                        }
                    )

                    if opcoes.confirmar and alterado:
                        salvar_evidencia_bruta(
                            diretorio_bruto=configuracoes.raw_storage_path,
                            empresa=empresa,
                            url_solicitada=linha["url_oficial"],
                            url_final=url_final,
                            tipo_conteudo=tipo_conteudo,
                            corpo=corpo,
                        )
                        repositorio.salvar(atualizada)

                    if alterado:
                        sucessos += 1
                    else:
                        inalteradas += 1

                    print(
                        f"[{numero}/{len(linhas)}] {empresa.nome_exibicao}: "
                        f"{'pronta para gravar' if alterado else 'sem alterações'}"
                    )

                except (
                    ErroEnriquecimentoEmpresa,
                    ErroRepositorioEmpresasMongoDB,
                    OSError,
                    ValueError,
                ) as erro:
                    falhas += 1
                    item.update({"situacao": "falha", "erro": str(erro)})
                    identificacao = linha["empresa_nome"] or linha["empresa_id"]
                    print(f"[{numero}/{len(linhas)}] FALHA: {identificacao} | {erro}")

                resultados.append(item)

    except (ErroConexaoMongoDB, ErroRepositorioEmpresasMongoDB) as erro:
        print(f"ERRO: {erro}")
        return 1

    relatorio = {
        "versao_schema": 1,
        "gerado_em": datetime.now(UTC).isoformat(),
        "modo": "gravacao_confirmada" if opcoes.confirmar else "previa",
        "linhas_validas": len(linhas),
        "atualizarias_ou_atualizadas": sucessos,
        "inalteradas": inalteradas,
        "falhas": falhas,
        "itens": resultados,
    }
    opcoes.saida_json.parent.mkdir(parents=True, exist_ok=True)
    opcoes.saida_json.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n# RESULTADO DO ENRIQUECIMENTO EM LOTE\n")
    print(f"Modo: {'GRAVAÇÃO CONFIRMADA' if opcoes.confirmar else 'SOMENTE PRÉVIA'}")
    print(f"Linhas processadas: {len(linhas)}")
    print(f"Atualizáveis/atualizadas: {sucessos}")
    print(f"Inalteradas: {inalteradas}")
    print(f"Falhas: {falhas}")
    print(f"Relatório JSON: {opcoes.saida_json}")

    if not opcoes.confirmar:
        print("Use --confirmar somente após revisar o relatório.")

    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(executar())
