"""Assinatura de conteúdo: reconhece a mesma vaga publicada por sites diferentes.

A identidade de publicação (``externalJobPostingId``) vem do id da vaga no site
de origem. A mesma vaga num agregador tem outro id e, sem esta assinatura,
seria publicada de novo em outro dia. A assinatura usa só o que vai para o
Empregos (título, empresa, local e descrição), depois de limpar o que muda de um
site para outro sem mudar a vaga: entidades HTML, acentos, caixa, pontuação,
ordem de "Cidade, UF" e o crédito da fonte no fim da descrição.

São duas assinaturas, porque "mesma vaga" depende de onde as cópias estão:

- entre sites DIFERENTES basta o começo da descrição: o fim muda de site para
  site (rodapé, "Informações adicionais", "candidate-se pelo link");
- no MESMO site a descrição inteira precisa ser igual: o site publica vagas
  diferentes com o mesmo começo (turnos, unidades, atribuições), e só uma cópia
  idêntica é republicação (ex.: employed com "-2" no endereço).
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Começo da descrição que entra na comparação entre sites diferentes.
CARACTERES_DESCRICAO = 300

# A preparação acrescenta "\n\nFonte: <site>" ao fim da descrição.
SEPARADOR_CREDITO = "\n\nFonte:"

_PALAVRAS_SEM_INFORMACAO_DE_LOCAL = frozenset({"brasil", "br", "brazil"})


@dataclass(frozen=True, slots=True)
class IdentidadeConteudo:
    """O que é preciso para decidir se duas publicações são a mesma vaga."""

    # Título, empresa, local e começo da descrição: a chave de busca.
    assinatura: str
    # Os mesmos campos com a descrição inteira: decide dentro do mesmo site.
    assinatura_completa: str
    # Site de onde a vaga veio (sem "www."); None quando desconhecido.
    dominio: str | None


def calcular_assinatura_conteudo(
    *,
    titulo: str,
    empresa: str,
    local: str,
    descricao: str,
    descricao_inteira: bool = False,
) -> str:
    """SHA-256 dos quatro campos normalizados (mesma vaga → mesma assinatura)."""

    texto = _normalizar(descricao.split(SEPARADOR_CREDITO, maxsplit=1)[0])
    partes = (
        _normalizar(titulo),
        _normalizar(empresa),
        _normalizar_local(local),
        texto if descricao_inteira else texto[:CARACTERES_DESCRICAO],
    )
    return hashlib.sha256("\x1f".join(partes).encode()).hexdigest()


def identidade_de_payload(
    payload: Mapping[str, Any] | None,
    *,
    dominio: str | None,
) -> IdentidadeConteudo | None:
    """Identidade de um payload do Empregos; None se faltar algum dos campos."""

    campos = _campos_do_payload(payload)
    if campos is None:
        return None
    return IdentidadeConteudo(
        assinatura=calcular_assinatura_conteudo(**campos),
        assinatura_completa=calcular_assinatura_conteudo(**campos, descricao_inteira=True),
        dominio=normalizar_dominio(dominio),
    )


def assinatura_de_payload(payload: Mapping[str, Any] | None) -> str | None:
    """Só a chave de busca (começo da descrição) de um payload."""

    campos = _campos_do_payload(payload)
    return calcular_assinatura_conteudo(**campos) if campos is not None else None


def mesma_vaga(
    *,
    dominio_a: str | None,
    assinatura_completa_a: str | None,
    dominio_b: str | None,
    assinatura_completa_b: str | None,
) -> bool:
    """Duas publicações com a mesma ``assinatura`` são a mesma vaga?

    Sites diferentes (ou site desconhecido): sim. Mesmo site: só se a descrição
    inteira também for igual.
    """

    a = normalizar_dominio(dominio_a)
    b = normalizar_dominio(dominio_b)
    if a is None or b is None or a != b:
        return True
    return assinatura_completa_a is not None and assinatura_completa_a == assinatura_completa_b


def normalizar_dominio(dominio: str | None) -> str | None:
    if not isinstance(dominio, str) or not dominio.strip():
        return None
    return dominio.strip().casefold().removeprefix("www.")


def _campos_do_payload(payload: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(payload, Mapping):
        return None
    empresa = payload.get("company")
    local = payload.get("location")
    titulo = payload.get("title")
    descricao = payload.get("description")
    if not isinstance(empresa, Mapping) or not isinstance(local, Mapping):
        return None
    nome = empresa.get("name")
    endereco = local.get("address")
    if not all(isinstance(valor, str) and valor.strip() for valor in (titulo, nome, endereco)):
        return None
    if not isinstance(descricao, str):
        return None
    return {"titulo": titulo, "empresa": nome, "local": endereco, "descricao": descricao}


def _normalizar(texto: str) -> str:
    """Sem HTML, acento, caixa e pontuação; espaços simples.

    "+" e "#" ficam: "C++" e "C#" são cargos diferentes.
    """

    # Duas passadas: "&amp;#8211;" vira "&#8211;" e depois "–".
    decodificado = html.unescape(html.unescape(texto))
    sem_acento = unicodedata.normalize("NFKD", decodificado)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w+#]+", " ", sem_acento.casefold()).split())


def _normalizar_local(local: str) -> str:
    """Ordem e pontuação não contam: "São Paulo, SP" = "SP - Sao Paulo/Brasil"."""

    palavras = set(_normalizar(local).split()) - _PALAVRAS_SEM_INFORMACAO_DE_LOCAL
    return " ".join(sorted(palavras))
