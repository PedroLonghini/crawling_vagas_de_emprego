"""Cache da análise de páginas que voltam iguais entre coletas.

A parte cara da extração é passar cada página por todos os extratores. Uma
página de vaga costuma voltar idêntica dia após dia; quando o conteúdo e o
contexto são os mesmos, a análise também é, e pode ser reaproveitada.

A chave inclui uma assinatura do código dos extratores. Qualquer mudança
nesse código gera chaves novas, então análises antigas deixam de ser usadas
sem que ninguém precise lembrar de limpar o cache.

O arquivo é um SQLite local. Vários processos podem ler ao mesmo tempo; as
gravações de cada pedaço são feitas numa única transação.
"""

from __future__ import annotations

import pickle
import sqlite3
from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from observatorio_vagas.domain.enums import Fonte

if TYPE_CHECKING:
    from observatorio_vagas.crawling.inventario import RegistroInventarioBruto
    from observatorio_vagas.extraction.processador import AnalisePagina

_PACOTE = Path(__file__).resolve().parents[1]

# Código que decide o resultado da análise de uma página.
_CODIGO_DOS_EXTRATORES = (
    _PACOTE / "extraction",
    _PACOTE / "crawling" / "filtro_conteudo.py",
    _PACOTE / "crawling" / "adaptadores" / "agregadores.py",
    _PACOTE / "domain",
    _PACOTE / "importacao",
)


@cache
def assinatura_dos_extratores() -> str:
    """Hash do código que produz a análise; muda quando o código muda."""

    resumo = sha256()

    for caminho in _CODIGO_DOS_EXTRATORES:
        arquivos = sorted(caminho.rglob("*.py")) if caminho.is_dir() else [caminho]

        for arquivo in arquivos:
            resumo.update(arquivo.relative_to(_PACOTE).as_posix().encode())
            resumo.update(arquivo.read_bytes())

    return resumo.hexdigest()


def chave_da_pagina(registro: RegistroInventarioBruto) -> str:
    """Tudo o que a análise de uma página lê, além do código."""

    partes = [
        assinatura_dos_extratores(),
        registro.hash_conteudo,
        registro.fonte,
        registro.tipo_pagina,
        registro.url_solicitada,
        registro.url_final,
        registro.tipo_conteudo or "",
        registro.codificacao or "",
        registro.empresa_nome or "",
    ]

    # O extrator CKAN usa a data da coleta como referência.
    if registro.fonte == Fonte.CKAN.value:
        partes.append(registro.coletado_em.date().isoformat())

    return sha256("\x1f".join(partes).encode("utf-8")).hexdigest()


class CachePaginas:
    """Guarda análises de páginas num arquivo SQLite."""

    def __init__(self, caminho: Path) -> None:
        self._caminho = caminho
        self._conexao: sqlite3.Connection | None = None
        self._pendentes: list[tuple[str, bytes]] = []
        self.acertos = 0
        self.faltas = 0

    def _abrir(self) -> sqlite3.Connection:
        if self._conexao is None:
            self._caminho.parent.mkdir(parents=True, exist_ok=True)
            conexao = sqlite3.connect(self._caminho, timeout=60)
            # WAL permite leitores enquanto outro processo grava.
            conexao.execute("PRAGMA journal_mode=WAL")
            conexao.execute("PRAGMA synchronous=NORMAL")
            conexao.execute(
                "CREATE TABLE IF NOT EXISTS analises (chave TEXT PRIMARY KEY, valor BLOB NOT NULL)"
            )
            self._conexao = conexao

        return self._conexao

    def obter(self, registro: RegistroInventarioBruto) -> AnalisePagina | None:
        """Devolve a análise guardada ou None. O cache nunca derruba a extração."""

        try:
            linha = (
                self._abrir()
                .execute("SELECT valor FROM analises WHERE chave = ?", (chave_da_pagina(registro),))
                .fetchone()
            )
        except sqlite3.Error:
            self.faltas += 1
            return None

        if linha is None:
            self.faltas += 1
            return None

        try:
            analise = pickle.loads(linha[0])
        except Exception:
            # Entrada ilegível (ex.: classe alterada): refaz a análise.
            self.faltas += 1
            return None

        self.acertos += 1
        return analise

    def guardar(self, registro: RegistroInventarioBruto, analise: AnalisePagina) -> None:
        """Agenda a análise para gravação em ``gravar``."""

        try:
            valor = pickle.dumps(analise, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            # Algo não serializável: só não entra no cache.
            return

        self._pendentes.append((chave_da_pagina(registro), valor))

    def gravar(self) -> None:
        """Grava as análises novas numa única transação."""

        if not self._pendentes:
            return

        try:
            conexao = self._abrir()
            with conexao:
                conexao.executemany(
                    "INSERT OR REPLACE INTO analises (chave, valor) VALUES (?, ?)",
                    self._pendentes,
                )
        except sqlite3.Error:
            # Ex.: "database is locked" com vários processos. Só perde o cache.
            pass

        self._pendentes.clear()

    def fechar(self) -> None:
        """Grava o que falta e fecha o arquivo."""

        try:
            self.gravar()
        finally:
            if self._conexao is not None:
                self._conexao.close()
                self._conexao = None
