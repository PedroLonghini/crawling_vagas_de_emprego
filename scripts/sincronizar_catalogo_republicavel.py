"""Gera o catálogo operacional de fontes abertas e republicáveis.

O Querido Diário disponibiliza uma lista dinâmica de municípios. Este script
transforma essa lista em lotes pequenos e disjuntos, evitando manter centenas
de códigos IBGE manualmente. O arquivo de saída somente é substituído depois
que toda a resposta e todas as linhas forem validadas.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.domain.politica_fonte import licenca_permite_republicacao

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
URL_MUNICIPIOS = "https://api.queridodiario.org.br/cities"
URL_BUSCA = "https://api.queridodiario.org.br/gazettes"
URL_LICENCA = "https://docs.queridodiario.ok.org.br/pt-br/latest/"
CONSULTAS_QUERIDO_DIARIO = (
    (
        "empregos",
        '"vaga de emprego" | "vagas abertas" | "oportunidades de emprego" | '
        '"agência do trabalhador" | "casa do trabalhador" | SINE | '
        '"balcão de empregos" | "primeiro emprego" | "feira de empregos" | '
        '"feirão de empregos" | "mutirão de emprego" | '
        '"intermediação de mão de obra" | "captação de vagas" | '
        '"recrutamento e seleção" | "emprega brasil"',
    ),
    (
        "estagios_aprendizes",
        '"estágio" | "vagas de estágio" | "jovem aprendiz" | '
        '"programa de aprendizagem" | "programa de estágio"',
    ),
)
CONSULTA_QUERIDO_DIARIO_COMBINADA = " | ".join(
    consulta for _, consulta in CONSULTAS_QUERIDO_DIARIO
)
USER_AGENT = "ObservatorioVagas/0.1.0"
IDS_CKAN_SUPORTADOS = (
    "es_setades_vagas_agencias",
    "iftm_vagas_estagio_emprego",
)
COLUNAS = (
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
T = TypeVar("T")


class ErroSincronizacaoCatalogo(RuntimeError):
    """Falha segura que impede substituir um catálogo válido."""


@dataclass(frozen=True, slots=True, order=True)
class MunicipioDisponivel:
    """Município efetivamente coberto pela API do Querido Diário."""

    estado: str
    territorio_id: str
    nome: str


def _resolver_caminho(caminho: Path) -> Path:
    """Resolve caminhos relativos a partir da raiz do projeto."""

    if caminho.is_absolute():
        return caminho.resolve()

    return (RAIZ_PROJETO / caminho).resolve()


def _texto(valor: object) -> str | None:
    """Aceita somente texto não vazio."""

    if not isinstance(valor, str):
        return None

    resultado = valor.strip()

    return resultado or None


def extrair_municipios_disponiveis(dados: object) -> tuple[MunicipioDisponivel, ...]:
    """Valida a resposta de ``/cities`` e remove municípios indisponíveis."""

    if not isinstance(dados, Mapping):
        raise ErroSincronizacaoCatalogo("a resposta de /cities precisa ser um objeto JSON")

    itens = dados.get("cities")

    if not isinstance(itens, list):
        raise ErroSincronizacaoCatalogo("a resposta de /cities não possui a lista cities")

    municipios: dict[str, MunicipioDisponivel] = {}

    for item in itens:
        if not isinstance(item, Mapping) or not _texto(item.get("availability_date")):
            continue

        territorio_id = _texto(item.get("territory_id"))
        nome = _texto(item.get("territory_name"))
        estado = _texto(item.get("state_code"))

        if territorio_id is None or nome is None or estado is None:
            continue

        if not territorio_id.isdigit() or len(territorio_id) != 7:
            continue

        municipios[territorio_id] = MunicipioDisponivel(
            estado=estado.upper(),
            territorio_id=territorio_id,
            nome=nome,
        )

    if not municipios:
        raise ErroSincronizacaoCatalogo("nenhum município disponível foi retornado pela API")

    return tuple(sorted(municipios.values()))


def _dividir(itens: Sequence[T], tamanho: int) -> tuple[tuple[T, ...], ...]:
    """Divide uma sequência em grupos previsíveis."""

    return tuple(
        tuple(itens[inicio : inicio + tamanho]) for inicio in range(0, len(itens), tamanho)
    )


def criar_linhas_querido_diario(
    municipios: Sequence[MunicipioDisponivel],
    *,
    tamanho_lote: int = 10,
    por_municipio: bool = False,
) -> tuple[dict[str, str], ...]:
    """Cria buscas por UF sem repetir um município entre os alvos."""

    if tamanho_lote < 1 or tamanho_lote > 20:
        raise ValueError("tamanho_lote precisa estar entre 1 e 20")

    por_estado: defaultdict[str, list[MunicipioDisponivel]] = defaultdict(list)

    for municipio in municipios:
        if not isinstance(municipio, MunicipioDisponivel):
            raise TypeError("municipios precisa conter MunicipioDisponivel")

        por_estado[municipio.estado].append(municipio)

    linhas: list[dict[str, str]] = []

    for estado in sorted(por_estado):
        grupos = _dividir(sorted(por_estado[estado]), 1 if por_municipio else tamanho_lote)

        for numero, grupo in enumerate(grupos, start=1):
            consultas = (
                (("oportunidades", CONSULTA_QUERIDO_DIARIO_COMBINADA),)
                if por_municipio
                else CONSULTAS_QUERIDO_DIARIO
            )
            for categoria, consulta in consultas:
                parametros: list[tuple[str, str]] = [
                    ("territory_ids", municipio.territorio_id) for municipio in grupo
                ]
                parametros.extend(
                    (
                        ("querystring", consulta),
                        ("excerpt_size", "2500"),
                        ("number_of_excerpts", "3"),
                        ("size", "50"),
                        ("sort_by", "descending_date"),
                    )
                )
                linhas.append(
                    {
                        "alvo_id": (
                            f"querido_diario_ibge_{grupo[0].territorio_id}_{categoria}"
                            if por_municipio
                            else f"querido_diario_{estado.casefold()}_{numero:02d}_{categoria}"
                        ),
                        "empresa_nome": (
                            f"Querido Diário / {grupo[0].nome} / {estado}"
                            if por_municipio
                            else f"Querido Diario / {estado} / Lote {numero:02d} / {categoria}"
                        ),
                        "fonte": "querido_diario",
                        "url_inicial": f"{URL_BUSCA}?{urlencode(parametros)}",
                        "ativa": "true",
                        "limite_paginas": "10",
                        "status_politica": "aprovada",
                        "licenca_nome": "CC BY 4.0",
                        "licenca_url": URL_LICENCA,
                        "atribuicao_obrigatoria": "true",
                        "republicacao_permitida": "true",
                    }
                )

    return tuple(linhas)


def atualizar_consultas_querido_diario(
    linhas: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    """Amplia somente buscas municipais ativas, preservando política e limites.

    Catálogos já gerados podem conter a consulta anterior mesmo após a ampliação
    dos termos. Esta migração é local: não consulta a API nem altera quais
    municípios, domínios ou licenças estão autorizados.
    """

    atualizadas: list[dict[str, str]] = []
    for linha_original in linhas:
        linha = dict(linha_original)
        if linha.get("fonte") != "querido_diario" or linha.get("ativa") != "true":
            atualizadas.append(linha)
            continue

        alvo_id = linha.get("alvo_id", "")
        if alvo_id.endswith("_empregos"):
            consulta = CONSULTAS_QUERIDO_DIARIO[0][1]
        elif alvo_id.endswith("_estagios_aprendizes"):
            consulta = CONSULTAS_QUERIDO_DIARIO[1][1]
        else:
            consulta = CONSULTA_QUERIDO_DIARIO_COMBINADA

        endereco = urlsplit(linha.get("url_inicial", ""))
        parametros = [
            (nome, valor)
            for nome, valor in parse_qsl(endereco.query, keep_blank_values=True)
            if nome != "querystring"
        ]
        parametros.append(("querystring", consulta))
        linha["url_inicial"] = urlunsplit(
            (
                endereco.scheme,
                endereco.netloc,
                endereco.path,
                urlencode(parametros),
                endereco.fragment,
            )
        )
        atualizadas.append(linha)

    return tuple(atualizadas)


def _carregar_linhas_ckan(caminho: Path) -> tuple[dict[str, str], ...]:
    """Copia somente integrações CKAN já implementadas e testadas."""

    # Valida primeiro o catálogo pelo mesmo caminho usado em produção.
    alvos = {alvo.alvo_id: alvo for alvo in carregar_alvos_csv(caminho)}

    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        linhas = tuple(csv.DictReader(arquivo))

    por_id = {linha.get("alvo_id", ""): linha for linha in linhas}
    ausentes = [alvo_id for alvo_id in IDS_CKAN_SUPORTADOS if alvo_id not in por_id]

    if ausentes:
        raise ErroSincronizacaoCatalogo(
            "fontes CKAN suportadas ausentes do catálogo base: " + ", ".join(ausentes)
        )

    ids = (*IDS_CKAN_SUPORTADOS, "pbh_sine_vagas_abertas")
    return tuple(
        {coluna: por_id[alvo_id].get(coluna, "") for coluna in COLUNAS}
        for alvo_id in ids
        if alvo_id in alvos and alvos[alvo_id].ativa and alvos[alvo_id].habilitado_para_publicacao
    )


def baixar_municipios(*, timeout: float = 30.0) -> tuple[MunicipioDisponivel, ...]:
    """Consulta a API oficial usando identificação explícita do projeto."""

    if timeout <= 0:
        raise ValueError("timeout precisa ser positivo")

    try:
        with requests.get(
            URL_MUNICIPIOS,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        ) as resposta:
            resposta.raise_for_status()
            dados: Any = resposta.json()
    except (requests.RequestException, OSError, UnicodeError, ValueError) as erro:
        raise ErroSincronizacaoCatalogo(
            f"não foi possível obter a lista oficial de municípios ({type(erro).__name__}); "
            "o catálogo anterior foi preservado"
        ) from erro

    return extrair_municipios_disponiveis(dados)


def sincronizar_biblioteca(base: Path, linhas: Sequence[Mapping[str, str]]) -> None:
    """Alinha catálogo central e URLs, preservando IDs históricos sem recoletá-los.

    A biblioteca do Querido Diário foi desativada: fontes operacionais agora
    devem ser páginas de carreira de empresas com candidatura rastreável.
    """
    with base.open(encoding="utf-8-sig", newline="") as arquivo:
        antigas = tuple(csv.DictReader(arquivo))
    novas = {linha["alvo_id"]: dict(linha) for linha in linhas}
    for antiga in antigas:
        if antiga["alvo_id"] not in novas:
            # Manter a política permite diagnosticar anúncios já no MongoDB.
            # Alvos antigos não duplicam a nova coleta municipal.
            novas[antiga["alvo_id"]] = {**antiga, "ativa": "false"}
    escrever_catalogo(base, tuple(novas.values()))
    escrever_urls_operacionais(base.parent / "urls_fontes.txt", linhas)
    escrever_catalogo(base.parent / "catalogo_fontes_diarias.csv", linhas)


def limpar_catalogos_operacionais(base: Path, saida: Path) -> tuple[dict[str, str], ...]:
    """Remove do catálogo operacional tudo que não possui licença aberta válida.

    A ação é local e sempre é precedida por backup no chamador. Candidatas e
    registros históricos não são apagados do projeto: ficam no backup e na fila
    de prospecção, mas deixam de poder ser usados pelo crawler por engano.
    """

    with base.open(encoding="utf-8-sig", newline="") as arquivo:
        linhas = tuple(csv.DictReader(arquivo))

    alvos = {alvo.alvo_id: alvo for alvo in carregar_alvos_csv(base)}
    aprovadas: list[dict[str, str]] = []

    for linha in linhas:
        alvo = alvos.get(linha.get("alvo_id", ""))
        if alvo is None or not alvo.ativa or not alvo.habilitado_para_publicacao:
            continue
        if not licenca_permite_republicacao(linha.get("licenca_nome", "")):
            continue
        aprovadas.append({coluna: linha.get(coluna, "") for coluna in COLUNAS})

    if not aprovadas:
        raise ErroSincronizacaoCatalogo("a limpeza recusou gerar um catálogo vazio")

    escrever_catalogo(base, aprovadas)
    escrever_catalogo(saida, aprovadas)
    escrever_catalogo(base.parent / "catalogo_fontes_diarias.csv", aprovadas)
    escrever_urls_operacionais(base.parent / "urls_fontes.txt", aprovadas)
    return tuple(aprovadas)


def escrever_urls_operacionais(
    caminho: Path,
    linhas: Sequence[Mapping[str, str]],
) -> None:
    """Mantém a biblioteca legível como espelho das URLs ativas do catálogo."""

    urls = sorted(
        {
            linha["url_inicial"]
            for linha in linhas
            if linha.get("ativa") == "true" and linha.get("url_inicial")
        }
    )
    temporario = caminho.with_suffix(f"{caminho.suffix}.tmp")
    temporario.write_text(
        "# URLs operacionais; regras e procedência no catálogo central.\n"
        + "\n".join(urls)
        + "\n",
        encoding="utf-8",
    )
    temporario.replace(caminho)


def escrever_catalogo(caminho: Path, linhas: Sequence[Mapping[str, str]]) -> None:
    """Grava de forma atômica para preservar o catálogo anterior em falhas."""

    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(f"{caminho.suffix}.tmp")

    try:
        with temporario.open("w", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS, extrasaction="ignore")
            escritor.writeheader()
            escritor.writerows(linhas)

        # A própria carga de produção valida o arquivo antes da substituição.
        carregar_alvos_csv(temporario)
        temporario.replace(caminho)
    except Exception:
        temporario.unlink(missing_ok=True)
        raise


def criar_parser() -> argparse.ArgumentParser:
    """Define a interface de linha de comando."""

    parser = argparse.ArgumentParser(
        description="Mantém os catálogos operacionais de páginas de carreira."
    )
    parser.add_argument(
        "--catalogo-base",
        type=Path,
        default=Path("config/catalogo_fontes.csv"),
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=Path("config/catalogo_fontes_republicaveis.csv"),
    )
    parser.add_argument("--tamanho-lote", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--por-municipio",
        action="store_true",
        help="uma busca combinada por município, com ID IBGE estável",
    )
    parser.add_argument(
        "--sincronizar-biblioteca",
        action="store_true",
        help="alinha também o catálogo central e urls_fontes.txt; cria backup",
    )
    parser.add_argument(
        "--atualizar-consultas-qd",
        action="store_true",
        help=(
            "atualiza localmente as consultas Querido Diário já ativas, sem "
            "consultar a API ou alterar política, municípios e limites"
        ),
    )
    parser.add_argument(
        "--limpar-operacionais",
        action="store_true",
        help=(
            "mantém nos catálogos operacionais somente alvos ativos com licença "
            "aberta compatível e republicação aprovada; cria backup antes da limpeza"
        ),
    )

    return parser


def executar(argumentos: Sequence[str] | None = None) -> int:
    """Sincroniza o catálogo e mostra a cobertura obtida."""

    opcoes = criar_parser().parse_args(argumentos)

    try:
        base = _resolver_caminho(opcoes.catalogo_base)
        saida = _resolver_caminho(opcoes.saida)
        if opcoes.atualizar_consultas_qd or opcoes.sincronizar_biblioteca:
            raise ErroSincronizacaoCatalogo(
                "Querido Diário foi retirado do crawler. Cadastre páginas de carreira "
                "no catálogo central em vez de sincronizar consultas municipais."
            )
        if opcoes.limpar_operacionais:
            caminhos = (
                base,
                saida,
                base.parent / "urls_fontes.txt",
                base.parent / "catalogo_fontes_diarias.csv",
            )
            backup = (
                RAIZ_PROJETO
                / "outputs"
                / "backup_catalogos"
                / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            )
            backup.mkdir(parents=True)
            for caminho in caminhos:
                if caminho.exists():
                    shutil.copy2(caminho, backup / caminho.name)
            linhas = limpar_catalogos_operacionais(base, saida)
            print("CATÁLOGOS OPERACIONAIS LIMPOS")
            print(f"Fontes preservadas: {len(linhas)}")
            print(f"Backup: {backup}")
            return 0
        raise ErroSincronizacaoCatalogo(
            "a sincronização de CKAN e Querido Diário foi retirada. "
            "Use o catálogo central para cadastrar páginas de carreira."
        )
    except (ErroSincronizacaoCatalogo, OSError, TypeError, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 1



if __name__ == "__main__":
    raise SystemExit(executar())
