"""Varredura semanal completa (Windows e Mac).

Decisão do usuário (08/10/2026): uma vez por semana, uma varredura completa para

1. não deixar passar vaga: coleta SEM janela de horas, listagens inteiras
   (a rotina diária só lê as últimas 24h e para nas vagas já conhecidas);
2. conferir se as vagas continuam no ar: encerra as vencidas, compara os links
   das listagens e abre a página de toda vaga já publicada no Empregos;
3. conferir duplicatas entre as vagas publicadas;
4. tirar do Empregos, automaticamente, as encerradas na origem e as duplicatas.
   Enquanto a API de remoção não estiver definida, a remoção fica simulada e
   a fila sai no resumo.

Uso (raiz do projeto, túnel do MongoDB aberto):

    python scripts/rotina_semanal.py

Para refazer as etapas seguintes sem coletar de novo (ex.: o MongoDB caiu no
fim da coleta), passe os relatórios da coleta já feita:

    python scripts/rotina_semanal.py --sem-coleta --desde 2026-10-07T17:30

Gera ``outputs/logs/<dia>_semanal_resumo.txt`` e os relatórios de cada etapa.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
CATALOGO = RAIZ / "config" / "lote_10mil_triado" / "catalogo_fontes.csv"
# A varredura tem o próprio registro de estado (links vistos, detalhes já
# baixados, agendamento): a coleta diária não mexe nele, nem ela no da diária.
NOME_ESTADO = "semanal"
DIRETORIO_ESTADO = CATALOGO.parent / ".cache" / NOME_ESTADO
DIRETORIO_COBERTURA = RAIZ / "outputs" / "cobertura"
DIRETORIO_LOGS = RAIZ / "outputs" / "logs"
DIRETORIO_REMOCAO = RAIZ / "outputs" / "verificacao" / "remocao_empregos"
DIRETORIO_CONFERENCIA = RAIZ / "outputs" / "verificacao" / "vagas_removidas"
LIMITE_ANUNCIOS_POR_FONTE = 200
# Toda vaga publicada é aberta toda semana; o resto da cota vai para as suspeitas.
MAX_PAGINAS_ABERTAS = 3000


@dataclass
class Passo:
    nome: str
    codigo: int
    resumo: str
    log: Path | None = None


@dataclass
class Varredura:
    dia: str
    inicio: datetime
    passos: list[Passo] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(passo.codigo == 0 for passo in self.passos)


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--sem-coleta",
        action="store_true",
        help="não coleta; usa os relatórios de cobertura criados desde --desde",
    )
    parser.add_argument(
        "--desde",
        type=datetime.fromisoformat,
        default=None,
        help="início da coleta já feita (AAAA-MM-DDTHH:MM, hora local) com --sem-coleta",
    )
    parser.add_argument("--max-paginas", type=int, default=MAX_PAGINAS_ABERTAS)
    return parser


def coleta_em_andamento() -> bool:
    """Outra coleta rodando regravaria o mesmo registro de links vistos."""

    import psutil

    for processo in psutil.process_iter(["cmdline"]):
        linha = " ".join(processo.info.get("cmdline") or ())
        if "processar_lote.py" in linha and processo.pid != os.getpid():
            return True
    return False


def coberturas_desde(diretorio: Path, inicio: datetime) -> list[Path]:
    """Relatórios de cobertura gravados por esta coleta (os nomes usam hora UTC)."""

    marco = inicio.timestamp()
    return sorted(
        caminho for caminho in diretorio.glob("coleta_*.json") if caminho.stat().st_mtime >= marco
    )


def rodar(nome: str, argumentos: Sequence[str], log: Path) -> int:
    """Roda um script do projeto e grava a saída em UTF-8 (também no Windows)."""

    ambiente = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as arquivo:
        processo = subprocess.run(
            [sys.executable, *argumentos],
            cwd=RAIZ,
            env=ambiente,
            stdout=arquivo,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return processo.returncode


def ultimas_linhas(log: Path, quantidade: int = 3) -> str:
    try:
        linhas = [linha for linha in log.read_text(encoding="utf-8").splitlines() if linha.strip()]
    except OSError:
        return ""
    return " | ".join(linhas[-quantidade:])


def vagas_novas_da_varredura(banco: Any, inicio: datetime) -> tuple[int, list[tuple[str, int]]]:
    """Anúncios que entraram no banco por esta coleta (a diária não tinha pegado)."""

    por_alvo = Counter(
        documento.get("alvo_id") or "?"
        for documento in banco["anuncios"].find(
            {"primeira_observacao_em": {"$gte": inicio}}, {"alvo_id": 1}
        )
    )
    return sum(por_alvo.values()), por_alvo.most_common(10)


def remover_do_empregos(banco: Any, dia: str) -> tuple[int, Counter[str], Path]:
    """Monta a fila (encerradas na origem + duplicatas) e envia a remoção."""

    from observatorio_vagas.domain.enums import StatusAnuncio
    from observatorio_vagas.integrations.empregos.remocao import (
        RemovedorNaoConfigurado,
        montar_fila_remocao,
        remover_publicacoes,
    )
    from observatorio_vagas.storage.mongodb.publicacoes import (
        RepositorioPublicacoesEmpregosMongoDB,
    )

    publicadas = RepositorioPublicacoesEmpregosMongoDB(banco).listar_no_ar()
    encerrados = [
        documento["_id"]
        for documento in banco["anuncios"].find(
            {
                "_id": {"$in": [operacao.anuncio_id for operacao in publicadas]},
                "status": StatusAnuncio.ENCERRADO.value,
            },
            {"_id": 1},
        )
    ]
    fila = montar_fila_remocao(publicadas=publicadas, anuncios_encerrados=encerrados)
    # Troca pelo removedor real quando a API de remoção estiver definida.
    resultados = remover_publicacoes(fila, removedor=RemovedorNaoConfigurado())

    saida = DIRETORIO_REMOCAO / f"{dia}.json"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(
        json.dumps(
            [
                {
                    "anuncio_id": str(resultado.item.operacao.anuncio_id),
                    "external_job_posting_id": resultado.item.operacao.external_job_posting_id,
                    "motivo": resultado.item.motivo.value,
                    "detalhe": resultado.item.detalhe,
                    "situacao": resultado.situacao.value,
                    "mensagem": resultado.mensagem,
                }
                for resultado in resultados
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return (
        len(publicadas),
        Counter(
            f"{resultado.item.motivo.value}:{resultado.situacao.value}" for resultado in resultados
        ),
        saida,
    )


def escrever_resumo(varredura: Varredura, linhas_extras: Sequence[str]) -> Path:
    resumo = DIRETORIO_LOGS / f"{varredura.dia}_semanal_resumo.txt"
    resumo.parent.mkdir(parents=True, exist_ok=True)
    linhas = [f"Varredura semanal {varredura.dia} - início {varredura.inicio:%H:%M}"]
    for passo in varredura.passos:
        estado = "ok" if passo.codigo == 0 else f"FALHA (código {passo.codigo})"
        linhas.append(f"- {passo.nome}: {estado}. {passo.resumo}")
        if passo.log is not None:
            linhas.append(f"  log: {passo.log}")
    linhas.extend(linhas_extras)
    linhas.append(
        f"Fim {datetime.now():%H:%M}. {'Tudo certo.' if varredura.ok else 'Houve falha.'}"
    )
    resumo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print("\n".join(linhas))
    return resumo


def executar(argumentos: list[str] | None = None) -> int:
    opcoes = criar_parser().parse_args(argumentos)
    os.chdir(RAIZ)
    sys.path.insert(0, str(RAIZ / "src"))

    from observatorio_vagas.config import get_settings
    from observatorio_vagas.storage.mongodb import ConexaoMongoDB, ErroConexaoMongoDB

    agora = datetime.now()
    varredura = Varredura(dia=f"{agora:%Y-%m-%d}", inicio=agora)
    extras: list[str] = []

    if coleta_em_andamento():
        varredura.passos.append(Passo("início", 1, "já há uma coleta rodando; nada foi feito"))
        escrever_resumo(varredura, extras)
        return 1
    try:
        with ConexaoMongoDB(get_settings()) as conexao:
            conexao.banco["anuncios"].estimated_document_count()
    except ErroConexaoMongoDB:
        varredura.passos.append(
            Passo("início", 1, "o MongoDB não respondeu (túnel fechado?); nada foi feito")
        )
        escrever_resumo(varredura, extras)
        return 1

    # 1. Coleta completa: listagens inteiras, até 200 vagas NOVAS por fonte.
    if opcoes.sem_coleta:
        if opcoes.desde is None:
            print("ERRO: --sem-coleta exige --desde (início da coleta já feita)")
            return 2
        inicio_coleta = opcoes.desde
    else:
        inicio_coleta = datetime.now()
        log = DIRETORIO_LOGS / f"{varredura.dia}_semanal_coleta.log"
        codigo = rodar(
            "coleta",
            [
                "scripts/processar_lote.py",
                "--catalogo",
                str(CATALOGO),
                "--coletar",
                "--confirmar",
                "--limite-anuncios",
                str(LIMITE_ANUNCIOS_POR_FONTE),
                # Separada da diária: registro de estado próprio e todas as fontes.
                "--estado",
                NOME_ESTADO,
                "--sem-agendamento",
            ],
            log,
        )
        varredura.passos.append(Passo("coleta completa", codigo, ultimas_linhas(log), log))

    coberturas = coberturas_desde(DIRETORIO_COBERTURA, inicio_coleta)
    if not coberturas:
        varredura.passos.append(Passo("cobertura", 1, "a coleta não gerou relatório de cobertura"))
        escrever_resumo(varredura, extras)
        return 1
    lista = DIRETORIO_LOGS / f"{varredura.dia}_semanal_coberturas.txt"
    lista.write_text("\n".join(map(str, coberturas)) + "\n", encoding="utf-8")

    # "Não deixou passar": vagas que entraram no banco por esta coleta.
    with ConexaoMongoDB(get_settings()) as conexao:
        total_novas, por_fonte = vagas_novas_da_varredura(
            conexao.banco, inicio_coleta.astimezone(UTC)
        )
    extras.append(f"Vagas novas que só a varredura encontrou: {total_novas}")
    extras.extend(f"  {quantidade:>6}  {alvo}" for alvo, quantidade in por_fonte)

    # 2. Vencidas pela data de validade, no banco inteiro.
    log = DIRETORIO_LOGS / f"{varredura.dia}_semanal_expiradas.log"
    codigo = rodar(
        "expiradas", ["scripts/encerrar_anuncios_expirados.py", "--todos", "--confirmar"], log
    )
    varredura.passos.append(Passo("encerrar vencidas", codigo, ultimas_linhas(log, 2), log))

    # 3. Ainda no ar? Links das listagens + página de toda vaga publicada.
    log = DIRETORIO_LOGS / f"{varredura.dia}_semanal_conferencia.log"
    relatorio = DIRETORIO_CONFERENCIA / f"{varredura.dia}.json"
    codigo = rodar(
        "conferencia",
        [
            "scripts/conferir_vagas_removidas.py",
            "--data",
            f"{inicio_coleta:%Y-%m-%d}",
            "--verificar",
            "--verificar-publicadas",
            "--max-verificacoes",
            str(opcoes.max_paginas),
            "--confirmar",
            "--saida",
            str(relatorio),
            # Links vistos nas listagens desta varredura (não os da diária).
            "--estado",
            str(DIRETORIO_ESTADO),
            "--cobertura",
            *map(str, coberturas),
        ],
        log,
    )
    varredura.passos.append(Passo("conferir se estão no ar", codigo, ultimas_linhas(log, 4), log))

    # 4 e 5. Duplicatas entre as publicadas + remoção do Empregos.
    try:
        with ConexaoMongoDB(get_settings()) as conexao:
            no_ar, contagem, saida = remover_do_empregos(conexao.banco, varredura.dia)
        detalhes = ", ".join(f"{chave}={valor}" for chave, valor in sorted(contagem.items()))
        varredura.passos.append(
            Passo(
                "duplicatas e remoção do Empregos",
                0,
                f"{no_ar} publicações no ar; fila de remoção: {detalhes or 'vazia'}",
                saida,
            )
        )
    except Exception as erro:  # noqa: BLE001 - registra e segue para o resumo
        varredura.passos.append(Passo("duplicatas e remoção do Empregos", 1, str(erro)))

    # Aba do Compass em dia com o que mudou.
    log = DIRETORIO_LOGS / f"{varredura.dia}_semanal_republicaveis.log"
    codigo = rodar(
        "republicaveis",
        ["scripts/atualizar_vagas_republicaveis.py", "--catalogo", str(CATALOGO)],
        log,
    )
    varredura.passos.append(Passo("aba vagas_republicaveis", codigo, ultimas_linhas(log, 3), log))

    escrever_resumo(varredura, extras)
    return 0 if varredura.ok else 1


if __name__ == "__main__":
    raise SystemExit(executar())
