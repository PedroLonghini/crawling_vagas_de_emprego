"""Fallback conservador para páginas de detalhe sem JSON-LD."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from parsel import Selector

TITULOS_GENERICOS = frozenset(
    {
        "carreiras",
        "empregos",
        "jobs",
        "oportunidades",
        "trabalhe conosco",
        "vagas",
    }
)

# Rótulos explícitos podem aparecer em parágrafos vizinhos. O seletor HTML
# entrega alguns desses textos como uma sequência única; esta lista impede que
# o valor de um campo absorva o rótulo do próximo.
ROTULOS_CAMPOS_VISIVEIS = (
    "modalidade",
    "modelo de trabalho",
    "formato de trabalho",
    "workplace type",
    "tipo de contrato",
    "regime de contratação",
    "employment type",
    "tipo de emprego",
    "tipo de vaga",
    "senioridade",
    "nível",
    "experience level",
    "local",
    "localidade",
    "cidade",
    "location",
    "local de trabalho",
    "salário",
    "salario",
    "faixa salarial",
    "remuneração",
    "remuneracao",
    "salary",
    "inscrições até",
    "inscricoes ate",
    "prazo de inscrição",
    "prazo de inscricao",
    "candidaturas até",
    "candidaturas ate",
    "prazo para candidatura",
    "data de encerramento",
    "application deadline",
    "candidate by",
    "benefícios",
    "beneficios",
    "benefits",
    "publicada em",
    "publicado em",
    "data de publicação",
    "data de publicacao",
    "posted on",
)


@dataclass(frozen=True, slots=True)
class ResultadoHTMLGenerico:
    """Documentos produzidos somente quando há evidência suficiente."""

    vagas: tuple[dict[str, Any], ...]


def _limpar_texto(valor: str | None) -> str | None:
    """Remove espaços repetidos e rejeita textos vazios."""

    if valor is None:
        return None

    texto = " ".join(valor.split()).strip()

    return texto or None


def _primeiro_texto(seletor: Selector, expressoes: tuple[str, ...]) -> str | None:
    """Retorna o primeiro seletor que realmente contém texto."""

    for expressao in expressoes:
        valor = _limpar_texto(seletor.xpath(expressao).get())

        if valor is not None:
            return valor

    return None


def _primeiro_texto_com_tamanho_minimo(
    seletor: Selector,
    expressoes: tuple[str, ...],
    *,
    tamanho_minimo: int,
) -> str | None:
    """Retorna a primeira alternativa que possui conteúdo suficiente."""

    for expressao in expressoes:
        valor = _limpar_texto(seletor.xpath(expressao).get())

        if valor is not None and len(valor) >= tamanho_minimo:
            return valor

    return None


def _valor_rotulado(seletor: Selector, rotulos: tuple[str, ...]) -> str | None:
    """Obtém valor curto exibido após um rótulo explícito da página."""

    valor_estruturado = _valor_rotulado_estruturado(seletor, rotulos)
    if valor_estruturado is not None:
        return valor_estruturado

    texto = " ".join(seletor.xpath("//body//text()").getall())
    alternativas = "|".join(re.escape(rotulo) for rotulo in rotulos)
    proximo_rotulo = "|".join(re.escape(rotulo) for rotulo in ROTULOS_CAMPOS_VISIVEIS)
    correspondencia = re.search(
        rf"(?:{alternativas})\s*:\s*(?P<valor>.{{1,80}}?)(?=\s*(?:\||•|/|(?:{proximo_rotulo})\s*:|$))",
        texto,
        flags=re.IGNORECASE,
    )

    return _limpar_texto(correspondencia.group("valor")) if correspondencia else None


def _valor_rotulado_estruturado(seletor: Selector, rotulos: tuple[str, ...]) -> str | None:
    """Lê pares ``rótulo/valor`` em tabelas, listas de definição e cards.

    Muitos portais usam ``<dt>Local</dt><dd>…</dd>`` ou
    ``<th>Contrato</th><td>…</td>`` e não escrevem dois-pontos. Esses formatos
    são estruturados e, por isso, mais confiáveis que procurar texto livre.
    """

    rotulos_normalizados = {_normalizar_rotulo(rotulo) for rotulo in rotulos}
    candidatos = seletor.xpath("//dt | //th | //*[@data-label]")
    for candidato in candidatos:
        if candidato.root.tag == "dt":
            valor = candidato.xpath("following-sibling::dd[1]")
        elif candidato.root.tag == "th":
            valor = candidato.xpath("following-sibling::td[1]")
        else:
            valor = candidato
        rotulo = _limpar_texto(
            candidato.attrib.get("data-label") or candidato.xpath("string(.)").get()
        )
        if rotulo is None or _normalizar_rotulo(rotulo) not in rotulos_normalizados:
            continue
        texto = _limpar_texto(valor.xpath("string(.)").get())
        if texto is not None and texto != rotulo:
            return texto
    return None


def _normalizar_rotulo(valor: str) -> str:
    """Remove somente pontuação que costuma acompanhar um rótulo de campo."""

    return " ".join(valor.casefold().strip(" :.-").split())


def _extrair_salario_rotulado(seletor: Selector) -> dict[str, Any] | None:
    """Converte somente uma faixa salarial em reais indicada explicitamente."""

    texto = _valor_rotulado(
        seletor,
        (
            "salário",
            "salario",
            "faixa salarial",
            "remuneração",
            "remuneracao",
            "salary",
        ),
    )
    if texto is None or not re.search(r"\br\$|\bbrl\b", texto, flags=re.IGNORECASE):
        return None

    numeros = re.findall(
        r"\b\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?\b|\b\d+(?:,\d{1,2})?\b",
        texto,
    )
    if not numeros:
        return None

    valores = [_normalizar_numero_monetario(numero) for numero in numeros[:2]]
    if any(valor is None for valor in valores):
        return None

    valor: dict[str, Any] = {
        "@type": "QuantitativeValue",
        "minValue": valores[0],
        "maxValue": valores[-1],
    }
    texto_normalizado = texto.casefold()
    if any(termo in texto_normalizado for termo in ("mensal", "mês", "mes", "month")):
        valor["unitText"] = "MONTH"
    elif any(termo in texto_normalizado for termo in ("anual", "ano", "year")):
        valor["unitText"] = "YEAR"

    return {
        "@type": "MonetaryAmount",
        "currency": "BRL",
        "value": valor,
    }


def _normalizar_numero_monetario(valor: str) -> str | None:
    """Transforma ``3.500,00`` em texto decimal aceito pelo normalizador."""

    texto = valor.strip()
    if not texto:
        return None

    if "." in texto and "," in texto:
        return texto.replace(".", "").replace(",", ".")

    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", texto):
        return texto.replace(".", "")

    return texto.replace(",", ".")


def _numero_monetario_microdado(valor: str | None) -> str | None:
    """Aceita somente números decimais declarados em microdados."""

    if valor is None:
        return None

    normalizado = _normalizar_numero_monetario(valor)
    if normalizado is None or re.fullmatch(r"\d+(?:\.\d{1,2})?", normalizado) is None:
        return None

    return normalizado


def _extrair_salario_microdados(seletor: Selector) -> dict[str, Any] | None:
    """Lê ``baseSalary`` do schema.org quando todos os valores são explícitos."""

    salario = seletor.xpath("//*[@itemprop='baseSalary'][1]")
    if not salario:
        return None

    moeda = _primeiro_texto(
        salario,
        (
            ".//*[@itemprop='currency']/@content",
            ".//*[@itemprop='currency']//text()",
        ),
    )
    if moeda is None or re.fullmatch(r"[A-Za-z]{3}", moeda) is None:
        return None

    minimo = _numero_monetario_microdado(
        _primeiro_texto(
            salario,
            (
                ".//*[@itemprop='minValue']/@content",
                ".//*[@itemprop='minValue']//text()",
            ),
        )
    )
    maximo = _numero_monetario_microdado(
        _primeiro_texto(
            salario,
            (
                ".//*[@itemprop='maxValue']/@content",
                ".//*[@itemprop='maxValue']//text()",
            ),
        )
    )
    if minimo is None and maximo is None:
        return None

    valor: dict[str, Any] = {
        "@type": "QuantitativeValue",
        "minValue": minimo or maximo,
        "maxValue": maximo or minimo,
    }
    periodo = _primeiro_texto(
        salario,
        (
            ".//*[@itemprop='unitText']/@content",
            ".//*[@itemprop='unitText']//text()",
        ),
    )
    if periodo is not None:
        valor["unitText"] = periodo

    return {
        "@type": "MonetaryAmount",
        "currency": moeda.upper(),
        "value": valor,
    }


def _extrair_prazo_rotulado(seletor: Selector) -> str | None:
    """Converte uma data brasileira explícita de candidatura para ISO."""

    rotulos = (
        "inscrições até",
        "inscricoes ate",
        "prazo de inscrição",
        "prazo de inscricao",
        "candidaturas até",
        "candidaturas ate",
        "prazo para candidatura",
        "data de encerramento",
        "application deadline",
        "candidate by",
    )
    return _converter_data_rotulada(seletor, rotulos)


def _extrair_data_publicacao_rotulada(seletor: Selector) -> str | None:
    """Converte data de publicação explicitamente exibida para ISO."""

    rotulos = (
        "publicada em",
        "publicado em",
        "data de publicação",
        "data de publicacao",
        "posted on",
    )
    return _converter_data_rotulada(seletor, rotulos)


def _converter_data_rotulada(seletor: Selector, rotulos: tuple[str, ...]) -> str | None:
    """Converte datas explícitas junto de um rótulo em formatos BR e ISO."""

    valor_estruturado = _valor_rotulado_estruturado(seletor, rotulos)
    texto = valor_estruturado or " ".join(seletor.xpath("//body//text()").getall())
    alternativas = "|".join(re.escape(rotulo) for rotulo in rotulos)
    correspondencia = re.search(
        rf"(?:{alternativas})\s*:?\s*(\d{{2}}/\d{{2}}/\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|"
        r"\d{1,2}\s+de\s+[A-Za-zç]+\s+de\s+\d{4})",
        texto,
        flags=re.IGNORECASE,
    )
    if correspondencia is None and valor_estruturado is not None:
        # Em um par estruturado, o rótulo não aparece no valor novamente.
        correspondencia = re.search(
            r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|"
            r"\d{1,2}\s+de\s+[A-Za-zç]+\s+de\s+\d{4})",
            texto,
            flags=re.IGNORECASE,
        )
    return _converter_data_texto(correspondencia.group(1)) if correspondencia else None


def _converter_data_texto(valor: str) -> str | None:
    """Aceita data em ISO, numérica brasileira ou mês escrito em português."""

    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor, formato).date().isoformat()
        except ValueError:
            continue
    meses = {
        "janeiro": 1,
        "fevereiro": 2,
        "marco": 3,
        "março": 3,
        "abril": 4,
        "maio": 5,
        "junho": 6,
        "julho": 7,
        "agosto": 8,
        "setembro": 9,
        "outubro": 10,
        "novembro": 11,
        "dezembro": 12,
    }
    correspondencia = re.fullmatch(
        r"\s*(\d{1,2})\s+de\s+([A-Za-zç]+)\s+de\s+(\d{4})\s*",
        valor,
        flags=re.IGNORECASE,
    )
    if correspondencia is None:
        return None
    mes = meses.get(correspondencia.group(2).casefold())
    if mes is None:
        return None
    try:
        return (
            datetime(int(correspondencia.group(3)), mes, int(correspondencia.group(1)))
            .date()
            .isoformat()
        )
    except ValueError:
        return None


def _primeira_data_iso(seletor: Selector, expressoes: tuple[str, ...]) -> str | None:
    """Aceita datas ISO de microdados sem tentar interpretar texto livre."""

    valor = _primeiro_texto(seletor, expressoes)
    if valor is None:
        return None

    correspondencia = re.match(r"(?P<data>\d{4}-\d{2}-\d{2})", valor)
    if correspondencia is None:
        return None

    try:
        return datetime.strptime(correspondencia.group("data"), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def extrair_job_posting_html_generico(
    corpo: bytes,
    *,
    url: str,
    empresa_nome: str | None,
    codificacao: str = "utf-8",
) -> ResultadoHTMLGenerico:
    """Extrai uma vaga estática sem inventar campos ausentes.

    Este extrator deve ser chamado apenas para uma página que o crawler já
    classificou como detalhe de vaga. Título e descrição longa são obrigatórios
    para reduzir falsos positivos.
    """

    if not isinstance(corpo, bytes):
        raise TypeError("corpo precisa ser bytes")

    if not isinstance(url, str) or not url.strip():
        raise ValueError("url precisa ser um texto não vazio")

    texto_html = corpo.decode(codificacao, errors="replace")
    seletor = Selector(text=texto_html)

    titulo = _primeiro_texto(
        seletor,
        (
            "//*[@itemprop='title']//text()",
            "string(//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' post-vaga-title ')][1])",
            "//h1//text()",
            "//meta[@property='og:title']/@content",
            "//title/text()",
        ),
    )
    descricao_seletores = (
        "string(//*[@itemprop='description'][1])",
        "string(//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' job-description ')][1])",
        "string(//*[@id='job-description'][1])",
        "string(//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' vagas-container ')][1])",
        "string(//*[contains(@class, 'description')][1])",
        "//meta[@property='og:description']/@content",
        "//meta[@name='description']/@content",
    )
    if urlsplit(url).hostname == "vagas.codam.com.br":
        descricao_seletores = (
            "string(//*[@data-testid='section-Descrição da vaga-title']/parent::*[1])",
        ) + descricao_seletores
    if urlsplit(url).hostname == "ats.abler.com.br":
        titulo = titulo or _primeiro_texto(seletor, ("//h2[1]//text()", "//h3[1]//text()"))
        descricao_seletores = (
            "string(//*[contains(@class, 'job-description')][1])",
            "string(//*[self::h2 or self::h3][contains(translate(normalize-space(.), 'SOBREVAGA', 'sobrevaga'), 'sobre a vaga')]/following::*[self::div or self::section][1])",
        ) + descricao_seletores
    descricao = _primeiro_texto_com_tamanho_minimo(
        seletor,
        descricao_seletores,
        tamanho_minimo=80,
    )

    if titulo is None or titulo.casefold() in TITULOS_GENERICOS:
        return ResultadoHTMLGenerico(vagas=())

    if descricao is None:
        return ResultadoHTMLGenerico(vagas=())

    empresa = _primeiro_texto(
        seletor,
        (
            "string(//*[@itemprop='hiringOrganization'][1]//*[@itemprop='name'][1])",
            "string(//*[contains(concat(' ', normalize-space(@class), ' '), ' company-name ')][1])",
            "//meta[@property='og:site_name']/@content",
        ),
    ) or _limpar_texto(empresa_nome)
    localidade_seletores = (
        "string(//*[@itemprop='jobLocation'][1])",
        "string(//*[contains(concat(' ', normalize-space(@class), ' '), ' job-location ')][1])",
        "string(//*[contains(concat(' ', normalize-space(@class), ' '), ' location ')][1])",
    )
    if urlsplit(url).hostname == "vagas.codam.com.br":
        localidade_seletores = (
            "//use[contains(@href, 'location-pin')]/ancestor::div[contains(@class, 'module-icon-item')][1]/span/text()",
        ) + localidade_seletores
    localidade = _primeiro_texto(
        seletor,
        localidade_seletores,
    ) or _valor_rotulado(
        seletor,
        ("local", "localidade", "cidade", "location", "local de trabalho"),
    )
    modalidade = _primeiro_texto(
        seletor,
        (
            "//*[@itemprop='jobLocationType']/@content",
            "//*[@itemprop='jobLocationType']//text()",
        ),
    ) or _valor_rotulado(
        seletor,
        (
            "modalidade",
            "modelo de trabalho",
            "formato de trabalho",
            "workplace type",
        ),
    )
    regime = _primeiro_texto(
        seletor,
        (
            "//*[@itemprop='employmentType']/@content",
            "//*[@itemprop='employmentType']//text()",
        ),
    ) or _valor_rotulado(
        seletor,
        (
            "tipo de contrato",
            "regime de contratação",
            "tipo de emprego",
            "tipo de vaga",
            "employment type",
        ),
    )
    senioridade = _primeiro_texto(
        seletor,
        (
            "//*[@itemprop='experienceRequirements']/@content",
            "//*[@itemprop='experienceRequirements']//text()",
        ),
    ) or _valor_rotulado(
        seletor,
        ("senioridade", "nível", "experience level"),
    )
    salario = _extrair_salario_microdados(seletor) or _extrair_salario_rotulado(seletor)
    prazo = _primeira_data_iso(
        seletor,
        (
            "//*[@itemprop='validThrough']/@content",
            "//*[@itemprop='validThrough']//text()",
        ),
    ) or _extrair_prazo_rotulado(seletor)
    beneficios = _primeiro_texto(
        seletor,
        (
            "//*[@itemprop='jobBenefits']/@content",
            "string(//*[@itemprop='jobBenefits'][1])",
        ),
    ) or _valor_rotulado(seletor, ("benefícios", "beneficios", "benefits"))
    publicado_em = _primeira_data_iso(
        seletor,
        (
            "//*[@itemprop='datePosted']/@content",
            "//*[@itemprop='datePosted']//text()",
        ),
    ) or _extrair_data_publicacao_rotulada(seletor)

    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "title": titulo,
        "description": descricao,
        "url": url.strip(),
        "_observatorio_extrator": "html_generico",
    }
    if urlsplit(url).hostname == "vagas.codam.com.br":
        # O formulário de currículo é genérico; a página da vaga preserva
        # o contexto da posição e oferece o botão de candidatura.
        documento["_observatorio_apply_url"] = url.strip()

    if empresa is not None:
        documento["hiringOrganization"] = {
            "@type": "Organization",
            "name": empresa,
        }

    if localidade is not None:
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": localidade,
            },
        }

    if modalidade is not None:
        documento["jobLocationType"] = modalidade

    if regime is not None:
        documento["employmentType"] = regime

    if senioridade is not None:
        documento["experienceRequirements"] = senioridade

    if salario is not None:
        documento["baseSalary"] = salario

    if prazo is not None:
        documento["validThrough"] = prazo

    if beneficios is not None:
        documento["jobBenefits"] = beneficios

    if publicado_em is not None:
        documento["datePosted"] = publicado_em

    return ResultadoHTMLGenerico(vagas=(documento,))
