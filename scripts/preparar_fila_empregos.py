"""Gera uma fila somente leitura de vagas candidatas ao Empregos."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.catalog import AlvoColeta, carregar_alvos_csv
from observatorio_vagas.extraction.data_publicacao import dia_publicacao, ontem_brasilia
from observatorio_vagas.integrations.empregos import (
    ItemFilaEmpregos,
    MotivoFilaEmpregos,
    ResultadoFilaEmpregos,
    preparar_fila_empregos,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    RepositorioVagasMongoDB,
)

LIMITE_CONSULTA_POR_ALVO = 1000


def criar_parser() -> argparse.ArgumentParser:
    """Define os filtros e a exportação do relatório."""

    parser = argparse.ArgumentParser(
        description=(
            "Avalia várias vagas para o Empregos sem gravar no MongoDB "
            "e sem realizar chamadas HTTP."
        )
    )
    parser.add_argument(
        "--catalogo",
        type=Path,
        default=Path("config/catalogo_fontes.csv"),
        help="catálogo atual de fontes e autorizações",
    )
    parser.add_argument(
        "--alvo-id",
        help="avalia somente os anúncios deste alvo",
    )
    parser.add_argument(
        "--somente-catalogo",
        action="store_true",
        help="ignora anúncios cujos alvos não aparecem no catálogo informado",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=100,
        help="quantidade máxima de anúncios avaliados (padrão: 100; máximo: 10000)",
    )
    datas = parser.add_mutually_exclusive_group()
    datas.add_argument(
        "--publicados-ontem",
        action="store_true",
        help="considera somente vagas publicadas ontem no horário de Brasília",
    )
    datas.add_argument(
        "--publicados-em",
        type=date.fromisoformat,
        help="considera somente vagas publicadas em YYYY-MM-DD",
    )
    parser.add_argument(
        "--saida-json",
        type=Path,
        help="arquivo opcional para o relatório completo",
    )
    parser.add_argument(
        "--diretorio-payloads",
        type=Path,
        help=(
            "diretório-base para exportar payloads elegíveis em JSON; "
            "não chama a API nem grava no MongoDB"
        ),
    )
    return parser


def _indexar_alvos(caminho: Path) -> dict[str, AlvoColeta]:
    """Mantém uma única política atual por alvo."""

    return {alvo.alvo_id: alvo for alvo in carregar_alvos_csv(caminho)}


def _listar_anuncios_do_catalogo(
    repositorio: RepositorioAnunciosMongoDB,
    *,
    alvos: dict[str, AlvoColeta],
    limite: int,
    publicado_em: date | None = None,
) -> tuple[Any, ...]:
    """Reúne somente anúncios dos alvos presentes no catálogo."""

    por_id: dict[object, Any] = {}
    limite_por_alvo = min(limite, LIMITE_CONSULTA_POR_ALVO)

    for alvo_id in alvos:
        for anuncio in repositorio.listar_por_alvo(alvo_id, limite=limite_por_alvo):
            por_id[anuncio.id] = anuncio

    candidatos = (
        anuncio
        for anuncio in por_id.values()
        if publicado_em is None or dia_publicacao(anuncio.publicado_em) == publicado_em
    )
    ordenados = sorted(
        candidatos,
        key=lambda anuncio: anuncio.ultima_observacao_em,
        reverse=True,
    )

    return tuple(ordenados[:limite])


def _motivo_para_json(motivo: MotivoFilaEmpregos) -> dict[str, Any]:
    """Converte um motivo conhecido em estrutura serializável."""

    return {
        "codigo": motivo.codigo,
        "campo": motivo.campo,
        "mensagem": motivo.mensagem,
    }


def _item_para_json(item: ItemFilaEmpregos) -> dict[str, Any]:
    """Converte UUIDs e enums sem incluir o payload completo."""

    return {
        "anuncio_id": str(item.anuncio_id),
        "vaga_id": str(item.vaga_id) if item.vaga_id is not None else None,
        "empresa_id": str(item.empresa_id) if item.empresa_id is not None else None,
        "alvo_id": item.alvo_id,
        "titulo": item.titulo,
        "situacao": item.situacao.value,
        "campos_preenchidos": item.campos_preenchidos,
        "total_campos": item.total_campos,
        "percentual_preenchimento": round(item.percentual_preenchimento, 2),
        "chave_idempotencia": item.chave_idempotencia,
        "situacao_publicacao_existente": (
            item.situacao_publicacao_existente.value
            if item.situacao_publicacao_existente is not None
            else None
        ),
        "bloqueios": [_motivo_para_json(motivo) for motivo in item.bloqueios],
        "alertas": [_motivo_para_json(motivo) for motivo in item.alertas],
    }


def _imprimir_item(item: ItemFilaEmpregos) -> None:
    """Mostra IDs suficientes para o diagnóstico individual."""

    print()
    print(f"[{item.situacao.value.upper()}] {item.titulo}")
    print(f"Anúncio ID: {item.anuncio_id}")
    print(f"Vaga ID: {item.vaga_id or 'não encontrada'}")
    print(f"Alvo ID: {item.alvo_id or 'não informado'}")
    print(
        f"Campos: {item.campos_preenchidos}/{item.total_campos} "
        f"({item.percentual_preenchimento:.2f}%)"
    )

    if item.situacao_publicacao_existente is not None:
        print(f"Histórico: {item.situacao_publicacao_existente.value}")

    for bloqueio in item.bloqueios:
        campo = f" | campo={bloqueio.campo}" if bloqueio.campo else ""
        print(f"- BLOQUEIO {bloqueio.codigo}{campo}: {bloqueio.mensagem}")

    for alerta in item.alertas:
        campo = f" | campo={alerta.campo}" if alerta.campo else ""
        print(f"- ALERTA{campo}: {alerta.mensagem}")


def _documento_payload(item: ItemFilaEmpregos) -> dict[str, Any]:
    """Forma um arquivo revisável para a futura integração HTTP."""

    preparacao = item.preparacao

    if preparacao is None or preparacao.payload is None:
        raise ValueError("item elegível não possui payload preparado")

    if item.vaga_id is None or item.empresa_id is None or item.chave_idempotencia is None:
        raise ValueError("item elegível não possui identidade completa de publicação")

    return {
        "versao_schema": 2,
        "anuncio_id": str(item.anuncio_id),
        "vaga_id": str(item.vaga_id),
        "empresa_id": str(item.empresa_id),
        "alvo_id": item.alvo_id,
        "titulo": item.titulo,
        "chave_idempotencia": item.chave_idempotencia,
        "proveniencia_fonte": (
            preparacao.proveniencia_fonte.para_documento()
            if preparacao.proveniencia_fonte is not None
            else None
        ),
        "payload": preparacao.payload,
    }


def _exportar_payloads_locais(
    resultado: ResultadoFilaEmpregos,
    *,
    diretorio_base: Path,
    gerado_em: datetime,
) -> Path:
    """Grava uma caixa de saída local, sem registrar nem enviar operações."""

    identificador_lote = gerado_em.strftime("lote-%Y%m%dT%H%M%S%fZ")
    diretorio_lote = diretorio_base / identificador_lote

    if diretorio_lote.exists():
        raise FileExistsError(f"o diretório de saída já existe: {diretorio_lote}")

    diretorio_payloads = diretorio_lote / "payloads"
    diretorio_payloads.mkdir(parents=True)
    itens_manifesto: list[dict[str, Any]] = []
    payloads_unificados: list[dict[str, Any]] = []

    for item in resultado.elegiveis:
        documento = _documento_payload(item)
        # O hash completo continua dentro do documento e do manifesto. Usamos
        # só 24 caracteres no nome para não ultrapassar o limite de caminhos
        # do Windows quando a pasta do projeto já possui nome longo.
        nome_arquivo = f"{item.chave_idempotencia[:24]}.json"
        caminho = diretorio_payloads / nome_arquivo

        if caminho.exists():
            raise FileExistsError(f"colisão no arquivo de payload: {caminho}")

        caminho.write_text(
            json.dumps(documento, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Lista pronta para o teste em lote. A identidade e a proveniência
        # continuam disponíveis nos arquivos individuais e no manifesto.
        payloads_unificados.append(documento["payload"])
        itens_manifesto.append(
            {
                "anuncio_id": documento["anuncio_id"],
                "vaga_id": documento["vaga_id"],
                "titulo": documento["titulo"],
                "chave_idempotencia": documento["chave_idempotencia"],
                "arquivo": str(Path("payloads") / nome_arquivo),
            }
        )

    manifesto = {
        "versao_schema": 1,
        "gerado_em": gerado_em.isoformat(),
        "modo": "PREPARACAO_LOCAL_SEM_API",
        "resumo": {
            "anuncios_avaliados": len(resultado.itens),
            "payloads_exportados": len(itens_manifesto),
            "bloqueados": len(resultado.bloqueadas),
            "ja_registrados": len(resultado.ja_registradas),
            "duplicadas": len(resultado.duplicadas),
        },
        "arquivo_payloads_unificados": "payloads_unificados.json",
        "itens": itens_manifesto,
    }
    (diretorio_lote / "payloads_unificados.json").write_text(
        json.dumps(payloads_unificados, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (diretorio_lote / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return diretorio_lote


def executar(argumentos: list[str] | None = None) -> int:
    """Consulta o banco e produz a fila sem qualquer mutação."""

    opcoes = criar_parser().parse_args(argumentos)

    if opcoes.limite < 1 or opcoes.limite > 10000:
        print("ERRO: limite deve estar entre 1 e 10000")
        return 2

    publicado_em = ontem_brasilia() if opcoes.publicados_ontem else opcoes.publicados_em

    try:
        alvos = _indexar_alvos(opcoes.catalogo)
        configuracoes = get_settings()
        gerado_em = datetime.now(UTC)

        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)
            if opcoes.alvo_id:
                anuncios = repositorio_anuncios.listar_por_alvo(
                    opcoes.alvo_id,
                    limite=min(opcoes.limite, LIMITE_CONSULTA_POR_ALVO),
                )
            elif opcoes.somente_catalogo:
                anuncios = _listar_anuncios_do_catalogo(
                    repositorio_anuncios,
                    alvos=alvos,
                    limite=opcoes.limite,
                    publicado_em=publicado_em,
                )
            else:
                anuncios = repositorio_anuncios.listar_recentes(
                    limite=min(opcoes.limite, LIMITE_CONSULTA_POR_ALVO),
                )
            if publicado_em is not None and not opcoes.somente_catalogo:
                anuncios = tuple(
                    anuncio
                    for anuncio in anuncios
                    if dia_publicacao(anuncio.publicado_em) == publicado_em
                )
            resultado = preparar_fila_empregos(
                anuncios,
                alvos=alvos,
                repositorio_empresas=RepositorioEmpresasMongoDB(conexao.banco),
                repositorio_vagas=RepositorioVagasMongoDB(conexao.banco),
                repositorio_publicacoes=(RepositorioPublicacoesEmpregosMongoDB(conexao.banco)),
            )

        print()
        print("# FILA DE PREPARAÇÃO PARA O EMPREGOS")
        print()
        print("Modo: SOMENTE LEITURA")
        print(f"Gerado em: {gerado_em.isoformat()}")
        print(
            "Filtro de publicação: "
            f"{publicado_em.isoformat() if publicado_em is not None else 'sem filtro'}"
        )
        print(f"Anúncios avaliados: {len(resultado.itens)}")
        print(f"Elegíveis para revisão final: {len(resultado.elegiveis)}")
        print(f"Bloqueados: {len(resultado.bloqueadas)}")
        print(f"Duplicadas entre fontes: {len(resultado.duplicadas)}")
        print(f"Já registrados no histórico: {len(resultado.ja_registradas)}")
        print("Chamadas HTTP: 0")
        print("Gravações no MongoDB: 0")

        for item in resultado.itens:
            _imprimir_item(item)

        diretorio_payloads = None

        if opcoes.diretorio_payloads is not None:
            diretorio_payloads = _exportar_payloads_locais(
                resultado,
                diretorio_base=opcoes.diretorio_payloads,
                gerado_em=gerado_em,
            )
            print()
            print(f"Payloads locais exportados em: {diretorio_payloads}")
            print(f"Payloads unificados: {diretorio_payloads / 'payloads_unificados.json'}")
            print("Chamadas HTTP: 0")
            print("Gravações no MongoDB: 0")

        if opcoes.saida_json is not None:
            documento = {
                "gerado_em": gerado_em.isoformat(),
                "publicados_em": publicado_em.isoformat() if publicado_em is not None else None,
                "diretorio_payloads": str(diretorio_payloads) if diretorio_payloads else None,
                "resumo": {
                    "avaliados": len(resultado.itens),
                    "elegiveis": len(resultado.elegiveis),
                    "bloqueados": len(resultado.bloqueadas),
                    "ja_registrados": len(resultado.ja_registradas),
                    "duplicadas": len(resultado.duplicadas),
                },
                "itens": [_item_para_json(item) for item in resultado.itens],
            }
            opcoes.saida_json.parent.mkdir(parents=True, exist_ok=True)
            opcoes.saida_json.write_text(
                json.dumps(documento, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print()
            print(f"Relatório JSON salvo em: {opcoes.saida_json}")

    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
