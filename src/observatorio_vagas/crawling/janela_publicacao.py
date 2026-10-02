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
from typing import TYPE_CHECKING, Any, Self
from zoneinfo import ZoneInfo

from scrapy.exceptions import IgnoreRequest

if TYPE_CHECKING:
    from scrapy import Request, Spider
    from scrapy.crawler import Crawler
    from scrapy.http import Response

FUSO = ZoneInfo("America/Sao_Paulo")

# Vagas antigas seguidas necessárias para encerrar a fonte. Um único destaque
# ou vaga fixada no meio da listagem não pode encerrar a coleta sozinho.
VELHAS_SEGUIDAS_PARA_ENCERRAR = 3

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
    except (AttributeError, NotImplementedError):
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
    """Controla, por fonte, quando as vagas velhas encerram a coleta."""

    horas: int
    velhas_seguidas_para_encerrar: int = VELHAS_SEGUIDAS_PARA_ENCERRAR
    _seguidas: dict[str, int] = field(default_factory=dict)
    _encerradas: set[str] = field(default_factory=set)

    def registrar(
        self, alvo_id: str, data: DataPublicacao | None, *, agora: datetime | None = None
    ) -> bool:
        """Registra uma vaga lida; devolve ``True`` se ela é velha."""

        if data is None:
            return False

        if not esta_velha(data, horas=self.horas, agora=agora):
            self._seguidas[alvo_id] = 0
            return False

        self._seguidas[alvo_id] = self._seguidas.get(alvo_id, 0) + 1
        if self._seguidas[alvo_id] >= self.velhas_seguidas_para_encerrar:
            self._encerradas.add(alvo_id)
        return True

    def encerrada(self, alvo_id: str) -> bool:
        return alvo_id in self._encerradas

    @property
    def fontes_encerradas(self) -> frozenset[str]:
        return frozenset(self._encerradas)


class EncerramentoPorIdadeDownloaderMiddleware:
    """Descarta o que sobrou na fila de uma fonte já encerrada pela janela."""

    def __init__(self, crawler: Crawler) -> None:
        self._crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        return cls(crawler)

    def process_request(self, request: Request, spider: Spider | None = None) -> None:
        spider = spider or getattr(self._crawler, "spider", None)
        janela: JanelaPublicacao | None = getattr(spider, "janela_publicacao", None)
        alvo_id = request.meta.get("observatorio_alvo_id")

        if janela is not None and isinstance(alvo_id, str) and janela.encerrada(alvo_id):
            self._crawler.stats.inc_value("observatorio/janela/requisicoes_descartadas")
            raise IgnoreRequest("fonte encerrada: vagas fora da janela de publicação")
