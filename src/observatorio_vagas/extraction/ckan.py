"""Conversão de CSVs públicos CKAN para documentos JobPosting."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256
from io import StringIO
from typing import Any
from unicodedata import combining, normalize

from observatorio_vagas.domain.enums import StatusAnuncio
from observatorio_vagas.extraction.concursos_ufvjm import extrair_concursos_ufvjm
from observatorio_vagas.importacao.pbh import importar_vagas_pbh_csv


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoCkan:
    """Resultado auditável da leitura de um recurso CSV."""

    vagas: tuple[dict[str, Any], ...]
    linhas_encontradas: int
    linhas_ignoradas: int
    linhas_invalidas: int


def extrair_job_postings_ckan(
    conteudo: str | bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
    data_referencia: date | None = None,
) -> ResultadoExtracaoCkan:
    """Reconhece os esquemas oficiais do ES, IFTM e UFPE."""

    if not isinstance(url, str) or not url.strip():
        raise ValueError("url do recurso CKAN não pode ser vazia")

    texto = _decodificar(conteudo, codificacao)
    linhas = _ler_linhas(texto)

    if not linhas:
        return ResultadoExtracaoCkan((), 0, 0, 0)

    colunas = set(linhas[0])

    if {"ocupacao", "identificacao", "cnpj", "remuneracao"} <= colunas:
        resultado = importar_vagas_pbh_csv(
            texto.encode(),
            referencia_bruta=url,
            observado_em=datetime.combine(data_referencia or date.today(), time.min, tzinfo=UTC),
        )
        vagas = tuple(
            {**anuncio.campos_estruturados, "url": url}
            for anuncio in resultado.anuncios
            if anuncio.status is not StatusAnuncio.ENCERRADO
        )
        return ResultadoExtracaoCkan(
            vagas,
            resultado.linhas_lidas,
            resultado.encerrados + resultado.duplicados,
            len(resultado.falhas),
        )

    if {"carreira", "finalidade_do_concurso_processo_seletivo", "situacao"} <= colunas:
        vagas, ignoradas, invalidas = extrair_concursos_ufvjm(linhas, url=url)
        return ResultadoExtracaoCkan(vagas, len(linhas), ignoradas, invalidas)

    if {"vaga", "codigo_da_vaga", "posto"} <= colunas:
        return _extrair_es(linhas, url=url)

    if {"concedente", "tipo_vaga", "vaga", "cidade"} <= colunas:
        return _extrair_iftm(
            linhas,
            url=url,
            data_referencia=(data_referencia or date.today()),
        )

    if {"id_edital", "tipo_concurso", "status"} <= colunas:
        return _extrair_ufpe(
            linhas,
            url=url,
            data_referencia=(data_referencia or date.today()),
        )

    if {"cargo", "nivel", "qtde"} <= colunas:
        return _extrair_ufms(
            linhas,
            url=url,
            data_referencia=(data_referencia or date.today()),
        )

    raise ValueError("CSV CKAN possui um esquema ainda não suportado")


def _extrair_ufms(
    linhas: list[dict[str, str]],
    *,
    url: str,
    data_referencia: date,
) -> ResultadoExtracaoCkan:
    """Converte o CSV mensal de vagas de concurso da UFMS.

    O conjunto público declara somente cargo, nível e quantidade. Por isso o
    conversor não inventa salário, prazo de inscrição ou CNPJ.
    """

    vagas: list[dict[str, Any]] = []
    ignoradas = 0
    invalidas = 0

    for numero_linha, linha in enumerate(linhas, start=2):
        cargo = _texto(linha.get("cargo"))
        quantidade = _inteiro_positivo(linha.get("qtde"))

        if cargo is None or quantidade is None:
            if any(_texto(valor) is not None for valor in linha.values()):
                invalidas += 1
            else:
                ignoradas += 1
            continue

        nivel = _texto(linha.get("nivel"))
        descricao = (
            "Vaga para concurso de servidores da Universidade Federal de Mato Grosso do Sul "
            f"(UFMS). Quantidade divulgada no arquivo mensal: {quantidade}."
        )
        if nivel is not None:
            descricao = f"{descricao} Nível informado pela fonte: {nivel}."

        vagas.append(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "identifier": {
                    "@type": "PropertyValue",
                    "name": "UFMS",
                    "value": sha256(f"{url}#{numero_linha}#{cargo}".encode()).hexdigest()[:50],
                },
                "title": cargo,
                "description": descricao,
                "datePosted": data_referencia.isoformat(),
                "url": url,
                "_observatorio_apply_url": url,
                "_observatorio_extrator": "ckan_ufms",
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Universidade Federal de Mato Grosso do Sul (UFMS)",
                    "description": (
                        "Instituição federal de ensino superior responsável pelo concurso."
                    ),
                },
                "industry": "Educação",
                "jobLocation": {
                    "@type": "Place",
                    "address": {"@type": "PostalAddress", "addressCountry": "BR"},
                },
            }
        )

    return ResultadoExtracaoCkan(tuple(vagas), len(linhas), ignoradas, invalidas)


def _decodificar(
    conteudo: str | bytes,
    codificacao: str,
) -> str:
    """Decodifica CSV sem esconder bytes inválidos."""

    if isinstance(conteudo, str):
        return conteudo

    if not isinstance(conteudo, bytes):
        raise TypeError("conteudo precisa ser texto ou bytes")

    candidatas = tuple(dict.fromkeys((codificacao, "utf-8-sig", "utf-8", "cp1252")))

    for candidata in candidatas:
        try:
            return conteudo.decode(candidata)
        except (LookupError, UnicodeDecodeError):
            continue

    raise ValueError("não foi possível decodificar o CSV CKAN")


def _ler_linhas(
    texto: str,
) -> list[dict[str, str]]:
    """Lê vírgula ou ponto e vírgula e normaliza os cabeçalhos."""

    primeira_linha = texto.splitlines()[0] if texto.splitlines() else ""
    delimitador = ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","
    leitor = csv.DictReader(StringIO(texto), delimiter=delimitador)

    if leitor.fieldnames is None:
        return []

    linhas: list[dict[str, str]] = []

    for linha in leitor:
        normalizada = {
            _normalizar_chave(chave): (valor or "").strip()
            for chave, valor in linha.items()
            if chave is not None
        }
        linhas.append(normalizada)

    return linhas


def _normalizar_chave(
    valor: str,
) -> str:
    """Transforma cabeçalhos acentuados em chaves estáveis."""

    sem_acentos = "".join(
        caractere for caractere in normalize("NFKD", valor) if not combining(caractere)
    )

    return re.sub(r"[^a-z0-9]+", "_", sem_acentos.casefold()).strip("_")


def _texto(
    valor: str | None,
) -> str | None:
    """Limpa espaços e representa ausência com ``None``."""

    if valor is None:
        return None

    limpo = " ".join(valor.split())

    return limpo or None


def _inteiro_positivo(valor: str | None) -> int | None:
    """Converte somente números inteiros de vaga maiores que zero."""

    texto = _texto(valor)
    if texto is None:
        return None

    try:
        numero = int(texto)
    except ValueError:
        return None

    return numero if numero > 0 else None


def _identificador(
    prefixo: str,
    *partes: str,
) -> str:
    """Gera chave curta e determinística para reprocessamento diário."""

    base = "|".join(parte.strip().casefold() for parte in partes)

    return f"{prefixo}-{sha256(base.encode()).hexdigest()[:32]}"


def _data_iso(
    valor: str | None,
    *,
    mes_primeiro: bool = False,
) -> str | None:
    """Converte os formatos de data observados nos portais."""

    texto = _texto(valor)

    if texto is None:
        return None

    try:
        return date.fromisoformat(texto[:10]).isoformat()
    except ValueError:
        pass

    formatos = ("%m/%d/%Y", "%d/%m/%Y") if mes_primeiro else ("%d/%m/%Y", "%m/%d/%Y")

    for formato in formatos:
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except ValueError:
            continue

    return None


def _extrair_es(
    linhas: list[dict[str, str]],
    *,
    url: str,
) -> ResultadoExtracaoCkan:
    """Converte vagas atuais das Agências do Trabalhador do ES."""

    vagas: list[dict[str, Any]] = []
    ignoradas = 0
    invalidas = 0

    for linha in linhas:
        titulo = _texto(linha.get("vaga"))
        codigo = _texto(linha.get("codigo_da_vaga"))
        posto = _texto(linha.get("posto"))

        if titulo is None or codigo is None or posto is None:
            invalidas += 1
            continue

        detalhes = [
            _texto(linha.get("descricao_da_vaga")),
            _texto(linha.get("exigencia_de_qualificacao")),
            _texto(linha.get("exigencia_de_escolarizacao")),
        ]
        quantidade = _texto(linha.get("quantidade"))

        if quantidade is not None:
            detalhes.append(f"Quantidade de vagas: {quantidade}.")

        detalhes.append(
            "O empregador não é identificado no conjunto público; "
            f"a intermediação ocorre pelo posto {posto}."
        )

        documento: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "identifier": {
                "@type": "PropertyValue",
                "name": "SETADES ES",
                "value": _identificador("es", posto, codigo, titulo),
            },
            "title": titulo,
            "description": " ".join(parte for parte in detalhes if parte is not None),
            "url": url,
            "hiringOrganization": {
                "@type": "Organization",
                "name": f"Empregador não divulgado ({posto})",
            },
            "industry": "Intermediação pública de emprego",
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": posto,
                    "addressRegion": "ES",
                    "addressCountry": "BR",
                },
            },
            "_observatorio_ckan": {
                "portal": "dados.es.gov.br",
                "codigoVaga": codigo,
                "posto": posto,
                "quantidade": quantidade,
            },
        }

        publicada = _data_iso(
            linha.get("dataatualizacao"),
            mes_primeiro=True,
        )

        if publicada is not None:
            documento["datePosted"] = publicada

        if "estagio" in _normalizar_chave(titulo):
            documento["employmentType"] = "INTERN"

        vagas.append(documento)

    return ResultadoExtracaoCkan(
        vagas=tuple(vagas),
        linhas_encontradas=len(linhas),
        linhas_ignoradas=ignoradas,
        linhas_invalidas=invalidas,
    )


def _extrair_iftm(
    linhas: list[dict[str, str]],
    *,
    url: str,
    data_referencia: date,
) -> ResultadoExtracaoCkan:
    """Converte oportunidades vigentes do Instituto Federal do Triângulo Mineiro."""

    vagas: list[dict[str, Any]] = []
    ignoradas = 0
    invalidas = 0

    for linha in linhas:
        titulo = _texto(linha.get("vaga"))
        empresa = _texto(linha.get("concedente"))
        cidade = _texto(linha.get("cidade"))
        inicio = _data_iso(linha.get("dt_vigencia_inicio"))
        fim = _data_iso(linha.get("dt_vigencia_limite"))

        if titulo is None or empresa is None or cidade is None:
            invalidas += 1
            continue

        # O conjunto do IFTM conserva linhas históricas e algumas não trazem
        # prazo de vigência. Sem uma data final verificável não há como provar
        # que a oportunidade ainda aceita candidaturas; portanto, não a
        # transformamos em anúncio publicável.
        if fim is None or date.fromisoformat(fim) < data_referencia:
            ignoradas += 1
            continue

        tipo = _texto(linha.get("tipo_vaga"))
        quantidade = _texto(linha.get("no_qtd_vagas"))
        carga_horaria = _texto(linha.get("carga_horaria"))
        detalhes = [
            (f"Tipo da oportunidade: {tipo}." if tipo is not None else None),
            (f"Quantidade informada: {quantidade}." if quantidade is not None else None),
            (
                f"Carga horária informada: {carga_horaria} horas."
                if carga_horaria is not None
                else None
            ),
            "Oportunidade divulgada pelo Instituto Federal do Triângulo Mineiro.",
        ]
        documento: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "identifier": {
                "@type": "PropertyValue",
                "name": "IFTM",
                "value": _identificador(
                    "iftm",
                    empresa,
                    titulo,
                    cidade,
                    inicio or "sem_inicio",
                    fim or "sem_fim",
                ),
            },
            "title": titulo,
            "description": " ".join(parte for parte in detalhes if parte is not None),
            "url": url,
            "hiringOrganization": {
                "@type": "Organization",
                "name": empresa,
            },
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": cidade,
                    "addressRegion": "MG",
                    "addressCountry": "BR",
                },
            },
            "_observatorio_ckan": {
                "portal": "dadosabertos.iftm.edu.br",
                "tipoVaga": tipo,
                "quantidadeVagas": quantidade,
                "cargaHoraria": carga_horaria,
            },
        }

        if inicio is not None:
            documento["datePosted"] = inicio

        if fim is not None:
            documento["validThrough"] = fim

        tipo_normalizado = _normalizar_chave(tipo or "")

        if "estagio" in tipo_normalizado:
            documento["employmentType"] = "INTERN"

        vagas.append(documento)

    return ResultadoExtracaoCkan(
        vagas=tuple(vagas),
        linhas_encontradas=len(linhas),
        linhas_ignoradas=ignoradas,
        linhas_invalidas=invalidas,
    )


def _extrair_ufpe(
    linhas: list[dict[str, str]],
    *,
    url: str,
    data_referencia: date,
) -> ResultadoExtracaoCkan:
    """Agrupa cotas do mesmo edital e conserva inscrições abertas."""

    grupos: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    ignoradas = 0
    invalidas = 0

    for linha in linhas:
        status = _normalizar_chave(linha.get("status", ""))
        inicio = _data_iso(linha.get("inicio_inscricao"))
        fim = _data_iso(linha.get("fim_inscricao"))

        # O portal conserva "EM ANDAMENTO" por anos depois do fim das
        # inscrições. O intervalo de inscrição é a trava temporal real.
        if status == "finalizado" or inicio is None or fim is None:
            ignoradas += 1
            continue

        data_inicio = date.fromisoformat(inicio)
        data_fim = date.fromisoformat(fim)

        if not data_inicio <= data_referencia <= data_fim:
            ignoradas += 1
            continue

        chave = (
            (linha.get("id_edital_original", "").strip() or linha.get("id_edital", "").strip()),
            linha.get("numero_edital", "").strip(),
            linha.get("ano_edital", "").strip(),
            linha.get("tipo_concurso", "").strip(),
        )

        if not all(chave):
            invalidas += 1
            continue

        grupos.setdefault(chave, []).append(linha)

    vagas: list[dict[str, Any]] = []

    for (id_edital, numero, ano, tipo), registros in grupos.items():
        primeira = registros[0]
        total_vagas = sum(
            _maximo_inteiro(registros, coluna)
            for coluna in (
                "qnt_vagas_ampla_concorrencia",
                "qnt_vagas_pcd",
                "qnt_vagas_raciais",
            )
        )
        validade = _data_iso(primeira.get("fim_inscricao"))
        publicacao = _data_iso(primeira.get("data_dou"))
        titulo = f"{tipo} - Edital {numero}/{ano}"
        descricao = (
            f"Concurso público da UFPE, edital {numero}/{ano}. "
            f"Quantidade informada no conjunto de dados: {total_vagas} vaga(s)."
        )
        documento: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "identifier": {
                "@type": "PropertyValue",
                "name": "UFPE",
                "value": _identificador("ufpe", id_edital, numero, ano, tipo),
            },
            "title": titulo,
            "description": descricao,
            "url": url,
            "hiringOrganization": {
                "@type": "CollegeOrUniversity",
                "name": "Universidade Federal de Pernambuco",
                "description": (
                    "Universidade federal responsável pelo concurso "
                    "publicado no Portal de Dados Abertos da UFPE."
                ),
            },
            "industry": "Educação superior pública",
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": "Recife",
                    "addressRegion": "PE",
                    "addressCountry": "BR",
                },
            },
            "_observatorio_ckan": {
                "portal": "dados.ufpe.br",
                "idEdital": id_edital,
                "numeroEdital": numero,
                "anoEdital": ano,
                "quantidadeVagas": total_vagas,
                "status": primeira.get("status"),
            },
        }

        if publicacao is not None:
            documento["datePosted"] = publicacao

        if validade is not None:
            documento["validThrough"] = validade

        tipo_normalizado = _normalizar_chave(tipo)

        if "substituto" in tipo_normalizado or "temporario" in tipo_normalizado:
            documento["employmentType"] = "TEMPORARY"

        vagas.append(documento)

    return ResultadoExtracaoCkan(
        vagas=tuple(vagas),
        linhas_encontradas=len(linhas),
        linhas_ignoradas=ignoradas,
        linhas_invalidas=invalidas,
    )


def _maximo_inteiro(
    linhas: list[dict[str, str]],
    coluna: str,
) -> int:
    """Evita somar linhas repetidas do mesmo edital."""

    valores: list[int] = []

    for linha in linhas:
        texto = linha.get(coluna, "").strip()

        try:
            valores.append(int(float(texto.replace(",", "."))))
        except ValueError:
            continue

    return max(valores, default=0)
