"""Conversão de um JobPosting JSON-LD para o domínio da aplicação."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from typing import Any

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.enums import Fonte, StatusAnuncio


def _texto(valor: object) -> str | None:
    """Converte valores simples em texto não vazio."""

    # Booleanos não devem virar os textos "True" e "False".
    if isinstance(valor, bool) or valor is None:
        return None

    if not isinstance(
        valor,
        (
            str,
            int,
            float,
        ),
    ):
        return None

    texto = str(valor).strip()

    return texto or None


def _primeiro_objeto(
    valor: object,
) -> dict[str, Any] | None:
    """Obtém o primeiro dicionário de um campo simples ou de uma lista."""

    if isinstance(valor, dict):
        return valor

    if isinstance(valor, list):
        for item in valor:
            if isinstance(item, dict):
                return item

    return None


def _juntar_textos(
    valores: object,
) -> str | None:
    """Transforma texto ou lista em uma representação original legível."""

    if isinstance(valores, list):
        partes = [texto for item in valores if (texto := _texto(item)) is not None]

        return ", ".join(partes) or None

    return _texto(valores)


def _converter_data(
    valor: object,
) -> date | datetime | None:
    """Converte datas ISO do JSON-LD sem inventar valores."""

    texto = _texto(valor)

    if texto is None:
        return None

    try:
        # A presença de T normalmente indica data e hora.
        if "T" in texto:
            # O Python entende +00:00, enquanto algumas fontes usam Z.
            texto_normalizado = texto.removesuffix("Z") + ("+00:00" if texto.endswith("Z") else "")

            return datetime.fromisoformat(texto_normalizado)

        return date.fromisoformat(texto)

    except ValueError:
        # Uma data inválida será tratada posteriormente como ausente.
        return None


def _extrair_id_externo(
    documento: dict[str, Any],
    url: str,
) -> str:
    """Obtém um identificador estável com no máximo 50 caracteres."""

    identificador = documento.get("identifier")

    if isinstance(identificador, dict):
        candidato = (
            _texto(identificador.get("value"))
            or _texto(identificador.get("@id"))
            or _texto(identificador.get("name"))
        )
    else:
        candidato = _texto(identificador)

    # Podemos preservar o identificador original quando ele cabe no limite
    # aceito pelo futuro campo externalJobPostingId.
    if candidato is not None and len(candidato) <= 50:
        return candidato

    # Quando a fonte não fornece um identificador curto, usamos um hash
    # determinístico. A mesma URL sempre produzirá o mesmo identificador.
    base = candidato or url

    return sha256(base.encode()).hexdigest()[:50]


def _extrair_endereco(
    documento: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Obtém localidade completa, endereço e código postal."""

    local = _primeiro_objeto(documento.get("jobLocation"))

    if local is None:
        return None, None, None

    endereco = _primeiro_objeto(local.get("address"))

    if endereco is None:
        return None, None, None

    nomes_campos = (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    )

    partes = [texto for nome in nomes_campos if (texto := _texto(endereco.get(nome))) is not None]

    localidade_completa = ", ".join(partes) or None
    endereco_original = _texto(endereco.get("streetAddress"))
    cep_original = _texto(endereco.get("postalCode"))

    return (
        localidade_completa,
        endereco_original,
        cep_original,
    )


def _extrair_empresa(
    documento: dict[str, Any],
) -> str | None:
    """Obtém o nome da organização contratante."""

    empresa = _primeiro_objeto(documento.get("hiringOrganization"))

    if empresa is None:
        return None

    return _texto(empresa.get("name"))


def _extrair_salario(
    documento: dict[str, Any],
) -> str | None:
    """Transforma baseSalary em texto sem normalizar seus valores."""

    salario = _primeiro_objeto(documento.get("baseSalary"))

    if salario is None:
        return None

    moeda = _texto(salario.get("currency"))
    valor = salario.get("value")

    if isinstance(valor, dict):
        valor_principal = _texto(valor.get("value"))

        if valor_principal is None:
            minimo = _texto(valor.get("minValue"))
            maximo = _texto(valor.get("maxValue"))

            if minimo is not None and maximo is not None:
                valor_principal = f"{minimo} - {maximo}"
            else:
                valor_principal = minimo or maximo

        periodo = _texto(valor.get("unitText"))

    else:
        valor_principal = _texto(valor)
        periodo = None

    partes = [
        parte
        for parte in (
            valor_principal,
            moeda,
            periodo,
        )
        if parte is not None
    ]

    return " ".join(partes) or None


def converter_job_posting_em_anuncio(
    documento: dict[str, Any],
    *,
    fonte: Fonte,
    hash_conteudo: str,
    referencia_bruta: str,
    # Identifica exatamente qual alvo do catálogo produziu o anúncio.
    #
    # Ele fica opcional porque alguns testes e dados antigos ainda podem
    # chamar esta função sem possuir essa informação.
    alvo_id: str | None = None,
    # URL da página usada como alternativa quando o JSON-LD
    # não informa a URL pública da vaga.
    url_fallback: str | None = None,
    # URL real usada pelo candidato para se inscrever.
    #
    # Ela é encontrada no HTML pelo extrator de candidatura.
    url_candidatura: str | None = None,
    observado_em: datetime | None = None,
) -> AnuncioVaga:
    """Converte um JobPosting estruturado em anúncio auditável."""

    titulo = _texto(documento.get("title"))

    if titulo is None:
        raise ValueError("JobPosting não possui title")

    # A URL do JSON-LD é preferida.
    #
    # A URL da resposta pode ser usada quando o documento não a informar.
    url = _texto(documento.get("url")) or _texto(url_fallback)

    if url is None:
        raise ValueError("JobPosting não possui url")

    localidade, endereco, cep = _extrair_endereco(documento)

    # A observação pertence ao momento da coleta, não ao momento
    # em que este extrator foi executado.
    momento_observacao = observado_em or agora_utc()
    return AnuncioVaga(
        # Guardamos tanto o tipo geral da fonte quanto o alvo específico.
        fonte=fonte,
        alvo_id=alvo_id,
        id_externo=_extrair_id_externo(
            documento,
            url,
        ),
        # Página pública que apresenta os detalhes da vaga.
        url=url,
        # Página ou formulário usado para realizar a candidatura.
        #
        # Esses endereços podem ser diferentes.
        url_candidatura=url_candidatura,
        titulo_original=titulo,
        descricao_original=_texto(documento.get("description")) or "",
        empresa_original=_extrair_empresa(documento),
        localidade_original=localidade,
        endereco_original=endereco,
        cep_original=cep,
        salario_original=_extrair_salario(documento),
        modalidade_original=_juntar_textos(
            documento.get("jobLocationType"),
        ),
        regime_original=_juntar_textos(
            documento.get("employmentType"),
        ),
        beneficios_original=_texto(documento.get("jobBenefits")),
        publicado_em=_converter_data(documento.get("datePosted")),
        expira_em=_converter_data(documento.get("validThrough")),
        status=StatusAnuncio.DESCOBERTO,
        hash_conteudo=hash_conteudo,
        referencia_bruta=referencia_bruta,
        # Preservamos o documento inteiro para auditoria e reprocessamento.
        # Preservamos o documento inteiro para auditoria e reprocessamento.
        campos_estruturados=dict(documento),
        # Estes campos precisam ficar dentro de AnuncioVaga(...).
        primeira_observacao_em=momento_observacao,
        ultima_observacao_em=momento_observacao,
    )
