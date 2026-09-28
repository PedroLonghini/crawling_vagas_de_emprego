"""Gera uma fila revisável de empresas encontradas em fontes agregadoras.

O arquivo produzido não é um catálogo autorizado e não muda o MongoDB. Ele
serve para separar empresas que apareceram em vagas de um agregador e que
precisam ter o site de carreiras e a permissão de republicação verificados.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    ErroRepositorioEmpresasMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
)


def criar_parser() -> argparse.ArgumentParser:
    """Define filtros e destino do relatório local."""

    parser = argparse.ArgumentParser(
        description=(
            "Agrupa empresas encontradas em anúncios para pesquisa manual de "
            "site de carreiras e permissão de republicação. Não altera o MongoDB."
        )
    )
    parser.add_argument("--alvo-id", required=True, help="alvo que originou os anúncios")
    parser.add_argument(
        "--limite",
        type=int,
        default=1000,
        help="máximo de anúncios recentes a analisar (padrão: 1000)",
    )
    parser.add_argument(
        "--somente-sem-pagina",
        action="store_true",
        help="omite empresas que já possuem uma página de carreiras cadastrada",
    )
    parser.add_argument(
        "--saida-json",
        type=Path,
        default=Path("outputs/fontes_candidatas.json"),
        help="arquivo JSON para revisão humana",
    )
    parser.add_argument(
        "--saida-csv-sites",
        type=Path,
        help=(
            "opcional: cria uma planilha-modelo para preencher URLs oficiais "
            "e usar no enriquecimento em lote"
        ),
    )
    return parser


def _chave_empresa(anuncio: AnuncioVaga) -> str:
    """Agrupa pelo UUID quando há empresa resolvida, ou pelo nome publicado."""

    if anuncio.empresa_id is not None:
        return f"id:{anuncio.empresa_id}"
    nome = (anuncio.empresa_original or "empresa não informada").strip().casefold()
    return f"nome:{nome}"


def _empresa_do_anuncio(
    anuncio: AnuncioVaga,
    repositorio: RepositorioEmpresasMongoDB,
) -> Empresa | None:
    """Busca uma empresa já resolvida; ausência é aceitável nesta fila."""

    if anuncio.empresa_id is None:
        return None
    return repositorio.buscar_por_id(anuncio.empresa_id)


def _item_inicial(anuncio: AnuncioVaga, empresa: Empresa | None) -> dict[str, Any]:
    """Cria a estrutura de uma empresa candidata sem inferir autorização."""

    nome = empresa.nome_exibicao if empresa is not None else anuncio.empresa_original
    return {
        "empresa_id": str(empresa.id) if empresa is not None else None,
        "empresa_nome": nome or "Empresa não informada",
        "dominio_conhecido": empresa.dominio if empresa is not None else None,
        "site_conhecido": str(empresa.site) if empresa and empresa.site else None,
        "pagina_carreiras_conhecida": (
            str(empresa.pagina_carreiras) if empresa and empresa.pagina_carreiras else None
        ),
        "cnpj_conhecido": empresa.cnpj if empresa is not None else None,
        "quantidade_anuncios": 0,
        "anuncios_com_descricao": 0,
        "ultima_observacao_em": None,
        "amostras": [],
    }


def _adicionar_anuncio(item: dict[str, Any], anuncio: AnuncioVaga) -> None:
    """Atualiza métricas e guarda no máximo três vagas auditáveis."""

    item["quantidade_anuncios"] += 1
    if len(anuncio.descricao_original.strip()) >= 100:
        item["anuncios_com_descricao"] += 1

    observado = anuncio.ultima_observacao_em.isoformat()
    if item["ultima_observacao_em"] is None or observado > item["ultima_observacao_em"]:
        item["ultima_observacao_em"] = observado

    if len(item["amostras"]) < 3:
        item["amostras"].append(
            {
                "anuncio_id": str(anuncio.id),
                "titulo": anuncio.titulo_original,
                "url_candidatura": str(anuncio.url_candidatura)
                if anuncio.url_candidatura
                else None,
                "url_anuncio": str(anuncio.url),
            }
        )


def _proximo_passo(item: dict[str, Any]) -> str:
    """Orienta a revisão sem afirmar que uma fonte tem licença."""

    if item["pagina_carreiras_conhecida"]:
        return "validar termos/licença da página de carreiras já cadastrada"
    if item["site_conhecido"] or item["dominio_conhecido"]:
        return "localizar página de carreiras no site conhecido e validar termos/licença"
    return "pesquisar site institucional e página de carreiras; depois validar termos/licença"


def gerar_itens(
    anuncios: list[AnuncioVaga],
    repositorio_empresas: RepositorioEmpresasMongoDB,
    *,
    somente_sem_pagina: bool,
) -> list[dict[str, Any]]:
    """Agrupa os anúncios por empresa e constrói a fila de revisão."""

    por_empresa: dict[str, dict[str, Any]] = {}

    for anuncio in anuncios:
        chave = _chave_empresa(anuncio)
        item = por_empresa.get(chave)
        if item is None:
            empresa = _empresa_do_anuncio(anuncio, repositorio_empresas)
            item = _item_inicial(anuncio, empresa)
            por_empresa[chave] = item
        _adicionar_anuncio(item, anuncio)

    itens = list(por_empresa.values())
    if somente_sem_pagina:
        itens = [item for item in itens if item["pagina_carreiras_conhecida"] is None]

    for item in itens:
        item["proximo_passo"] = _proximo_passo(item)

    return sorted(
        itens,
        key=lambda item: (item["quantidade_anuncios"], item["empresa_nome"].casefold()),
        reverse=True,
    )


def escrever_modelo_sites(caminho: Path, itens: list[dict[str, Any]]) -> None:
    """Gera uma planilha vazia; o usuário informa somente URLs oficiais confirmadas."""

    colunas = (
        "empresa_id",
        "empresa_nome",
        "url_oficial",
        "anuncio_amostra_id",
        "url_anuncio_amostra",
    )
    caminho.parent.mkdir(parents=True, exist_ok=True)

    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas)
        escritor.writeheader()

        for item in itens:
            amostras = item["amostras"]
            primeira_amostra = amostras[0] if amostras else {}
            escritor.writerow(
                {
                    "empresa_id": item["empresa_id"] or "",
                    "empresa_nome": item["empresa_nome"],
                    "url_oficial": "",
                    "anuncio_amostra_id": primeira_amostra.get("anuncio_id", ""),
                    "url_anuncio_amostra": primeira_amostra.get("url_anuncio", ""),
                }
            )


def executar(argumentos: list[str] | None = None) -> int:
    """Consulta anúncios e escreve apenas um relatório JSON local."""

    opcoes = criar_parser().parse_args(argumentos)
    if not 1 <= opcoes.limite <= 1000:
        print("ERRO: limite deve estar entre 1 e 1000")
        return 2

    try:
        with ConexaoMongoDB(get_settings()) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)
            repositorio_empresas = RepositorioEmpresasMongoDB(conexao.banco)
            anuncios = repositorio_anuncios.listar_por_alvo(opcoes.alvo_id.strip(), opcoes.limite)

            if not anuncios:
                print(f"ERRO: nenhum anúncio encontrado para o alvo: {opcoes.alvo_id}")
                return 1

            itens = gerar_itens(
                anuncios,
                repositorio_empresas,
                somente_sem_pagina=opcoes.somente_sem_pagina,
            )

    except (
        ErroConexaoMongoDB,
        ErroRepositorioAnunciosMongoDB,
        ErroRepositorioEmpresasMongoDB,
        ValueError,
    ) as erro:
        print(f"ERRO: {erro}")
        return 1

    relatorio = {
        "versao_schema": 1,
        "gerado_em": datetime.now(UTC).isoformat(),
        "alvo_id": opcoes.alvo_id,
        "anuncios_analisados": len(anuncios),
        "empresas_candidatas": len(itens),
        "aviso": (
            "Esta lista não concede autorização de republicação. Cada empresa "
            "precisa ter a licença ou os termos verificados antes de entrar no catálogo."
        ),
        "itens": itens,
    }
    opcoes.saida_json.parent.mkdir(parents=True, exist_ok=True)
    opcoes.saida_json.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if opcoes.saida_csv_sites is not None:
        escrever_modelo_sites(opcoes.saida_csv_sites, itens)

    print("# FILA DE FONTES CANDIDATAS")
    print()
    print(f"Anúncios analisados: {len(anuncios)}")
    print(f"Empresas candidatas: {len(itens)}")
    print(f"Relatório JSON salvo em: {opcoes.saida_json}")
    if opcoes.saida_csv_sites is not None:
        print(f"Planilha de URLs oficiais salva em: {opcoes.saida_csv_sites}")
    print()
    for item in itens[:20]:
        print(
            f"- {item['empresa_nome']} | anúncios: {item['quantidade_anuncios']} | "
            f"descrições: {item['anuncios_com_descricao']} | {item['proximo_passo']}"
        )
    if len(itens) > 20:
        print(f"... e mais {len(itens) - 20} empresas no arquivo JSON.")

    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
