"""Importa uma lista simples de URLs para o catálogo de fontes.

O arquivo de entrada possui somente uma URL por linha. Metadados que não podem
ser provados pela URL, principalmente licença e permissão de republicação,
recebem valores seguros e precisam ser revisados posteriormente.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import SplitResult, urlsplit, urlunsplit

from observatorio_vagas.crawling.catalog import (
    LIMITE_PAGINAS_PADRAO,
    carregar_alvos_csv,
)
from observatorio_vagas.domain.enums import Fonte
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio

RAIZ_PROJETO = Path(__file__).resolve().parents[1]

COLUNAS_CATALOGO = (
    "alvo_id",
    "empresa_nome",
    "fonte",
    "url_inicial",
    "ativa",
    "limite_paginas",
    "status_politica",
    "licenca_nome",
    "licenca_url",
    "atribuicao_obrigatoria",
    "republicacao_permitida",
    "autorizacao_escrita",
    "referencia_autorizacao",
)

PALAVRAS_PAGINA_CARREIRAS = frozenset(
    {
        "career",
        "careers",
        "carreira",
        "carreiras",
        "emprego",
        "empregos",
        "job",
        "jobs",
        "oportunidade",
        "oportunidades",
        "trabalhe-conosco",
        "trabalheconosco",
        "vaga",
        "vagas",
    }
)

PLATAFORMAS_COM_TENANT_NO_DOMINIO = (
    "abler.com.br",
    "gupy.io",
    "pandape.com.br",
    "tweezer.jobs",
)


@dataclass(frozen=True, slots=True)
class FalhaURL:
    """URL rejeitada sem impedir a importação das demais linhas."""

    numero_linha: int
    texto: str
    mensagem: str


@dataclass(frozen=True, slots=True)
class ResultadoImportacao:
    """Resumo completo de uma importação ou simulação."""

    existentes: int
    adicionadas: int
    repetidas: int
    falhas: tuple[FalhaURL, ...]
    linhas: tuple[dict[str, str], ...]


def _resolver_caminho(caminho: Path) -> Path:
    """Resolve caminhos relativos sempre a partir da raiz do projeto."""

    if caminho.is_absolute():
        return caminho.resolve()

    return (RAIZ_PROJETO / caminho).resolve()


def normalizar_url(texto: str) -> str:
    """Produz uma URL HTTP estável, sem fragmento nem credenciais."""

    if not isinstance(texto, str):
        raise TypeError("URL precisa ser um texto")

    valor = texto.strip()

    if not valor:
        raise ValueError("URL não pode ser vazia")

    endereco = urlsplit(valor)

    if endereco.scheme.casefold() not in {"http", "https"} or not endereco.hostname:
        raise ValueError("use uma URL HTTP ou HTTPS completa")

    if endereco.username is not None or endereco.password is not None:
        raise ValueError("URL não pode conter usuário ou senha")

    try:
        porta = endereco.port
    except ValueError as erro:
        raise ValueError("porta inválida na URL") from erro

    esquema = endereco.scheme.casefold()
    dominio = endereco.hostname.casefold()

    if ":" in dominio and not dominio.startswith("["):
        dominio = f"[{dominio}]"

    porta_padrao = (esquema == "http" and porta == 80) or (esquema == "https" and porta == 443)
    autoridade = dominio if porta is None or porta_padrao else f"{dominio}:{porta}"
    caminho = re.sub(r"/{2,}", "/", endereco.path or "/")

    return urlunsplit(
        SplitResult(
            scheme=esquema,
            netloc=autoridade,
            path=caminho,
            query=endereco.query,
            fragment="",
        )
    )


def _chave_url(url: str) -> str:
    """Compara barras finais sem confundir consultas diferentes."""

    endereco = urlsplit(url)
    caminho = endereco.path.rstrip("/") or "/"

    return urlunsplit(
        (
            endereco.scheme.casefold(),
            endereco.netloc.casefold(),
            caminho,
            endereco.query,
            "",
        )
    )


def _slug(texto: str) -> str:
    """Converte domínio ou caminho em identificador legível."""

    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )
    normalizado = re.sub(r"[^a-zA-Z0-9]+", "_", sem_acentos).strip("_").casefold()

    return normalizado or "fonte"


def inferir_fonte(url: str) -> Fonte:
    """Escolhe o adaptador conhecido sem fazer requisições externas."""

    endereco = urlsplit(url)
    dominio = endereco.hostname.casefold() if endereco.hostname else ""
    segmentos = {_slug(segmento).replace("_", "-") for segmento in endereco.path.split("/")}

    if dominio == "empregos.com.br" or dominio.endswith(".empregos.com.br"):
        return Fonte.EMPREGOS

    if dominio == "gupy.io" or dominio.endswith(".gupy.io"):
        return Fonte.GUPY

    if "pandape" in dominio or dominio.endswith(".infojobs.com.br"):
        return Fonte.PANDAPE

    if dominio == "api.queridodiario.org.br":
        return Fonte.QUERIDO_DIARIO

    if "/api/3/action/" in endereco.path.casefold():
        return Fonte.CKAN

    if segmentos & PALAVRAS_PAGINA_CARREIRAS:
        return Fonte.PAGINA_CARREIRAS

    return Fonte.OUTRA


def _identificador_empresa(url: str) -> str:
    """Obtém um nome provisório a partir do tenant, caminho ou domínio."""

    endereco = urlsplit(url)
    dominio = endereco.hostname.casefold() if endereco.hostname else "fonte"
    partes_dominio = dominio.split(".")
    segmentos = [segmento for segmento in endereco.path.split("/") if segmento]

    if dominio == "jobs.quickin.io" and segmentos:
        return segmentos[0]

    for plataforma in PLATAFORMAS_COM_TENANT_NO_DOMINIO:
        if dominio.endswith(f".{plataforma}"):
            return dominio[: -len(plataforma)].rstrip(".").split(".")[-1]

    partes_uteis = [parte for parte in partes_dominio if parte not in {"www", "com", "org", "br"}]

    return partes_uteis[0] if partes_uteis else partes_dominio[0]


def inferir_empresa_nome(url: str) -> str:
    """Cria um nome provisório que poderá ser refinado depois."""

    identificador = _identificador_empresa(url)
    palavras = re.split(r"[-_]+", identificador)

    return " ".join(palavra.capitalize() for palavra in palavras if palavra) or "Fonte nova"


def _criar_alvo_id(
    url: str,
    fonte: Fonte,
    ids_existentes: set[str],
) -> str:
    """Cria um ID legível e acrescenta hash somente quando necessário."""

    empresa = _slug(_identificador_empresa(url))
    sufixo = {
        Fonte.GUPY: "gupy_oficial",
        Fonte.PANDAPE: "pandape_oficial",
        Fonte.PAGINA_CARREIRAS: "carreiras",
    }.get(fonte, fonte.value)
    candidato = f"{empresa}_{sufixo}"

    if candidato.casefold() not in ids_existentes:
        return candidato

    resumo = hashlib.sha256(url.encode()).hexdigest()[:8]

    return f"{candidato}_{resumo}"


def _normalizar_booleano(valor: str, *, padrao: bool) -> str:
    """Padroniza as grafias históricas em true ou false."""

    texto = valor.strip().casefold()

    if texto in {"1", "ativo", "sim", "true", "yes"}:
        return "true"

    if texto in {"0", "inativo", "nao", "não", "false", "no"}:
        return "false"

    return "true" if padrao else "false"


def _normalizar_linha_existente(linha: dict[str, str | None]) -> dict[str, str]:
    """Coloca todas as linhas existentes no mesmo formato sem mudar a política."""

    limite_texto = (linha.get("limite_paginas") or "").strip()

    try:
        limite = int(limite_texto)
    except ValueError as erro:
        raise ValueError("limite_paginas precisa ser inteiro") from erro

    if limite < 1:
        raise ValueError("limite_paginas precisa ser pelo menos 1")

    return {
        "alvo_id": (linha.get("alvo_id") or "").strip(),
        "empresa_nome": (linha.get("empresa_nome") or "").strip(),
        "fonte": (linha.get("fonte") or "").strip().casefold(),
        "url_inicial": normalizar_url(linha.get("url_inicial") or ""),
        "ativa": _normalizar_booleano(linha.get("ativa") or "", padrao=False),
        "limite_paginas": str(limite),
        "status_politica": (linha.get("status_politica") or "pendente").strip().casefold(),
        "licenca_nome": (linha.get("licenca_nome") or "").strip(),
        "licenca_url": (linha.get("licenca_url") or "").strip(),
        "atribuicao_obrigatoria": _normalizar_booleano(
            linha.get("atribuicao_obrigatoria") or "",
            padrao=False,
        ),
        "republicacao_permitida": _normalizar_booleano(
            linha.get("republicacao_permitida") or "",
            padrao=False,
        ),
    }


def carregar_linhas_catalogo(
    caminho: Path,
    *,
    limite_maximo: int | None = None,
) -> list[dict[str, str]]:
    """Lê e padroniza o catálogo sem descartar colunas de segurança."""

    if not caminho.exists():
        return []

    if limite_maximo is not None and limite_maximo < 1:
        raise ValueError("limite_maximo precisa ser pelo menos 1")

    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo)
        cabecalhos = set(leitor.fieldnames or ())
        obrigatorias = set(COLUNAS_CATALOGO[:6])
        ausentes = obrigatorias - cabecalhos

        if ausentes:
            nomes = ", ".join(sorted(ausentes))
            raise ValueError(f"catálogo sem colunas obrigatórias: {nomes}")

        linhas = [_normalizar_linha_existente(linha) for linha in leitor]

    if limite_maximo is not None:
        for linha in linhas:
            limite_atual = int(linha["limite_paginas"])
            linha["limite_paginas"] = str(min(limite_atual, limite_maximo))

    return linhas


def carregar_urls_catalogos_conhecidos(
    diretorio: Path,
) -> set[str]:
    """Lê URLs de todos os catálogos para não rebaixar uma fonte já revisada."""

    urls: set[str] = set()

    for caminho in diretorio.glob("catalogo_*.csv"):
        for linha in carregar_linhas_catalogo(caminho):
            urls.add(_chave_url(linha["url_inicial"]))

    return urls


def importar_urls(
    linhas_existentes: list[dict[str, str]],
    textos_urls: list[str],
    *,
    limite_paginas: int = LIMITE_PAGINAS_PADRAO,
    urls_conhecidas: set[str] | None = None,
) -> ResultadoImportacao:
    """Mescla URLs válidas e mantém intactas as decisões já existentes."""

    if limite_paginas < 1:
        raise ValueError("limite_paginas precisa ser pelo menos 1")

    resultado = [dict(linha) for linha in linhas_existentes]
    urls_existentes = {_chave_url(linha["url_inicial"]) for linha in resultado}
    urls_existentes.update(urls_conhecidas or ())
    ids_existentes = {linha["alvo_id"].casefold() for linha in resultado}
    repetidas = 0
    adicionadas = 0
    falhas: list[FalhaURL] = []

    for numero_linha, texto_original in enumerate(textos_urls, start=1):
        texto = texto_original.strip()

        if not texto or texto.startswith("#"):
            continue

        try:
            url = normalizar_url(texto)
        except (TypeError, ValueError) as erro:
            falhas.append(
                FalhaURL(
                    numero_linha=numero_linha,
                    texto=texto,
                    mensagem=str(erro),
                )
            )
            continue

        chave = _chave_url(url)

        if chave in urls_existentes:
            repetidas += 1
            continue

        fonte = inferir_fonte(url)
        alvo_id = _criar_alvo_id(url, fonte, ids_existentes)
        dominio = urlsplit(url).hostname or ""
        restricao = encontrar_restricao_dominio(dominio)
        status = "bloqueada" if restricao is not None else "pendente"

        resultado.append(
            {
                "alvo_id": alvo_id,
                "empresa_nome": inferir_empresa_nome(url),
                "fonte": fonte.value,
                "url_inicial": url,
                "ativa": "false",
                "limite_paginas": str(limite_paginas),
                "status_politica": status,
                "licenca_nome": "",
                "licenca_url": "",
                "atribuicao_obrigatoria": "false",
                "republicacao_permitida": "false",
            }
        )
        urls_existentes.add(chave)
        ids_existentes.add(alvo_id.casefold())
        adicionadas += 1

    return ResultadoImportacao(
        existentes=len(linhas_existentes),
        adicionadas=adicionadas,
        repetidas=repetidas,
        falhas=tuple(falhas),
        linhas=tuple(resultado),
    )


def escrever_catalogo(caminho: Path, linhas: tuple[dict[str, str], ...]) -> None:
    """Valida e substitui o catálogo de maneira atômica."""

    caminho.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=caminho.parent,
        prefix=f".{caminho.stem}_",
        suffix=".csv",
        delete=False,
    ) as temporario:
        caminho_temporario = Path(temporario.name)
        escritor = csv.DictWriter(
            temporario,
            fieldnames=COLUNAS_CATALOGO,
            lineterminator="\n",
        )
        escritor.writeheader()
        escritor.writerows(linhas)

    try:
        carregar_alvos_csv(caminho_temporario)
        caminho_temporario.replace(caminho)
    except Exception:
        caminho_temporario.unlink(missing_ok=True)
        raise


def criar_parser() -> argparse.ArgumentParser:
    """Define uma interface curta para listas com uma URL por linha."""

    parser = argparse.ArgumentParser(
        description="Importa URLs simples e padroniza o catálogo sem liberar publicação.",
    )
    parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("config/urls_fontes.txt"),
        help="arquivo com uma URL por linha",
    )
    parser.add_argument(
        "--catalogo",
        type=Path,
        default=Path("config/catalogo_fontes.csv"),
        help="catálogo CSV que receberá as fontes",
    )
    parser.add_argument(
        "--limite-paginas",
        type=int,
        default=10,
        help="limite seguro aplicado apenas às URLs novas",
    )
    parser.add_argument(
        "--simular",
        action="store_true",
        help="mostra o resultado sem alterar o catálogo",
    )
    parser.add_argument(
        "--somente-normalizar",
        action="store_true",
        help="padroniza as linhas existentes sem importar o arquivo de URLs",
    )
    parser.add_argument(
        "--limite-maximo",
        type=int,
        help="reduz limites existentes maiores que este valor",
    )

    return parser


def executar(argumentos: list[str] | None = None) -> int:
    """Executa a importação tolerante e apresenta um resumo curto."""

    opcoes = criar_parser().parse_args(argumentos)
    catalogo = _resolver_caminho(opcoes.catalogo)
    entrada = _resolver_caminho(opcoes.entrada)

    try:
        existentes = carregar_linhas_catalogo(
            catalogo,
            limite_maximo=opcoes.limite_maximo,
        )

        if opcoes.somente_normalizar:
            textos_urls: list[str] = []
        else:
            textos_urls = entrada.read_text(encoding="utf-8-sig").splitlines()

        resultado = importar_urls(
            existentes,
            textos_urls,
            limite_paginas=opcoes.limite_paginas,
            urls_conhecidas=carregar_urls_catalogos_conhecidos(catalogo.parent),
        )

        if not opcoes.simular:
            escrever_catalogo(catalogo, resultado.linhas)

    except (OSError, TypeError, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 2

    print()
    print("# IMPORTAÇÃO SIMPLES DE FONTES")
    print()
    print(f"Catálogo: {catalogo}")
    print(f"Modo: {'SIMULAÇÃO' if opcoes.simular else 'GRAVAÇÃO'}")
    print(f"Fontes que já existiam: {resultado.existentes}")
    print(f"Fontes novas adicionadas: {resultado.adicionadas}")
    print(f"URLs repetidas ignoradas: {resultado.repetidas}")
    print(f"URLs inválidas ignoradas: {len(resultado.falhas)}")
    print("Novas fontes ficam inativas e pendentes até a revisão da política.")

    if resultado.falhas:
        print()
        print("## URLS INVÁLIDAS")

        for falha in resultado.falhas:
            print(f"- linha {falha.numero_linha}: {falha.texto} | {falha.mensagem}")

    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
