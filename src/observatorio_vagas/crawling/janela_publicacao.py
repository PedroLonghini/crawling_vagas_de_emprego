"""Janela de publicação: a coleta de uma fonte para quando as vagas ficam velhas.

Listagens de vagas costumam vir da mais nova para a mais antiga. Quando as
últimas vagas lidas de uma fonte já passaram da janela (padrão: 24 horas), as
demais também passaram, e continuar só gasta requisições. A fonte então é
encerrada e o crawler segue para as outras.

Fontes cujas páginas não informam a data de publicação nunca são encerradas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from zoneinfo import ZoneInfo

from scrapy.exceptions import IgnoreRequest, NotSupported

if TYPE_CHECKING:
    from scrapy import Request, Spider
    from scrapy.crawler import Crawler
    from scrapy.http import Response

FUSO = ZoneInfo("America/Sao_Paulo")

# Vagas antigas seguidas necessárias para encerrar a fonte. Um único destaque
# ou vaga fixada no meio da listagem não pode encerrar a coleta sozinho.
VELHAS_SEGUIDAS_PARA_ENCERRAR = 3

# Fonte cujas 3 primeiras vagas lidas não trazem data é tratada como "sem data"
# e lê apenas as 3 primeiras páginas de listagem (as mais novas).
LEITURAS_SEM_DATA = 3
LISTAGENS_SEM_DATA = 3
# Primeiras posições de uma listagem que podem ser destaques fixos (velhos).
POSICOES_DE_DESTAQUE = 3

_ISO_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_BR_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

_CAMPOS_JSON_LD = ("datePosted", "datePublished")
_SELETORES_META = (
    "meta[property='article:published_time']::attr(content)",
    "meta[property='og:article:published_time']::attr(content)",
    "meta[itemprop='datePosted']::attr(content)",
    "meta[itemprop='datePublished']::attr(content)",
    "[itemprop='datePosted']::attr(datetime)",
    "[itemprop='datePublished']::attr(datetime)",
)


@dataclass(frozen=True, slots=True)
class DataPublicacao:
    """Data lida da página; ``com_hora`` diz se o horário é confiável."""

    momento: datetime
    com_hora: bool


def interpretar_data(texto: str) -> DataPublicacao | None:
    """Converte ISO 8601 ou dd/mm/aaaa; sem fuso, vale o de Brasília."""

    texto = texto.strip()
    if not texto:
        return None

    br = _BR_DATA.fullmatch(texto)
    if br:
        try:
            dia = date(int(br[3]), int(br[2]), int(br[1]))
        except ValueError:
            return None
        return DataPublicacao(datetime(dia.year, dia.month, dia.day, tzinfo=FUSO), False)

    if not _ISO_DATA.match(texto):
        return None

    apenas_data = _ISO_DATA.fullmatch(texto) is not None
    try:
        momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=FUSO)

    return DataPublicacao(momento, not apenas_data)


def _valores_json_ld(corpo: Any) -> list[str]:
    encontrados: list[str] = []
    if isinstance(corpo, dict):
        for campo in _CAMPOS_JSON_LD:
            valor = corpo.get(campo)
            if isinstance(valor, str):
                encontrados.append(valor)
        for filho in corpo.values():
            if isinstance(filho, dict | list):
                encontrados.extend(_valores_json_ld(filho))
    elif isinstance(corpo, list):
        for item in corpo:
            encontrados.extend(_valores_json_ld(item))
    return encontrados


def extrair_data_publicacao(response: Response) -> DataPublicacao | None:
    """Lê a data de publicação do JSON-LD ou das metatags, sem baixar nada."""

    try:
        blocos = response.css("script[type='application/ld+json']::text").getall()
    except (AttributeError, NotImplementedError, NotSupported):
        return None

    for bloco in blocos:
        try:
            dados = json.loads(bloco)
        except ValueError:
            continue
        for valor in _valores_json_ld(dados):
            data = interpretar_data(valor)
            if data is not None:
                return data

    for seletor in _SELETORES_META:
        valor = response.css(seletor).get()
        if valor:
            data = interpretar_data(valor)
            if data is not None:
                return data

    return None


def esta_velha(
    data: DataPublicacao,
    *,
    horas: int,
    agora: datetime | None = None,
) -> bool:
    """Passou da janela? Data sem hora só conta como velha por dia inteiro."""

    limite = (agora or datetime.now(UTC)) - timedelta(hours=horas)

    if data.com_hora:
        return data.momento < limite

    # Sem horário, o dia da vaga pode ainda estar dentro da janela.
    return data.momento.astimezone(FUSO).date() < limite.astimezone(FUSO).date()


@dataclass(slots=True)
class JanelaPublicacao:
    """Decide o que ainda vale baixar de cada fonte.

    - Vaga velha (fora da janela) ou já gravada é descartada uma a uma.
    - Uma LISTAGEM fica esgotada quando 3 vagas seguidas, na ordem em que aparecem
      nela, são velhas: o resto daquela listagem (e as páginas seguintes dela) é mais
      antigo. Antes isso encerrava a FONTE inteira pela ordem de chegada das
      respostas, que é concorrente, e jogava fora vagas novas de outras listagens
      (perda medida na rodada de 05/10/2026: 12 fontes, ~500 URLs com cara de vaga).
    - A fonte inteira só é encerrada por ``encerrar`` (nenhum link de vaga nas
      primeiras páginas).
    - Fontes sem data ficam limitadas às primeiras listagens.
    """

    horas: int | None = None
    velhas_seguidas_para_encerrar: int = VELHAS_SEGUIDAS_PARA_ENCERRAR
    leituras_sem_data_para_classificar: int = LEITURAS_SEM_DATA
    listagens_sem_data: int = LISTAGENS_SEM_DATA
    _encerradas: set[str] = field(default_factory=set)
    _sem_data: dict[str, int] = field(default_factory=dict)
    _com_data: set[str] = field(default_factory=set)
    _velhas_por_lista: dict[str, dict[int, bool]] = field(default_factory=dict)
    _esgotada_desde: dict[str, int] = field(default_factory=dict)

    def registrar(
        self,
        alvo_id: str,
        data: DataPublicacao | None,
        *,
        conhecida: bool = False,
        agora: datetime | None = None,
        lista: str | None = None,
        posicao: int | None = None,
    ) -> bool:
        """Registra uma vaga lida; devolve ``True`` se ela deve ser descartada."""

        if data is None:
            self._sem_data[alvo_id] = self._sem_data.get(alvo_id, 0) + 1
            return conhecida

        self._com_data.add(alvo_id)

        if self.horas is None:
            return conhecida

        velha = esta_velha(data, horas=self.horas, agora=agora)
        if lista is not None and posicao is not None:
            self._registrar_na_lista(lista, posicao, velha)
        return velha or conhecida

    def _registrar_na_lista(self, lista: str, posicao: int, velha: bool) -> None:
        if lista in self._esgotada_desde:
            return
        resultados = self._velhas_por_lista.setdefault(lista, {})
        resultados[posicao] = velha
        seguidas = self.velhas_seguidas_para_encerrar
        posicoes = sorted(resultados)
        for indice, inicio in enumerate(posicoes):
            trecho = posicoes[indice : indice + seguidas]
            if len(trecho) < seguidas or trecho != list(range(inicio, inicio + seguidas)):
                continue
            if not all(resultados[p] for p in trecho):
                continue
            # Destaques fixos no topo costumam ser velhos: o trecho só vale a partir
            # da 4ª posição, ou depois de uma vaga nova já vista nesta listagem.
            if inicio < POSICOES_DE_DESTAQUE and all(resultados[p] for p in posicoes if p < inicio):
                continue
            # Uma vaga nova DEPOIS do trecho indica que a lista não está em ordem
            # de data; nesse caso não esgotamos nada.
            if any(not resultados[p] for p in posicoes if p > inicio):
                continue
            self._esgotada_desde[lista] = inicio + seguidas
            return

    def limite_da_lista(self, lista: str | None) -> int | None:
        """Primeira posição que não vale mais baixar nesta listagem (ou ``None``)."""

        return self._esgotada_desde.get(lista) if lista else None

    def lista_esgotada(self, lista: str | None) -> bool:
        return bool(lista) and lista in self._esgotada_desde

    def encerrar(self, alvo_id: str) -> None:
        """Encerra a fonte inteira (por exemplo, nada de útil nas primeiras páginas)."""

        self._encerradas.add(alvo_id)

    def sem_data(self, alvo_id: str) -> bool:
        """A fonte nunca mostrou data nas primeiras vagas lidas?"""

        return (
            alvo_id not in self._com_data
            and self._sem_data.get(alvo_id, 0) >= self.leituras_sem_data_para_classificar
        )

    def encerrada(self, alvo_id: str) -> bool:
        return alvo_id in self._encerradas

    @property
    def fontes_encerradas(self) -> frozenset[str]:
        return frozenset(self._encerradas)


def carregar_urls_conhecidas(caminho: Path | None) -> frozenset[str]:
    """Lê o arquivo (uma URL por linha) com as vagas já gravadas no MongoDB."""

    if caminho is None or not caminho.exists():
        return frozenset()

    return frozenset(
        linha.strip() for linha in caminho.read_text(encoding="utf-8").splitlines() if linha.strip()
    )


class EncerramentoPorIdadeDownloaderMiddleware:
    """Descarta o que sobrou na fila de uma fonte já encerrada ou saturada."""

    def __init__(self, crawler: Crawler) -> None:
        self._crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        return cls(crawler)

    def process_request(self, request: Request, spider: Spider | None = None) -> None:
        spider = spider or getattr(self._crawler, "spider", None)
        janela: JanelaPublicacao | None = getattr(spider, "janela_publicacao", None)
        alvo_id = request.meta.get("observatorio_alvo_id")

        if janela is None or not isinstance(alvo_id, str):
            return None

        if janela.encerrada(alvo_id):
            self._crawler.stats.inc_value("observatorio/janela/requisicoes_descartadas")
            raise IgnoreRequest("fonte encerrada: nenhum link de vaga nas primeiras páginas")

        limite = janela.limite_da_lista(request.meta.get("observatorio_lista"))
        posicao = request.meta.get("observatorio_posicao")
        if limite is not None and isinstance(posicao, int) and posicao >= limite:
            self._crawler.stats.inc_value("observatorio/janela/requisicoes_descartadas")
            raise IgnoreRequest("listagem esgotada: as vagas seguintes são mais antigas")
        if janela.lista_esgotada(request.meta.get("observatorio_lista_origem")):
            self._crawler.stats.inc_value("observatorio/janela/requisicoes_descartadas")
            raise IgnoreRequest("página seguinte de uma listagem esgotada")

        numero_pagina = request.meta.get("observatorio_numero_pagina")
        if (
            janela.sem_data(alvo_id)
            and request.meta.get("observatorio_tipo_pagina") == "inicial"
            and isinstance(numero_pagina, int)
            and numero_pagina > janela.listagens_sem_data
        ):
            self._crawler.stats.inc_value("observatorio/janela/listagens_sem_data_descartadas")
            raise IgnoreRequest("fonte sem data: só as primeiras listagens são lidas")

        return None
