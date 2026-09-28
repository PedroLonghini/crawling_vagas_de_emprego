"""Conversão de anúncios coletados em vagas canônicas."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from uuid import NAMESPACE_URL, uuid5

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.domain.localizacao import endereco_indica_brasil, pais_eh_brasil
from observatorio_vagas.domain.vaga import (
    SalarioNormalizado,
    VagaCanonica,
)


class ErroNormalizacaoVaga(ValueError):
    """Indica que o anúncio não possui os dados mínimos."""


class _ExtratorTextoHtml(HTMLParser):
    """Extrai somente o texto visível de uma descrição HTML."""

    def __init__(self) -> None:
        """Prepara a lista que receberá as partes do texto."""

        super().__init__(convert_charrefs=True)

        self.partes: list[str] = []

    def handle_data(
        self,
        data: str,
    ) -> None:
        """Guarda cada trecho textual encontrado."""

        texto = data.strip()

        if texto:
            self.partes.append(texto)


def _texto(
    valor: object,
) -> str | None:
    """Converte valores simples em texto não vazio."""

    # None representa ausência.
    #
    # Booleanos não podem virar os textos "True" ou "False".
    if valor is None or isinstance(valor, bool):
        return None

    # Dicionários e listas precisam de tratamento específico.
    if not isinstance(
        valor,
        (
            str,
            int,
            float,
            Decimal,
        ),
    ):
        return None

    # Troca várias quebras e espaços por um único espaço.
    texto = re.sub(
        r"\s+",
        " ",
        str(valor),
    ).strip()

    return texto or None


def _limpar_html(
    valor: object,
) -> str | None:
    """Remove HTML preservando o conteúdo da descrição."""

    texto = _texto(valor)

    if texto is None:
        return None

    analisador = _ExtratorTextoHtml()

    analisador.feed(texto)
    analisador.close()

    return _texto(" ".join(analisador.partes))


def _primeiro_objeto(
    valor: object,
) -> Mapping[str, object] | None:
    """Obtém o primeiro objeto de um campo ou lista."""

    if isinstance(valor, Mapping):
        return valor

    if isinstance(valor, list):
        return next(
            (item for item in valor if isinstance(item, Mapping)),
            None,
        )

    return None


def _juntar_textos(
    valor: object,
) -> str | None:
    """Transforma texto ou lista em texto legível."""

    if isinstance(valor, list):
        partes = [texto for item in valor if (texto := _texto(item)) is not None]

        return ", ".join(partes) or None

    return _texto(valor)


def _extrair_endereco(
    anuncio: AnuncioVaga,
) -> tuple[
    str | None,
    str | None,
    str,
    str | None,
    str | None,
]:
    """Extrai cidade, estado, país, endereço e CEP."""

    local = _primeiro_objeto(anuncio.campos_estruturados.get("jobLocation"))

    endereco = _primeiro_objeto(local.get("address")) if local is not None else None

    # Quando não existe endereço estruturado, preservamos
    # os campos originais encontrados pelo extrator. Não assumimos Brasil:
    # uma localização sem país só pode ser publicada se houver evidência
    # brasileira no texto original.
    if endereco is None:
        endereco_original = anuncio.endereco_original or anuncio.localidade_original
        cidade, estado = _inferir_cidade_estado(endereco_original)
        return (
            cidade,
            estado,
            ("BR" if endereco_indica_brasil(endereco_original) else ""),
            endereco_original,
            anuncio.cep_original,
        )

    cidade = _texto(endereco.get("addressLocality"))

    estado = _texto(endereco.get("addressRegion"))

    rua = _texto(endereco.get("streetAddress"))

    cep = _texto(endereco.get("postalCode")) or anuncio.cep_original

    pais_bruto = endereco.get("addressCountry")

    # Schema.org permite que o país seja um objeto.
    if isinstance(
        pais_bruto,
        Mapping,
    ):
        pais = _texto(pais_bruto.get("name")) or _texto(pais_bruto.get("value"))
    else:
        pais = _texto(pais_bruto)

    partes_endereco = [
        parte
        for parte in (
            rua,
            cidade,
            estado,
        )
        if parte is not None
    ]

    endereco_normalizado = (
        ", ".join(partes_endereco) or anuncio.endereco_original or anuncio.localidade_original
    )

    pais_normalizado = (pais or "").upper()

    if not pais_normalizado and endereco_indica_brasil(
        endereco_normalizado,
        anuncio.endereco_original,
        anuncio.localidade_original,
    ):
        pais_normalizado = "BR"

    # Mantemos a grafia da fonte para países estrangeiros, mas padronizamos o
    # Brasil para BR quando o Schema.org usa Brasil ou Brazil.
    if pais_eh_brasil(pais_normalizado):
        pais_normalizado = "BR"

    return (
        cidade,
        estado,
        pais_normalizado,
        endereco_normalizado,
        cep,
    )


def _inferir_cidade_estado(localidade: str | None) -> tuple[str | None, str | None]:
    """Lê o formato público comum ``Cidade - UF`` sem inventar endereço."""

    texto = _texto(localidade)
    if texto is None:
        return None, None
    correspondencia = re.search(r"^\s*(.+?)\s*[-/,]\s*([A-Za-z]{2})\s*$", texto)
    if correspondencia is None:
        return None, None
    cidade, estado = correspondencia.groups()
    return cidade.strip() or None, estado.upper()


def _converter_decimal(
    valor: object,
) -> Decimal | None:
    """Converte um número simples sem aceitar valores inválidos."""

    if valor is None or isinstance(valor, bool):
        return None

    try:
        numero = Decimal(str(valor).strip())

    except (
        InvalidOperation,
        ValueError,
    ):
        return None

    # NaN e Infinity não representam valores salariais.
    if not numero.is_finite():
        return None

    # Salários negativos também são rejeitados.
    return numero if numero >= 0 else None


def _normalizar_numero_monetario(
    valor: str,
) -> Decimal | None:
    """Converte um trecho monetário para Decimal.

    Exemplos:

    - 30,288 vira 30288;
    - 6.500,50 vira 6500.50;
    - 6,500.50 vira 6500.50;
    - 6500 vira 6500.

    Nenhum valor é estimado. Apenas interpretamos os
    separadores utilizados pela fonte.
    """

    texto = valor.strip()

    if not texto:
        return None

    possui_virgula = "," in texto
    possui_ponto = "." in texto

    # Quando existem ponto e vírgula, consideramos o último
    # deles como separador decimal.
    if possui_virgula and possui_ponto:
        separador_decimal = "," if texto.rfind(",") > texto.rfind(".") else "."

        separador_milhar = "." if separador_decimal == "," else ","

        normalizado = texto.replace(
            separador_milhar,
            "",
        ).replace(
            separador_decimal,
            ".",
        )

    # Quando existe somente um separador, três dígitos depois
    # dele normalmente representam milhares.
    elif possui_virgula or possui_ponto:
        separador = "," if possui_virgula else "."

        partes = texto.split(separador)

        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            normalizado = "".join(partes)
        else:
            normalizado = ".".join(partes)

    else:
        normalizado = texto

    return _converter_decimal(normalizado)


def _extrair_numeros_monetarios(
    valor: object,
) -> tuple[Decimal, ...]:
    """Extrai os números existentes em um texto salarial.

    Exemplo:

    £30,288 - £32,070

    produz:

    Decimal("30288"), Decimal("32070")
    """

    if valor is None or isinstance(valor, bool):
        return ()

    texto = str(valor).strip()

    # Símbolos como R$, £ e USD não fazem parte dos números.
    candidatos = re.findall(
        r"\d+(?:[.,]\d+)*",
        texto,
    )

    numeros: list[Decimal] = []

    for candidato in candidatos:
        numero = _normalizar_numero_monetario(candidato)

        if numero is not None:
            numeros.append(numero)

    return tuple(numeros)


def _periodo_salario(
    valor: object,
) -> PeriodoSalario:
    """Converte unidades usadas pelo Schema.org."""

    texto = (_texto(valor) or "").casefold()

    if any(
        sinal in texto
        for sinal in (
            "hour",
            "hora",
        )
    ):
        return PeriodoSalario.HORA

    if any(
        sinal in texto
        for sinal in (
            "day",
            "dia",
        )
    ):
        return PeriodoSalario.DIA

    if any(
        sinal in texto
        for sinal in (
            "week",
            "semana",
        )
    ):
        return PeriodoSalario.SEMANA

    if any(
        sinal in texto
        for sinal in (
            "month",
            "mes",
            "mês",
        )
    ):
        return PeriodoSalario.MES

    if any(
        sinal in texto
        for sinal in (
            "year",
            "annual",
            "ano",
        )
    ):
        return PeriodoSalario.ANO

    return PeriodoSalario.NAO_INFORMADO


def _extrair_salario(
    anuncio: AnuncioVaga,
) -> SalarioNormalizado | None:
    """Normaliza somente salários publicados pela fonte."""

    salario = _primeiro_objeto(anuncio.campos_estruturados.get("baseSalary"))

    if salario is None:
        return None

    moeda = (_texto(salario.get("currency")) or "").upper()

    # A moeda precisa usar três letras:
    #
    # BRL, GBP, USD etc.
    if len(moeda) != 3 or not moeda.isalpha():
        return None

    valor = salario.get("value")

    if isinstance(
        valor,
        Mapping,
    ):
        # Formato com valores separados.
        minimo = _converter_decimal(valor.get("minValue"))

        maximo = _converter_decimal(valor.get("maxValue"))

        # Formato em que a faixa inteira aparece como texto.
        numeros_publicados = _extrair_numeros_monetarios(valor.get("value"))

        if minimo is None and numeros_publicados:
            minimo = numeros_publicados[0]

        if maximo is None and numeros_publicados:
            maximo = numeros_publicados[-1]

        periodo = _periodo_salario(valor.get("unitText"))

    else:
        numeros_publicados = _extrair_numeros_monetarios(valor)

        if numeros_publicados:
            minimo = numeros_publicados[0]
            maximo = numeros_publicados[-1]
        else:
            minimo = _converter_decimal(valor)
            maximo = minimo

        periodo = PeriodoSalario.NAO_INFORMADO

    # Sem números publicados não existe salário para armazenar.
    if minimo is None and maximo is None:
        return None

    # Uma faixa invertida provavelmente representa um problema
    # na própria fonte.
    if minimo is not None and maximo is not None and minimo > maximo:
        return None

    mensal_minimo: Decimal | None = None
    mensal_maximo: Decimal | None = None
    regra: str | None = None

    # Preservamos os valores anuais e armazenamos a referência
    # mensal em campos separados.
    if periodo is PeriodoSalario.ANO:
        mensal_minimo = minimo / Decimal("12") if minimo is not None else None

        mensal_maximo = maximo / Decimal("12") if maximo is not None else None

        regra = "valor anual dividido por 12"

    return SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        moeda=moeda,
        periodo=periodo,
        minimo=minimo,
        maximo=maximo,
        mensal_minimo=mensal_minimo,
        mensal_maximo=mensal_maximo,
        regra_normalizacao=regra,
    )


def _extrair_modalidade_local(
    anuncio: AnuncioVaga,
) -> str | None:
    """Extrai a modalidade armazenada dentro de jobLocation."""

    local = _primeiro_objeto(anuncio.campos_estruturados.get("jobLocation"))

    if local is None:
        return None

    propriedades = local.get("additionalProperty")

    if isinstance(propriedades, Mapping):
        propriedades = [propriedades]

    if not isinstance(propriedades, list):
        return None

    valores: list[str] = []

    for propriedade in propriedades:
        if not isinstance(propriedade, Mapping):
            continue

        valor = _texto(propriedade.get("value"))

        if valor is not None:
            valores.append(valor)

    return " ".join(valores) or None


def _normalizar_modalidade(
    anuncio: AnuncioVaga,
) -> ModalidadeTrabalho:
    """Classifica somente quando existe sinal explícito."""

    texto = " ".join(
        parte
        for parte in (
            anuncio.modalidade_original,
            _juntar_textos(anuncio.campos_estruturados.get("jobLocationType")),
            _extrair_modalidade_local(anuncio),
        )
        if parte
    ).casefold()

    if any(
        sinal in texto
        for sinal in (
            "hybrid",
            "hibrid",
            "híbrido",
        )
    ):
        return ModalidadeTrabalho.HIBRIDO

    if any(
        sinal in texto
        for sinal in (
            "telecommute",
            "remote",
            "remoto",
            "home office",
        )
    ):
        return ModalidadeTrabalho.REMOTO

    if any(
        sinal in texto
        for sinal in (
            "onsite",
            "on-site",
            "presencial",
        )
    ):
        return ModalidadeTrabalho.PRESENCIAL

    return ModalidadeTrabalho.NAO_INFORMADO


def _normalizar_regime(
    anuncio: AnuncioVaga,
) -> RegimeContratacao:
    """Classifica o vínculo sem confundir emprego estrangeiro com CLT."""

    valor = anuncio.regime_original or _juntar_textos(
        anuncio.campos_estruturados.get("employmentType")
    )

    texto = (valor or "").casefold()

    if "clt" in texto:
        return RegimeContratacao.CLT

    if (
        "contractor" in texto
        or "pessoa jurídica" in texto
        or re.search(
            r"\bpj\b",
            texto,
        )
    ):
        return RegimeContratacao.PJ

    if any(
        sinal in texto
        for sinal in (
            "intern",
            "estágio",
            "estagio",
        )
    ):
        return RegimeContratacao.ESTAGIO

    if any(
        sinal in texto
        for sinal in (
            "temporary",
            "temporário",
            "temporario",
        )
    ):
        return RegimeContratacao.TEMPORARIO

    if any(
        sinal in texto
        for sinal in (
            "apprentice",
            "aprendiz",
        )
    ):
        return RegimeContratacao.APRENDIZ

    if any(
        sinal in texto
        for sinal in (
            "freelance",
            "freelancer",
        )
    ):
        return RegimeContratacao.FREELANCER

    # FULL_TIME estrangeiro não significa CLT.
    if texto:
        return RegimeContratacao.OUTRO

    return RegimeContratacao.NAO_INFORMADO


def _normalizar_senioridade(
    anuncio: AnuncioVaga,
) -> Senioridade:
    """Classifica a senioridade pelo campo original ou título."""

    texto = " ".join(
        parte
        for parte in (
            anuncio.senioridade_original,
            anuncio.titulo_original,
        )
        if parte
    ).casefold()

    if any(
        sinal in texto
        for sinal in (
            "estágio",
            "estagio",
            "intern",
        )
    ):
        return Senioridade.ESTAGIO

    if any(
        sinal in texto
        for sinal in (
            "aprendiz",
            "apprentice",
        )
    ):
        return Senioridade.APRENDIZ

    if re.search(
        r"\b(junior|júnior|jr)\b",
        texto,
    ):
        return Senioridade.JUNIOR

    if re.search(
        r"\b(pleno|pl|mid[- ]?level)\b",
        texto,
    ):
        return Senioridade.PLENO

    if re.search(
        r"\b(senior|sênior|sr)\b",
        texto,
    ):
        return Senioridade.SENIOR

    if any(
        sinal in texto
        for sinal in (
            "especialista",
            "specialist",
        )
    ):
        return Senioridade.ESPECIALISTA

    if any(
        sinal in texto
        for sinal in (
            "manager",
            "gerente",
            "diretor",
            "director",
            "head",
        )
    ):
        return Senioridade.LIDERANCA

    return Senioridade.NAO_INFORMADA


def _quantidade_vagas(
    anuncio: AnuncioVaga,
) -> int | None:
    """Obtém a quantidade sem inferir números do título."""

    valor = anuncio.numero_vagas_original or anuncio.campos_estruturados.get("totalJobOpenings")

    if isinstance(valor, bool):
        return None

    try:
        quantidade = int(str(valor).strip())

    except (
        TypeError,
        ValueError,
    ):
        return None

    return quantidade if quantidade >= 1 else None


def _lista_textual(
    *valores: object,
) -> tuple[str, ...]:
    """Guarda textos úteis sem criar informações novas."""

    itens: list[str] = []

    for valor in valores:
        texto = _limpar_html(valor)

        if texto is None:
            continue

        itens.append(texto)

    return tuple(itens)


def _converter_coordenada(
    valor: object,
    *,
    minimo: float,
    maximo: float,
) -> float | None:
    """Converte e valida uma coordenada geográfica."""

    if valor is None or isinstance(valor, bool):
        return None

    try:
        numero = float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None

    # Esta validação também rejeita infinito e NaN.
    if not minimo <= numero <= maximo:
        return None

    return numero


def _extrair_coordenadas(
    anuncio: AnuncioVaga,
) -> tuple[
    float | None,
    float | None,
]:
    """Extrai latitude e longitude de jobLocation.geo."""

    local = _primeiro_objeto(
        anuncio.campos_estruturados.get(
            "jobLocation",
        )
    )

    if local is None:
        return None, None

    geo = _primeiro_objeto(
        local.get("geo"),
    )

    if geo is None:
        return None, None

    latitude = _converter_coordenada(
        geo.get("latitude"),
        minimo=-90,
        maximo=90,
    )

    longitude = _converter_coordenada(
        geo.get("longitude"),
        minimo=-180,
        maximo=180,
    )

    # As duas coordenadas precisam estar presentes.
    if latitude is None or longitude is None:
        return None, None

    return latitude, longitude


def converter_anuncio_em_vaga_canonica(
    anuncio: AnuncioVaga,
) -> VagaCanonica:
    """Transforma um anúncio associado em vaga idempotente."""

    if anuncio.empresa_id is None:
        raise ErroNormalizacaoVaga(
            "o anúncio precisa possuir empresa_id antes de criar a vaga canônica"
        )

    titulo = _texto(
        anuncio.titulo_original,
    )

    if titulo is None:
        raise ErroNormalizacaoVaga("o anúncio não possui título válido")

    vaga_id = uuid5(
        NAMESPACE_URL,
        (f"https://observatorio-vagas.local/vagas/anuncios/{anuncio.id}"),
    )

    (
        cidade,
        estado,
        pais,
        endereco,
        cep,
    ) = _extrair_endereco(
        anuncio,
    )

    (
        latitude,
        longitude,
    ) = _extrair_coordenadas(
        anuncio,
    )

    documento = anuncio.campos_estruturados

    descricao = _limpar_html(
        anuncio.descricao_original,
    )

    responsabilidades = _lista_textual(
        anuncio.responsabilidades_original,
        documento.get("responsibilities"),
    )

    requisitos = _lista_textual(
        anuncio.requisitos_original,
        documento.get("qualifications"),
        documento.get("skills"),
        documento.get("experienceRequirements"),
        documento.get("educationRequirements"),
    )

    beneficios = _lista_textual(
        anuncio.beneficios_original,
        documento.get("jobBenefits"),
    )

    return VagaCanonica(
        id=vaga_id,
        empresa_id=anuncio.empresa_id,
        titulo_normalizado=titulo,
        descricao_normalizada=descricao,
        senioridade=(
            _normalizar_senioridade(
                anuncio,
            )
        ),
        cidade=cidade,
        estado=estado,
        pais=pais,
        endereco=endereco,
        cep=cep,
        latitude=latitude,
        longitude=longitude,
        modalidade=(
            _normalizar_modalidade(
                anuncio,
            )
        ),
        regime=(
            _normalizar_regime(
                anuncio,
            )
        ),
        salario=(
            _extrair_salario(
                anuncio,
            )
        ),
        quantidade_vagas=(
            _quantidade_vagas(
                anuncio,
            )
        ),
        requisitos=requisitos,
        responsabilidades=responsabilidades,
        beneficios=beneficios,
        criado_em=(anuncio.primeira_observacao_em),
        atualizado_em=(anuncio.ultima_observacao_em),
    )
