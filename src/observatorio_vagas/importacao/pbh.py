"""Importação auditável das vagas abertas publicadas pela PBH.

A Prefeitura de Belo Horizonte publica esse conjunto como CSV. Ele não é uma
página de vaga e, portanto, não deve passar pelo spider de HTML. Este módulo
converte cada linha válida para o mesmo ``AnuncioVaga`` utilizado pelo restante
do projeto.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Any

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import Fonte, StatusAnuncio

ALVO_ID_PBH = "pbh_sine_vagas_abertas"
URL_CONJUNTO_PBH = "https://dados.pbh.gov.br/pt_BR/dataset/vagas-ofertadas-pbh"
ATRIBUICAO_PBH = (
    "Prefeitura de Belo Horizonte / Secretaria Municipal de Desenvolvimento "
    "Econômico / Central de Vagas SINE BH"
)
LICENCA_PBH = "Creative Commons Attribution"

_CAMPOS_OBRIGATORIOS = frozenset(
    {
        "data",
        "cnpj",
        "identificacao",
        "ocupacao",
        "local_trabalho",
        "numero_vagas",
        "experiencia",
        "escolaridade",
        "remuneracao",
    }
)

_ALIASES_CABECALHO = {
    "data": "data",
    "cnpj": "cnpj",
    "identificacao": "identificacao",
    "ocupacao": "ocupacao",
    "local de trabalho": "local_trabalho",
    "n de vagas": "numero_vagas",
    "no de vagas": "numero_vagas",
    "numero de vagas": "numero_vagas",
    "experiencia": "experiencia",
    "escolaridade": "escolaridade",
    "remuneracao": "remuneracao",
}


class ErroArquivoVagasPBH(ValueError):
    """Indica que o arquivo não possui uma estrutura utilizável."""


@dataclass(frozen=True, slots=True)
class FalhaLinhaVagasPBH:
    """Linha rejeitada sem interromper as demais vagas do arquivo."""

    numero_linha: int
    identificacao: str
    mensagem: str


@dataclass(frozen=True, slots=True)
class ResultadoImportacaoVagasPBH:
    """Resultado completo da conversão tolerante do CSV."""

    anuncios: tuple[AnuncioVaga, ...]
    falhas: tuple[FalhaLinhaVagasPBH, ...]
    linhas_lidas: int
    duplicados: int
    encerrados: int


def _normalizar_cabecalho(valor: str) -> str:
    """Remove diferenças de acento, caixa e pontuação dos cabeçalhos."""

    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )
    sem_simbolos = re.sub(r"[^a-z0-9]+", " ", sem_acentos.casefold())
    return " ".join(sem_simbolos.split())


def _decodificar(conteudo: bytes) -> str:
    """Aceita os formatos mais comuns produzidos pelo portal e pelo Excel."""

    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue

    raise ErroArquivoVagasPBH("o CSV precisa usar UTF-8 ou Windows-1252")


def _detectar_dialeto(texto: str) -> csv.Dialect:
    """Detecta vírgula ou ponto e vírgula sem depender da extensão."""

    amostra = texto[:8192]

    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _mapear_cabecalhos(nomes: list[str | None]) -> dict[str, str]:
    """Relaciona os nomes oficiais às chaves internas do importador."""

    mapeamento: dict[str, str] = {}

    for nome in nomes:
        if nome is None:
            continue

        normalizado = _normalizar_cabecalho(nome)
        chave = _ALIASES_CABECALHO.get(normalizado)

        if chave is not None:
            mapeamento[nome] = chave

    ausentes = _CAMPOS_OBRIGATORIOS - set(mapeamento.values())

    if ausentes:
        lista = ", ".join(sorted(ausentes))
        raise ErroArquivoVagasPBH(f"colunas obrigatórias ausentes: {lista}")

    return mapeamento


def _texto(valor: object) -> str:
    """Converte uma célula para texto limpo."""

    return "" if valor is None else " ".join(str(valor).strip().split())


def _converter_data(valor: str) -> date:
    """Converte a data brasileira sem aceitar interpretações ambíguas."""

    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue

    raise ValueError("DATA deve usar DD/MM/AAAA")


def _normalizar_cnpj(valor: str) -> str:
    """Preserva zeros e valida a estrutura básica do CNPJ publicado."""

    normalizado = re.sub(r"[^A-Za-z0-9]", "", valor).upper()

    if len(normalizado) != 14 or not normalizado[-2:].isdigit():
        raise ValueError("CNPJ deve possuir 14 posições")

    return normalizado


def _converter_quantidade(valor: str) -> int:
    """Exige uma quantidade inteira e positiva."""

    try:
        quantidade = int(valor)
    except ValueError as erro:
        raise ValueError("No DE VAGAS deve ser um número inteiro") from erro

    if quantidade < 1:
        raise ValueError("No DE VAGAS deve ser pelo menos 1")

    return quantidade


def _criar_salario_estruturado(remuneracao: str) -> dict[str, Any] | None:
    """Preserva uma remuneração numérica para a normalização posterior."""

    if not re.search(r"\d", remuneracao) or "combinar" in remuneracao.casefold():
        return None

    return {
        "@type": "MonetaryAmount",
        "currency": "BRL",
        "value": {
            "@type": "QuantitativeValue",
            "value": remuneracao,
            "unitText": "MONTH",
        },
    }


def _criar_descricao(
    *,
    ocupacao: str,
    local_trabalho: str,
    experiencia: str,
    escolaridade: str,
    remuneracao: str,
    numero_vagas: int,
) -> str:
    """Monta um resumo somente com fatos presentes na linha oficial."""

    return "\n".join(
        (
            f"Ocupação: {ocupacao}.",
            f"Local de trabalho: {local_trabalho}.",
            f"Experiência informada: {experiencia or 'não informada'}.",
            f"Escolaridade informada: {escolaridade or 'não informada'}.",
            f"Remuneração informada: {remuneracao or 'não informada'}.",
            f"Número de vagas: {numero_vagas}.",
            f"Fonte e atribuição: {ATRIBUICAO_PBH}. {URL_CONJUNTO_PBH}",
            f"Licença do conjunto: {LICENCA_PBH}.",
        )
    )


def _linha_para_anuncio(
    linha: dict[str, str],
    *,
    numero_linha: int,
    referencia_bruta: str,
    observado_em: datetime,
    validade_dias: int,
) -> AnuncioVaga:
    """Converte uma linha já mapeada para o domínio comum da aplicação."""

    identificacao = _texto(linha["identificacao"])
    ocupacao = _texto(linha["ocupacao"])
    local_trabalho = _texto(linha["local_trabalho"])
    experiencia = _texto(linha["experiencia"])
    escolaridade = _texto(linha["escolaridade"])
    remuneracao = _texto(linha["remuneracao"])

    if not identificacao:
        raise ValueError("IDENTIFICACAO não pode ser vazia")
    if not ocupacao:
        raise ValueError("OCUPACAO não pode ser vazia")
    if not local_trabalho:
        raise ValueError("LOCAL DE TRABALHO não pode ser vazio")

    publicada_em = _converter_data(_texto(linha["data"]))
    cnpj = _normalizar_cnpj(_texto(linha["cnpj"]))
    numero_vagas = _converter_quantidade(_texto(linha["numero_vagas"]))
    limite_operacional = publicada_em + timedelta(days=validade_dias)
    encerrada = limite_operacional < observado_em.date()

    documento: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {
            "@type": "PropertyValue",
            "value": identificacao,
        },
        "title": ocupacao,
        "description": _criar_descricao(
            ocupacao=ocupacao,
            local_trabalho=local_trabalho,
            experiencia=experiencia,
            escolaridade=escolaridade,
            remuneracao=remuneracao,
            numero_vagas=numero_vagas,
        ),
        "datePosted": publicada_em.isoformat(),
        "validThrough": limite_operacional.isoformat(),
        "hiringOrganization": {
            "@type": "Organization",
            "taxID": cnpj,
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": local_trabalho,
                "addressRegion": "MG",
                "addressCountry": "BR",
            },
        },
        "experienceRequirements": experiencia or None,
        "educationRequirements": escolaridade or None,
        "totalJobOpenings": numero_vagas,
        "_observatorio": {
            "atribuicao": ATRIBUICAO_PBH,
            "licenca": LICENCA_PBH,
            "url_conjunto": URL_CONJUNTO_PBH,
            "expiracao_inferida": True,
            "validade_operacional_dias": validade_dias,
            "linha_csv": numero_linha,
        },
    }

    salario = _criar_salario_estruturado(remuneracao)
    if salario is not None:
        documento["baseSalary"] = salario

    conteudo_canonico = json.dumps(
        linha,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    hash_linha = sha256(conteudo_canonico).hexdigest()

    return AnuncioVaga(
        alvo_id=ALVO_ID_PBH,
        fonte=Fonte.OUTRA,
        id_externo=f"pbh-sine:{identificacao}",
        url=URL_CONJUNTO_PBH,
        id_empresa_na_fonte=cnpj,
        titulo_original=ocupacao,
        descricao_original=documento["description"],
        localidade_original=local_trabalho,
        salario_original=remuneracao or None,
        requisitos_original="; ".join(parte for parte in (experiencia, escolaridade) if parte)
        or None,
        numero_vagas_original=numero_vagas,
        publicado_em=publicada_em,
        # O CSV não informa expiração. Esta data é uma trava interna e fica
        # explicitamente identificada como inferida nos campos estruturados.
        expira_em=limite_operacional,
        status=(StatusAnuncio.ENCERRADO if encerrada else StatusAnuncio.DESCOBERTO),
        hash_conteudo=hash_linha,
        referencia_bruta=f"{referencia_bruta}#linha={numero_linha}",
        campos_estruturados=documento,
        primeira_observacao_em=observado_em,
        ultima_observacao_em=observado_em,
    )


def importar_vagas_pbh_csv(
    conteudo: bytes,
    *,
    referencia_bruta: str,
    observado_em: datetime | None = None,
    validade_dias: int = 30,
) -> ResultadoImportacaoVagasPBH:
    """Importa linhas válidas e isola erros ou duplicidades.

    ``validade_dias`` é uma trava operacional, não uma informação publicada
    pela PBH. Ela impede que uma linha histórica seja tratada indefinidamente
    como vaga aberta.
    """

    if not conteudo:
        raise ErroArquivoVagasPBH("o arquivo CSV está vazio")

    if isinstance(validade_dias, bool) or not 1 <= validade_dias <= 365:
        raise ValueError("validade_dias deve estar entre 1 e 365")

    momento = observado_em or datetime.now(UTC)
    if momento.tzinfo is None or momento.utcoffset() is None:
        raise ValueError("observado_em precisa informar o fuso horário")

    texto = _decodificar(conteudo)
    leitor = csv.DictReader(
        io.StringIO(texto),
        dialect=_detectar_dialeto(texto),
    )
    mapeamento = _mapear_cabecalhos(list(leitor.fieldnames or []))

    anuncios: list[AnuncioVaga] = []
    falhas: list[FalhaLinhaVagasPBH] = []
    ids_encontrados: set[str] = set()
    linhas_lidas = 0
    duplicados = 0
    encerrados = 0

    for numero_linha, linha_original in enumerate(leitor, start=2):
        if not any(_texto(valor) for valor in linha_original.values()):
            continue

        linhas_lidas += 1
        linha = {
            chave_interna: _texto(linha_original.get(cabecalho))
            for cabecalho, chave_interna in mapeamento.items()
        }
        identificacao = linha.get("identificacao") or "não informada"
        chave_id = identificacao.casefold()

        if chave_id in ids_encontrados:
            duplicados += 1
            continue

        try:
            anuncio = _linha_para_anuncio(
                linha,
                numero_linha=numero_linha,
                referencia_bruta=referencia_bruta,
                observado_em=momento,
                validade_dias=validade_dias,
            )
        except (KeyError, TypeError, ValueError) as erro:
            falhas.append(
                FalhaLinhaVagasPBH(
                    numero_linha=numero_linha,
                    identificacao=identificacao,
                    mensagem=str(erro),
                )
            )
            continue

        ids_encontrados.add(chave_id)
        anuncios.append(anuncio)

        if anuncio.status is StatusAnuncio.ENCERRADO:
            encerrados += 1

    return ResultadoImportacaoVagasPBH(
        anuncios=tuple(anuncios),
        falhas=tuple(falhas),
        linhas_lidas=linhas_lidas,
        duplicados=duplicados,
        encerrados=encerrados,
    )
