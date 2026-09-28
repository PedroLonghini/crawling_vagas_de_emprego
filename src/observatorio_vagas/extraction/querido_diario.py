"""Extração conservadora de seleções na API pública do Querido Diário."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from parsel import Selector

from observatorio_vagas.extraction.cnpj_documento import extrair_cnpjs_texto

_TERMOS_SELECAO = (
    "chamada publica",
    "credenciamento",
    "contratacao de pessoal",
    "contratacao temporaria",
    "processo seletivo",
    "selecao simplificada",
)
_TERMOS_INTERMEDIACAO = (
    "agencia do trabalhador",
    "balcao de empregos",
    "casa do trabalhador",
    "intermediacao de mao de obra",
    "sine",
)
_TERMOS_OPORTUNIDADE = (
    "agente",
    "cargo",
    "candidato",
    "emprego",
    "enfermeir",
    "funcionario",
    "inscricao",
    "medic",
    "pessoal",
    "professor",
    "profissional",
    "servidor",
    "tecnic",
    "vaga",
)
_SINAIS_ABERTURA = (
    "abertura do certame",
    "abertura de inscricoes",
    "cadastro reserva",
    "cadastro de reserva",
    "chamamento para inscricao",
    "contratacao temporaria",
    "edital de abertura",
    "edital para preenchimento",
    "inscricoes estarao abertas",
    "inscricoes no periodo",
    "inscricoes serao realizadas",
    "inscricoes abertas",
    "inscricoes ate",
    "oferta de vagas",
    "periodo de inscricoes",
    "prazo de inscricao",
    "prazo para inscricao",
    "preenchimento de vagas",
    "recebera inscricoes",
    "recebimento de inscricoes",
    "selecao de candidatos",
    "vagas disponiveis",
)
_SINAIS_ENCERRAMENTO = (
    "candidato aprovado",
    "candidatos aprovados",
    "edital de convocacao",
    "homologacao do resultado",
    "inscricoes encerradas",
    "lista de classificacao",
    "processo seletivo encerrado",
    "resultado preliminar",
    "resultado definitivo",
    "resultado final",
)
_TERMOS_CONTEUDO_EXCLUIDO = (
    "concurso publico",
    "edital",
    "licitacao",
    "pregao",
)
_INICIOS_CARGO_INVALIDOS = (
    "abaixo citado",
    "contratacao temporaria",
    "correspondente",
    "e ",
    "em ",
    "exonerado",
    "ou ",
    "pretendid",
    "provimento",
)
_TERMOS_CARGO_INVALIDOS = (
    "concurso publico",
    "conforme edital",
    "em virtude",
    "esferas municipal",
    "periodo motivo",
    "recebimento ou nao",
)
_PADRAO_FIM_INSCRICAO = re.compile(
    r"(?:inscri(?:ç|c)(?:ão|ões|ao|oes)|"
    r"prazo\s+de\s+inscri(?:ç|c)(?:ão|ões|ao|oes))"
    r"[^.\n]{0,180}?"
    r"(?:at[eé]|encerra(?:m|das?)?(?:\s+em)?|fim(?:\s+em|\s+das)?)"
    r"[^0-9]{0,30}"
    r"(?P<dia>\d{1,2})/(?P<mes>\d{1,2})/(?P<ano>\d{4})",
    flags=re.IGNORECASE,
)
_PADRAO_PERIODO_INSCRICOES = re.compile(
    r"(?:periodo\s+de\s+inscri(?:ç|c)(?:ão|ões|ao|oes)|"
    r"inscri(?:ç|c)(?:ão|ões|ao|oes))"
    r"[^.\n]{0,160}?"
    r"(?:de|entre)\s+\d{1,2}/\d{1,2}/\d{4}"
    r"\s+(?:a|at[eé])\s+"
    r"(?P<dia>\d{1,2})/(?P<mes>\d{1,2})/(?P<ano>\d{4})",
    flags=re.IGNORECASE,
)
_PADRAO_CARGO = re.compile(
    r"(?:cargo|fun(?:ç|c)(?:ão|oes))\s+(?:p[uú]blic[oa]\s+)?(?:de\s+)?"
    r"(?P<cargo>[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ /-]{2,80}?)"
    r"(?=\s+(?:com|para)\b|[,;.\n]|$)",
    flags=re.IGNORECASE,
)
_PADRAO_CARGO_CONTRATACAO = re.compile(
    r"(?:contrata(?:ç|c)(?:ão|ao)\s+(?:tempor[aá]ria\s+)?de|"
    r"para\s+atuar\s+como)\s+"
    r"(?P<cargo>[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ /-]{2,80}?)"
    r"(?=[,;.\n]|\s+(?:com|para|no|na)\b|$)",
    flags=re.IGNORECASE,
)
_PADRAO_CARGO_VAGA_DIRETA = re.compile(
    r"(?:vaga(?:s)?\s+(?:dispon[ií]ve(?:l|is)\s+)?para|vaga(?:s)?\s+de)\s+"
    r"(?P<cargo>[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ /-]{2,80}?)"
    r"(?=[,;.\n]|\s+(?:com|para|no|na|interessados)\b|$)",
    flags=re.IGNORECASE,
)
_URL_LICENCA = "https://creativecommons.org/licenses/by/4.0/deed.pt-br"


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoQueridoDiario:
    """Documentos de vaga e métricas da resposta da API."""

    vagas: tuple[dict[str, Any], ...]
    diarios_encontrados: int
    excertos_analisados: int
    excertos_ignorados: int
    itens_invalidos: int


def _carregar_json(
    conteudo: str | bytes,
    codificacao: str,
) -> Mapping[str, Any]:
    """Lê a resposta sem aceitar uma estrutura inesperada silenciosamente."""

    if isinstance(conteudo, bytes):
        if not codificacao.strip():
            raise ValueError("codificacao não pode ser vazia")

        texto = conteudo.decode(codificacao)
    elif isinstance(conteudo, str):
        texto = conteudo
    else:
        raise TypeError("conteudo precisa ser texto ou bytes")

    try:
        dados = json.loads(texto)
    except (json.JSONDecodeError, UnicodeError) as erro:
        raise ValueError("resposta do Querido Diário não contém JSON válido") from erro

    if not isinstance(dados, Mapping):
        raise ValueError("resposta do Querido Diário precisa ser um objeto JSON")

    return dados


def _texto(
    valor: object,
) -> str | None:
    """Obtém um texto não vazio sem converter tipos arbitrários."""

    if not isinstance(valor, str):
        return None

    texto = " ".join(valor.split())

    return texto or None


def _texto_do_excerto(
    valor: object,
) -> str | None:
    """Remove marcação de destaque sem executar ou confiar no HTML."""

    texto = _texto(valor)

    if texto is None:
        return None

    seletor = Selector(text=f"<div>{texto}</div>")

    return _texto(seletor.xpath("string(//div)").get())


def _normalizar_busca(
    valor: str,
) -> str:
    """Remove acentos e normaliza caixa somente para reconhecimento."""

    decomposto = unicodedata.normalize("NFKD", valor)

    return "".join(
        caractere for caractere in decomposto if not unicodedata.combining(caractere)
    ).casefold()


def _parece_oportunidade_publica(
    texto: str,
) -> bool:
    """Exige vaga aberta, sem incluir concursos, editais ou contratações públicas."""

    normalizado = _normalizar_busca(texto)

    return (
        not any(termo in normalizado for termo in _TERMOS_CONTEUDO_EXCLUIDO)
        and any(
            termo in normalizado
            for termo in (*_TERMOS_SELECAO, *_TERMOS_INTERMEDIACAO)
        )
        and any(termo in normalizado for termo in _TERMOS_OPORTUNIDADE)
        and any(sinal in normalizado for sinal in _SINAIS_ABERTURA)
        and not any(sinal in normalizado for sinal in _SINAIS_ENCERRAMENTO)
    )


def _cargo_plausivel(
    valor: str,
) -> bool:
    """Evita transformar fragmentos jurídicos em nomes de cargo."""

    normalizado = _normalizar_busca(valor)

    if any(normalizado.startswith(inicio) for inicio in _INICIOS_CARGO_INVALIDOS):
        return False

    return not any(termo in normalizado for termo in _TERMOS_CARGO_INVALIDOS)


def _url_http(
    valor: object,
) -> str | None:
    """Aceita somente URLs HTTP completas retornadas pela API."""

    texto = _texto(valor)

    if texto is None:
        return None

    endereco = urlsplit(texto)

    if endereco.scheme not in {"http", "https"} or not endereco.netloc:
        return None

    return texto


def _data_iso(
    valor: object,
) -> str | None:
    """Valida uma data ISO antes de colocá-la no documento estruturado."""

    texto = _texto(valor)

    if texto is None:
        return None

    try:
        return date.fromisoformat(texto[:10]).isoformat()
    except ValueError:
        return None


def _fim_inscricoes(
    texto: str,
) -> str | None:
    """Extrai somente uma data ligada explicitamente ao fim das inscrições."""

    correspondencia = _PADRAO_FIM_INSCRICAO.search(texto)

    if correspondencia is None:
        correspondencia = _PADRAO_PERIODO_INSCRICOES.search(texto)

    if correspondencia is None:
        return None

    try:
        resultado = date(
            int(correspondencia.group("ano")),
            int(correspondencia.group("mes")),
            int(correspondencia.group("dia")),
        )
    except ValueError:
        return None

    return resultado.isoformat()


def _cnpj_municipio(
    *,
    texto: str,
    municipio: str,
) -> str | None:
    """Aceita um CNPJ somente quando a evidência o liga ao município."""

    candidatos = extrair_cnpjs_texto(texto)

    if len(candidatos) != 1:
        return None

    evidencia = _normalizar_busca(candidatos[0].trecho_evidencia)
    nome_municipio = _normalizar_busca(municipio)

    if nome_municipio not in evidencia:
        return None

    if "municipio" not in evidencia and "prefeitura" not in evidencia:
        return None

    return candidatos[0].cnpj


def _titulo(
    texto: str,
    municipio: str,
    estado: str,
) -> str:
    """Usa o cargo explícito ou um título genérico que não inventa função."""

    for padrao in (
        _PADRAO_CARGO,
        _PADRAO_CARGO_CONTRATACAO,
        _PADRAO_CARGO_VAGA_DIRETA,
    ):
        correspondencia = padrao.search(texto)

        if correspondencia is None:
            continue

        cargo = _texto(correspondencia.group("cargo"))

        if cargo is not None and _cargo_plausivel(cargo):
            return cargo

    local = f"{municipio}/{estado}" if estado else municipio

    return f"Processo seletivo público - {local}"


def _identificador(
    *,
    territorio_id: str,
    data_publicacao: str,
    checksum: str | None,
    texto: str,
) -> str:
    """Gera ID estável mesmo quando a consulta da API for alterada."""

    base = "|".join(
        (
            territorio_id,
            data_publicacao,
            checksum or "",
            texto,
        )
    )

    return f"qd-{sha256(base.encode()).hexdigest()[:47]}"


def _descricao_com_atribuicao(
    *,
    texto: str,
    municipio: str,
    url: str,
) -> str:
    """Preserva a atribuição exigida pela licença aberta da plataforma."""

    atribuicao = (
        "Fonte: Querido Diário / Open Knowledge Brasil e Diário Oficial "
        f"do Município de {municipio}. Licença CC BY 4.0: {_URL_LICENCA}. "
        "Conteúdo extraído e formatado pelo Observatório de Vagas. "
        f"Documento original: {url}"
    )

    return f"{texto}\n\n{atribuicao}"


def _converter_excerto(
    *,
    diario: Mapping[str, Any],
    texto: str,
) -> dict[str, Any] | None:
    """Converte um excerto confirmado em um JobPosting auditável."""

    territorio_id = _texto(diario.get("territory_id"))
    municipio = _texto(diario.get("territory_name"))
    estado = (_texto(diario.get("state_code")) or "").upper()
    data_publicacao = _data_iso(diario.get("date"))
    url = _url_http(diario.get("url"))

    if territorio_id is None or municipio is None or data_publicacao is None:
        return None

    if url is None:
        url = _url_http(diario.get("txt_url"))

    if url is None:
        return None

    checksum = _texto(diario.get("checksum")) or _texto(diario.get("file_checksum"))
    validade = _fim_inscricoes(texto)
    cnpj = _cnpj_municipio(
        texto=texto,
        municipio=municipio,
    )
    normalizado = _normalizar_busca(texto)
    empresa_nome = f"Município de {municipio}"
    organizacao: dict[str, Any] = {
        "@type": "GovernmentOrganization",
        "name": empresa_nome,
        "description": (f"{empresa_nome}, órgão da administração pública municipal."),
    }

    if cnpj is not None:
        organizacao["taxID"] = cnpj

    documento: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {
            "@type": "PropertyValue",
            "name": "Querido Diário",
            "value": _identificador(
                territorio_id=territorio_id,
                data_publicacao=data_publicacao,
                checksum=checksum,
                texto=texto,
            ),
        },
        "title": _titulo(texto, municipio, estado),
        "description": _descricao_com_atribuicao(
            texto=texto,
            municipio=municipio,
            url=url,
        ),
        "datePosted": data_publicacao,
        "url": url,
        "hiringOrganization": organizacao,
        "industry": "Administração Pública",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": municipio,
                "addressRegion": estado,
                "addressCountry": "BR",
            },
        },
        "_observatorio_querido_diario": {
            "territoryId": territorio_id,
            "edition": _texto(diario.get("edition")),
            "isExtraEdition": diario.get("is_extra_edition"),
            "textUrl": _url_http(diario.get("txt_url")),
            "checksum": checksum,
            "license": "CC BY 4.0",
            "licenseUrl": _URL_LICENCA,
            "attributionRequired": True,
        },
    }

    if validade is not None:
        documento["validThrough"] = validade

    if "contratacao temporaria" in normalizado:
        documento["employmentType"] = "TEMPORARY"

    return documento


def extrair_job_postings_querido_diario(
    conteudo: str | bytes,
    *,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoQueridoDiario:
    """Converte excertos relevantes de ``/gazettes`` em candidatos a vaga."""

    dados = _carregar_json(conteudo, codificacao)
    diarios = dados.get("gazettes")

    if not isinstance(diarios, list):
        raise ValueError("resposta do Querido Diário não possui a lista gazettes")

    vagas: list[dict[str, Any]] = []
    excertos_analisados = 0
    excertos_ignorados = 0
    itens_invalidos = 0

    for item in diarios:
        if not isinstance(item, Mapping):
            itens_invalidos += 1
            continue

        excertos = item.get("excerpts")

        if not isinstance(excertos, list):
            itens_invalidos += 1
            continue

        for excerto in excertos:
            texto = _texto_do_excerto(excerto)
            excertos_analisados += 1

            if texto is None or not _parece_oportunidade_publica(texto):
                excertos_ignorados += 1
                continue

            documento = _converter_excerto(
                diario=item,
                texto=texto,
            )

            if documento is None:
                itens_invalidos += 1
                continue

            vagas.append(documento)

    return ResultadoExtracaoQueridoDiario(
        vagas=tuple(vagas),
        diarios_encontrados=len(diarios),
        excertos_analisados=excertos_analisados,
        excertos_ignorados=excertos_ignorados,
        itens_invalidos=itens_invalidos,
    )
