"""Ritmo de requisições de cada site, lido de ``config/ritmo_sites.csv``.

O arquivo é a única fonte do ritmo: para mudar quantas requisições um site
recebe, edite a linha dele (ou use ``scripts/sonda_ritmo_site.py``). A linha
``*`` vale para todo site que não aparece na lista.

O ritmo máximo de um site é ``1 / intervalo_segundos`` requisições por
segundo: o intervalo é a espera entre o início de uma requisição e a da
seguinte, e ``requisicoes_simultaneas`` só limita quantas ficam em andamento
ao mesmo tempo. Se o servidor responder devagar, o ritmo real é menor.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from scrapy import Request, Spider

PADRAO = "*"
VARIAVEL_ARQUIVO = "OBSERVATORIO_RITMO_SITES"
RAIZ_PROJETO = Path(__file__).resolve().parents[3]
ARQUIVO_PADRAO = RAIZ_PROJETO / "config" / "ritmo_sites.csv"

MAXIMO_SIMULTANEAS = 8
INTERVALO_MINIMO = 0.1


@dataclass(frozen=True, slots=True)
class RitmoSite:
    dominio: str
    simultaneas: int
    intervalo: float
    observacao: str = ""

    @property
    def requisicoes_por_segundo(self) -> float:
        return 1 / self.intervalo

    def para_scrapy(self) -> dict[str, Any]:
        return {
            "concurrency": self.simultaneas,
            "delay": self.intervalo,
            "randomize_delay": True,
        }


@dataclass(slots=True)
class RitmoSites:
    padrao: RitmoSite = field(default_factory=lambda: RitmoSite(PADRAO, 2, 0.5))
    por_dominio: dict[str, RitmoSite] = field(default_factory=dict)
    ignoradas: list[str] = field(default_factory=list)

    def slots_scrapy(self) -> dict[str, dict[str, Any]]:
        return {dominio: ritmo.para_scrapy() for dominio, ritmo in self.por_dominio.items()}

    def do_host(self, host: str) -> RitmoSite | None:
        """Ritmo configurado para o host (``www.`` e subdomínios incluídos)."""

        host = host.casefold()
        while host:
            if host in self.por_dominio:
                return self.por_dominio[host]
            host = host.partition(".")[2]
        return None


def normalizar_dominio(texto: str) -> str:
    texto = texto.strip().casefold()
    if "//" in texto:
        texto = urlsplit(texto).hostname or texto
    return texto.removeprefix("www.").strip("/")


def _linha(linha: dict[str, str]) -> RitmoSite:
    dominio = linha["dominio"].strip()
    dominio = PADRAO if dominio == PADRAO else normalizar_dominio(dominio)
    simultaneas = int(linha["requisicoes_simultaneas"])
    intervalo = float(linha["intervalo_segundos"].replace(",", "."))
    if not 1 <= simultaneas <= MAXIMO_SIMULTANEAS:
        raise ValueError(f"requisicoes_simultaneas deve estar entre 1 e {MAXIMO_SIMULTANEAS}")
    if intervalo < INTERVALO_MINIMO:
        raise ValueError(f"intervalo_segundos deve ser pelo menos {INTERVALO_MINIMO}")
    if not dominio:
        raise ValueError("dominio vazio")
    return RitmoSite(dominio, simultaneas, intervalo, (linha.get("observacao") or "").strip())


def caminho_do_arquivo() -> Path:
    return Path(os.environ.get(VARIAVEL_ARQUIVO) or ARQUIVO_PADRAO)


def carregar_ritmo(caminho: Path | None = None) -> RitmoSites:
    """Lê o CSV; linha inválida é ignorada e anotada, nunca derruba a coleta."""

    caminho = caminho or caminho_do_arquivo()
    ritmo = RitmoSites()

    if not caminho.exists():
        ritmo.ignoradas.append(f"arquivo não encontrado: {caminho}")
        return ritmo

    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        linhas = (
            registro
            for registro in csv.DictReader(
                linha for linha in arquivo if linha.strip() and not linha.lstrip().startswith("#")
            )
        )
        for numero, registro in enumerate(linhas, start=2):
            try:
                site = _linha(registro)
            except (KeyError, ValueError, AttributeError) as erro:
                ritmo.ignoradas.append(f"linha {numero}: {erro}")
                continue
            if site.dominio == PADRAO:
                ritmo.padrao = site
            else:
                ritmo.por_dominio[site.dominio] = site

    return ritmo


class RitmoPorSiteDownloaderMiddleware:
    """Aplica o ritmo do CSV: um slot por site e sem ajuste do AutoThrottle.

    Sem isto o AutoThrottle trocaria o intervalo escolhido por outro calculado
    pela latência, e ``www.site`` e ``site`` virariam slots separados (o dobro
    do ritmo permitido).
    """

    def __init__(self, ritmo: RitmoSites) -> None:
        self._ritmo = ritmo

    @classmethod
    def from_crawler(cls, crawler: Any) -> RitmoPorSiteDownloaderMiddleware:
        return cls(carregar_ritmo())

    def process_request(self, request: Request, spider: Spider | None = None) -> None:
        site = self._ritmo.do_host(urlsplit(request.url).hostname or "")
        if site is not None:
            request.meta["download_slot"] = site.dominio
            request.meta["autothrottle_dont_adjust_delay"] = True
        return None


def definir_ritmo(
    dominio: str,
    simultaneas: int,
    intervalo: float,
    observacao: str = "",
    caminho: Path | None = None,
) -> RitmoSite:
    """Grava (ou troca) a linha do site no CSV, preservando os comentários."""

    caminho = caminho or caminho_do_arquivo()
    dominio = PADRAO if dominio.strip() == PADRAO else normalizar_dominio(dominio)
    novo = _linha(
        {
            "dominio": dominio,
            "requisicoes_simultaneas": str(simultaneas),
            "intervalo_segundos": str(intervalo),
            "observacao": observacao,
        }
    )
    nota = novo.observacao.replace(",", ";")

    linhas = caminho.read_text(encoding="utf-8-sig").splitlines() if caminho.exists() else []
    for posicao, atual in enumerate(linhas):
        primeiro = atual.split(",", 1)[0].strip()
        if not primeiro or primeiro.startswith("#") or primeiro == "dominio":
            continue
        normal = PADRAO if primeiro == PADRAO else normalizar_dominio(primeiro)
        if normal != novo.dominio:
            continue
        if not observacao:
            # Mantém a observação que já existia.
            partes = atual.split(",", 3)
            nota = partes[3] if len(partes) > 3 else ""
        linhas[posicao] = f"{novo.dominio},{novo.simultaneas},{novo.intervalo:g},{nota}"
        break
    else:
        linhas.append(f"{novo.dominio},{novo.simultaneas},{novo.intervalo:g},{nota}")

    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return novo
