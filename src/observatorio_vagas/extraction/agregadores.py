"""Sites de terceiros (agregadores, portais) e nomes que são do site, não da empresa.

Em agregadores a empresa muitas vezes não aparece na página, e a leitura caía no
nome do próprio site ("Jobbrazil", "Trabalha Brasil", "eu.dev.br"). Duas regras:

1. Nome igual ao domínio, num site de terceiros, não é a empresa. Na aba
   "trabalhe conosco" da própria empresa o nome também é o domínio (Tilibra,
   tilibra.com.br) e ali está certo; por isso a regra só vale para terceiros.
2. Site com anúncios de muitas empresas diferentes é agregador, mesmo fora da
   lista fixa. A lista detectada vem de ``scripts/detectar_agregadores.py``.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

ARQUIVO_DETECTADOS = Path(__file__).resolve().parents[3] / "config" / "agregadores_detectados.csv"

# Agregadores e portais de vagas conhecidos: publicam vagas de várias empresas.
AGREGADORES_CONHECIDOS = (
    "jobijoba.com.br", "cargos.com.br", "bne.com.br", "trabalhabrasil.com.br",
    "empregandobrasil.com.br", "vagas.com.br", "infojobs.com.br", "catho.com.br",
    "indeed.com", "glassdoor.com", "talent.com", "emploive.com", "melhoresempregos.com",
    "jobbrazil.com", "programathor.com.br", "hubclubgo.com.br", "drjobpro.com",
    "sine.com.br", "empregos.com.br", "eu.dev.br", "adzuna.com.br", "buscarvagas.com.br",
    "linkedin.com",
)  # fmt: skip
_SUFIXOS = (".com.br", ".org.br", ".net.br", ".gov.br", ".edu.br", ".com", ".br", ".net", ".org")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def _no_dominio(host: str, dominios) -> bool:
    return any(host == d or host.endswith("." + d) for d in dominios)


@lru_cache(maxsize=1)
def agregadores_detectados() -> frozenset[str]:
    """Domínios marcados como agregador por terem anúncios de muitas empresas."""

    try:
        with ARQUIVO_DETECTADOS.open(encoding="utf-8", newline="") as arquivo:
            return frozenset(
                linha["dominio"].strip().casefold()
                for linha in csv.DictReader(arquivo)
                if (linha.get("dominio") or "").strip()
            )
    except OSError:
        return frozenset()


def e_agregador(url: str) -> bool:
    """O endereço é de um agregador/portal de vagas (conhecido ou detectado)."""

    host = _host(url)
    return bool(host) and (
        _no_dominio(host, AGREGADORES_CONHECIDOS) or _no_dominio(host, agregadores_detectados())
    )


def e_site_de_terceiros(url: str) -> bool:
    """Agregador, portal, consultoria ou plataforma de recrutamento (não a empresa)."""

    from observatorio_vagas.extraction.listagem_carreiras import DOMINIOS_DE_TERCEIROS

    return e_agregador(url) or _no_dominio(_host(url), DOMINIOS_DE_TERCEIROS)


def _so_letras(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.casefold())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in sem_acento if not unicodedata.combining(c)))


def nome_e_do_site(nome: str, url: str) -> bool:
    """O nome é o do próprio site: "Jobbrazil" em jobbrazil.com, "eu.dev.br" em eu.dev.br."""

    host = _host(url)
    nome_limpo = _so_letras(nome)
    if not host or len(nome_limpo) < 3:
        return False
    sem_sufixo = next((host.removesuffix(s) for s in _SUFIXOS if host.endswith(s)), host)
    formas = {
        _so_letras(host),
        _so_letras(sem_sufixo),
        _so_letras(sem_sufixo.rsplit(".", 1)[-1]),
    }
    return nome_limpo in formas
