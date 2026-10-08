"""Leitura do estado JSON que aplicações JavaScript embutem no HTML.

Nada da página é executado. Os formatos aceitos são:

- ``<script id="__NEXT_DATA__" type="application/json">{...}</script>`` (Next.js);
- ``window.__INITIAL_STATE__ = {...}`` e atribuições parecidas com JSON puro;
- ``window.__NUXT__=(function(a,b,...){return {...}}(1,"x",...))`` (Nuxt 2),
  que não é JSON: é um literal JavaScript com variáveis no lugar de valores
  repetidos. Um leitor pequeno entende esse subconjunto da linguagem.

Qualquer coisa fora desse subconjunto devolve ``None``; quem chama registra
no diagnóstico que a camada existia mas não pôde ser lida.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Nomes comuns de estado embutido.
NOMES_ESTADO = (
    "__NUXT__",
    "__NEXT_DATA__",
    "__INITIAL_STATE__",
    "__APOLLO_STATE__",
    "__PRELOADED_STATE__",
)

_ATRIBUICAO = re.compile(
    r"window\.(?P<nome>__[A-Z_]+__)\s*=\s*",
)
_NEXT_DATA = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(?P<json>.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


class _ErroLiteral(ValueError):
    """O trecho não pertence ao subconjunto de JavaScript aceito."""


class _LeitorLiteral:
    """Lê literais JavaScript: objetos, listas, textos, números e nomes."""

    _NUMERO = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
    _NOME = re.compile(r"[A-Za-z_$][\w$]*")

    def __init__(self, texto: str, variaveis: dict[str, Any] | None = None) -> None:
        self.texto = texto
        self.posicao = 0
        self.variaveis = variaveis or {}

    def _espacos(self) -> None:
        texto = self.texto
        while self.posicao < len(texto) and texto[self.posicao] in " \t\r\n":
            self.posicao += 1

    def _ver(self) -> str:
        self._espacos()
        return self.texto[self.posicao : self.posicao + 1]

    def _esperar(self, caractere: str) -> None:
        if self._ver() != caractere:
            raise _ErroLiteral(f"esperado {caractere!r} na posição {self.posicao}")
        self.posicao += 1

    def valor(self) -> Any:
        caractere = self._ver()

        if caractere == "{":
            return self._objeto()
        if caractere == "[":
            return self._lista()
        if caractere in "\"'":
            return self._texto()
        if caractere == "-" or caractere.isdigit() or caractere == ".":
            correspondencia = self._NUMERO.match(self.texto, self.posicao)
            if not correspondencia:
                raise _ErroLiteral(f"número inválido na posição {self.posicao}")
            self.posicao = correspondencia.end()
            numero = correspondencia.group()
            return float(numero) if any(c in numero for c in ".eE") else int(numero)

        correspondencia = self._NOME.match(self.texto, self.posicao)
        if not correspondencia:
            raise _ErroLiteral(f"valor inesperado na posição {self.posicao}")

        nome = correspondencia.group()
        self.posicao = correspondencia.end()

        if nome == "true":
            return True
        if nome == "false":
            return False
        if nome in {"null", "undefined"}:
            return None
        if nome == "void":
            self.valor()
            return None
        if nome in self.variaveis:
            return self.variaveis[nome]

        raise _ErroLiteral(f"nome desconhecido {nome!r}")

    def _objeto(self) -> dict[str, Any]:
        self._esperar("{")
        resultado: dict[str, Any] = {}

        while True:
            if self._ver() == "}":
                self.posicao += 1
                return resultado

            if self._ver() in "\"'":
                chave = self._texto()
            else:
                correspondencia = self._NOME.match(self.texto, self.posicao) or (
                    self._NUMERO.match(self.texto, self.posicao)
                )
                if not correspondencia:
                    raise _ErroLiteral(f"chave inválida na posição {self.posicao}")
                chave = correspondencia.group()
                self.posicao = correspondencia.end()

            self._esperar(":")
            resultado[chave] = self.valor()

            if self._ver() == ",":
                self.posicao += 1

    def _lista(self) -> list[Any]:
        self._esperar("[")
        resultado: list[Any] = []

        while True:
            if self._ver() == "]":
                self.posicao += 1
                return resultado

            resultado.append(self.valor())

            if self._ver() == ",":
                self.posicao += 1

    def _texto(self) -> str:
        aspas = self.texto[self.posicao]
        self.posicao += 1
        partes: list[str] = []
        texto = self.texto

        while self.posicao < len(texto):
            caractere = texto[self.posicao]

            if caractere == aspas:
                self.posicao += 1
                return "".join(partes)

            if caractere == "\\":
                proximo = texto[self.posicao + 1 : self.posicao + 2]
                if proximo == "u":
                    partes.append(chr(int(texto[self.posicao + 2 : self.posicao + 6], 16)))
                    self.posicao += 6
                    continue
                if proximo == "x":
                    partes.append(chr(int(texto[self.posicao + 2 : self.posicao + 4], 16)))
                    self.posicao += 4
                    continue
                partes.append(
                    {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        "b": "\b",
                        "f": "\f",
                        "v": "\v",
                        "0": "\0",
                    }.get(proximo, proximo)
                )
                self.posicao += 2
                continue

            partes.append(caractere)
            self.posicao += 1

        raise _ErroLiteral("texto sem fechamento")


def _ler_nuxt2(trecho: str) -> Any:
    """Lê ``(function(a,b){return {...}}(1,2))``."""

    cabecalho = re.match(r"\(\s*function\s*\((?P<parametros>[^)]*)\)\s*\{\s*return\s*", trecho)
    if not cabecalho:
        raise _ErroLiteral("não é uma função Nuxt 2")

    parametros = [nome.strip() for nome in cabecalho.group("parametros").split(",") if nome.strip()]

    # Primeiro lê o objeto sem resolver as variáveis, só para achar onde ele
    # termina e onde começam os argumentos.
    marcador = object()
    leitor = _LeitorLiteral(trecho, {nome: marcador for nome in parametros})
    leitor.posicao = cabecalho.end()
    leitor.valor()
    fim_objeto = leitor.posicao

    resto = trecho[fim_objeto:]
    abertura = re.match(r"\s*;?\s*\}\s*\(", resto)
    if not abertura:
        raise _ErroLiteral("argumentos da função não encontrados")

    leitor_argumentos = _LeitorLiteral(trecho)
    leitor_argumentos.posicao = fim_objeto + abertura.end()
    argumentos: list[Any] = []

    while leitor_argumentos._ver() != ")":
        argumentos.append(leitor_argumentos.valor())
        if leitor_argumentos._ver() == ",":
            leitor_argumentos.posicao += 1

    variaveis = dict(zip(parametros, argumentos, strict=False))
    # Parâmetros sem argumento valem undefined.
    for nome in parametros[len(argumentos) :]:
        variaveis[nome] = None

    leitor_final = _LeitorLiteral(trecho, variaveis)
    leitor_final.posicao = cabecalho.end()
    return leitor_final.valor()


def extrair_estados_embutidos(html: str) -> tuple[dict[str, Any], list[str]]:
    """Devolve os estados lidos e os nomes encontrados que não puderam ser lidos."""

    estados: dict[str, Any] = {}
    ilegiveis: list[str] = []

    correspondencia = _NEXT_DATA.search(html)
    if correspondencia:
        try:
            estados["__NEXT_DATA__"] = json.loads(correspondencia.group("json"))
        except json.JSONDecodeError:
            ilegiveis.append("__NEXT_DATA__")

    for atribuicao in _ATRIBUICAO.finditer(html):
        nome = atribuicao.group("nome")
        if nome in estados:
            continue

        trecho = html[atribuicao.end() :]
        # Limita ao script atual.
        fim_script = trecho.find("</script>")
        if fim_script != -1:
            trecho = trecho[:fim_script]

        try:
            if trecho.lstrip().startswith("(function"):
                estados[nome] = _ler_nuxt2(trecho.lstrip())
            else:
                estados[nome] = _LeitorLiteral(trecho).valor()
        except (_ErroLiteral, ValueError, IndexError, RecursionError):
            ilegiveis.append(nome)

    return estados, ilegiveis
