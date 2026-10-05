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
    """Controla, por fonte, quando a coleta deve parar.

    Duas regras encerram uma fonte:
    - com ``horas``: vagas velhas seguidas (a data está na página);
    - sem data na página: a primeira vaga que já está gravada no MongoDB.
    Fontes sem data também ficam limitadas às primeiras listagens.
    """

    horas: int | None = None
    velhas_seguidas_para_encerrar: int = VELHAS_SEGUIDAS_PARA_ENCERRAR
    leituras_sem_data_para_classificar: int = LEITURAS_SEM_DATA
    listagens_sem_data: int = LISTAGENS_SEM_DATA
    _seguidas: dict[str, int] = field(default_factory=dict)
    _encerradas: set[str] = field(default_factory=set)
    _sem_data: dict[str, int] = field(default_factory=dict)
    _com_data: set[str] = field(default_factory=set)

    def registrar(
        self,
        alvo_id: str,
        data: DataPublicacao | None,
        *,
        conhecida: bool = False,
        agora: datetime | None = None,
    ) -> bool:
        """Registra uma vaga lida; devolve ``True`` se ela deve ser descartada."""

        if data is None:
            self._sem_data[alvo_id] = self._sem_data.get(alvo_id, 0) + 1
            if conhecida:
                # Sem data, a única pista de que acabaram as novas é já
                # termos esta vaga: o resto da fonte é antigo.
                self._encerradas.add(alvo_id)
                return True
            return False

        self._com_data.add(alvo_id)

        if self.horas is None:
            return False

        if not esta_velha(data, horas=self.horas, agora=agora):
            self._seguidas[alvo_id] = 0
            return False

        self._seguidas[alvo_id] = self._seguidas.get(alvo_id, 0) + 1
        if self._seguidas[alvo_id] >= self.velhas_seguidas_para_encerrar:
            self._encerradas.add(alvo_id)
        return True

    def encerrar(self, alvo_id: str) -> None:
        """Encerra a fonte por outro motivo (por exemplo, nada de útil nas primeiras páginas)."""

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
            raise IgnoreRequest("fonte encerrada: o restante já é antigo ou conhecido")

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
