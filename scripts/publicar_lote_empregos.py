"""Simula ou publica um lote pequeno e auditável de vagas no Empregos."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from observatorio_vagas.config import Settings, get_settings
from observatorio_vagas.crawling.catalog import AlvoColeta, carregar_alvos_csv
from observatorio_vagas.integrations.empregos import (
    ClienteEmpregos,
    ItemResultadoLoteEmpregos,
    PublicadorEmpregos,
    ResultadoFilaEmpregos,
    ResultadoPublicacaoLoteEmpregos,
    preparar_fila_empregos,
    publicar_fila_empregos,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    RepositorioVagasMongoDB,
    preparar_banco,
)


def criar_parser() -> argparse.ArgumentParser:
    """Define limites baixos e confirmação explícita para o lote."""

    parser = argparse.ArgumentParser(
        description=(
            "Prepara várias vagas e simula a publicação no Empregos. "
            "Somente --confirmar-publicacao permite escritas e requisições HTTP."
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
        help="considera somente os anúncios deste alvo",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=100,
        help="quantidade máxima de anúncios avaliados (padrão: 100)",
    )
    parser.add_argument(
        "--maximo-envios",
        type=int,
        default=10,
        help="máximo de vagas simuladas ou publicadas neste lote (padrão: 10)",
    )
    parser.add_argument(
        "--confirmar-publicacao",
        action="store_true",
        help=(
            "autoriza histórico no MongoDB e POSTs para as vagas elegíveis; "
            "também exige as travas de produção do .env"
        ),
    )
    parser.add_argument(
        "--saida-json",
        type=Path,
        help="arquivo opcional com o resultado operacional, sem payload ou segredo",
    )
    return parser


def _indexar_alvos(caminho: Path) -> dict[str, AlvoColeta]:
    """Mantém uma política atual por identificador de alvo."""

    return {alvo.alvo_id: alvo for alvo in carregar_alvos_csv(caminho)}


def executar_publicacao_lote(
    *,
    fila: ResultadoFilaEmpregos,
    configuracoes: Settings,
    conexao: ConexaoMongoDB,
    confirmar_publicacao: bool,
    maximo_envios: int,
) -> ResultadoPublicacaoLoteEmpregos:
    """Valida as travas antes da primeira escrita e processa o lote."""

    cliente = ClienteEmpregos(configuracoes)
    repositorio = RepositorioPublicacoesEmpregosMongoDB(conexao.banco)

    if confirmar_publicacao and fila.elegiveis:
        # Falhar aqui impede até a criação dos índices quando endpoint,
        # ambiente, chave ou kill switch não estiverem corretos.
        cliente.validar_configuracao_publicacao()
        preparar_banco(conexao.banco)

    return publicar_fila_empregos(
        fila,
        publicador=PublicadorEmpregos(cliente, repositorio),
        confirmar_publicacao=confirmar_publicacao,
        limite_envios=maximo_envios,
    )


def _item_para_json(item: ItemResultadoLoteEmpregos) -> dict[str, Any]:
    """Serializa apenas metadados seguros do resultado."""

    return {
        "anuncio_id": str(item.anuncio_id),
        "vaga_id": str(item.vaga_id) if item.vaga_id is not None else None,
        "titulo": item.titulo,
        "situacao": item.situacao.value,
        "operacao_id": (str(item.operacao_id) if item.operacao_id is not None else None),
        "status_http": item.status_http,
        "request_id": item.request_id,
        "mensagem": item.mensagem,
    }


def _imprimir_resultado(resultado: ResultadoPublicacaoLoteEmpregos) -> None:
    """Mostra um resumo completo sem revelar payload ou credencial."""

    print()
    print("# RESULTADO DO LOTE PARA O EMPREGOS")
    print()
    print(f"Modo: {'PUBLICAÇÃO CONFIRMADA' if resultado.confirmada else 'SIMULAÇÃO'}")
    print(f"Itens no relatório: {len(resultado.itens)}")
    print(f"Simulados: {len(resultado.simuladas)}")
    print(f"Publicados: {len(resultado.publicadas)}")
    print(f"Sucessos reutilizados: {len(resultado.reutilizadas)}")
    print(f"Ignorados ou adiados: {len(resultado.ignoradas)}")
    print(f"Falhas: {len(resultado.falhas)}")

    if not resultado.confirmada:
        print("Chamadas HTTP: 0")
        print("Gravações no MongoDB: 0")

    for item in resultado.itens:
        print()
        print(f"[{item.situacao.value.upper()}] {item.titulo}")
        print(f"Anúncio ID: {item.anuncio_id}")
        print(f"Vaga ID: {item.vaga_id or 'não encontrada'}")

        if item.operacao_id is not None:
            print(f"Operação ID: {item.operacao_id}")

        if item.status_http is not None:
            print(f"Status HTTP: {item.status_http}")

        if item.request_id is not None:
            print(f"Request ID: {item.request_id}")

        if item.mensagem:
            print(f"Detalhe: {item.mensagem}")


def executar(argumentos: list[str] | None = None) -> int:
    """Carrega a fila, aplica o limite seguro e executa o modo escolhido."""

    opcoes = criar_parser().parse_args(argumentos)

    if opcoes.limite < 1 or opcoes.limite > 1000:
        print("ERRO: limite deve estar entre 1 e 1000")
        return 2

    if opcoes.maximo_envios < 1 or opcoes.maximo_envios > 100:
        print("ERRO: maximo-envios deve estar entre 1 e 100")
        return 2

    try:
        alvos = _indexar_alvos(opcoes.catalogo)
        configuracoes = get_settings()

        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)
            anuncios = (
                repositorio_anuncios.listar_por_alvo(opcoes.alvo_id, limite=opcoes.limite)
                if opcoes.alvo_id
                else repositorio_anuncios.listar_recentes(limite=opcoes.limite)
            )
            repositorio_publicacoes = RepositorioPublicacoesEmpregosMongoDB(conexao.banco)
            fila = preparar_fila_empregos(
                anuncios,
                alvos=alvos,
                repositorio_empresas=RepositorioEmpresasMongoDB(conexao.banco),
                repositorio_vagas=RepositorioVagasMongoDB(conexao.banco),
                repositorio_publicacoes=repositorio_publicacoes,
            )

            print()
            print("# PLANO DO LOTE PARA O EMPREGOS")
            print()
            print(
                "Modo solicitado: "
                f"{'PUBLICAÇÃO REAL' if opcoes.confirmar_publicacao else 'SIMULAÇÃO'}"
            )
            print(f"Anúncios avaliados: {len(fila.itens)}")
            print(f"Elegíveis: {len(fila.elegiveis)}")
            print(f"Bloqueados: {len(fila.bloqueadas)}")
            print(f"Com histórico: {len(fila.ja_registradas)}")
            print(f"Limite de envios: {opcoes.maximo_envios}")

            resultado = executar_publicacao_lote(
                fila=fila,
                configuracoes=configuracoes,
                conexao=conexao,
                confirmar_publicacao=opcoes.confirmar_publicacao,
                maximo_envios=opcoes.maximo_envios,
            )

        _imprimir_resultado(resultado)

        if opcoes.saida_json is not None:
            documento = {
                "gerado_em": datetime.now(UTC).isoformat(),
                "confirmada": resultado.confirmada,
                "limite_envios": resultado.limite_envios,
                "resumo": {
                    "itens": len(resultado.itens),
                    "simulados": len(resultado.simuladas),
                    "publicados": len(resultado.publicadas),
                    "reutilizados": len(resultado.reutilizadas),
                    "ignorados": len(resultado.ignoradas),
                    "falhas": len(resultado.falhas),
                },
                "resultados": [_item_para_json(item) for item in resultado.itens],
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

    return 1 if resultado.falhas else 0


if __name__ == "__main__":
    raise SystemExit(executar())
