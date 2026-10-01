"""Interpretação de informações escritas no texto da vaga.

Usado quando a informação não vem em campo estruturado nem em rótulo. Cada
função devolve o valor e a origem (ou ``None``), e algumas devolvem também um
motivo para o diagnóstico quando decidem deixar o campo vazio.

A regra de modalidade segue a referência ``modalidade_ref.py``, já conferida
no lote de 2.448 vagas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos, para comparar palavras."""

    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(caractere for caractere in texto if not unicodedata.combining(caractere))


@dataclass(frozen=True, slots=True)
class Leitura:
    """Valor interpretado, de onde veio e o trecho lido."""

    valor: object
    origem: str
    valor_bruto: str = ""


@dataclass(frozen=True, slots=True)
class Indefinido:
    """Nada decidido; ``motivo`` explica por quê (ex.: conflito)."""

    motivo: str
    valor_bruto: str = ""


# --------------------------------------------------------------------------
# 3.1 Modalidade
# --------------------------------------------------------------------------

_REMOTO = (
    r"(100\s?% ?remot[oa]|remot[oa]|home[\s-]?office|teletrabalho|trabalho a distancia"
    r"|remote|anywhere)"
)
_HIBRIDO = r"(hibrid[oa]s?|hybrid)"
# "presencialmente" descreve tarefa, não modalidade.
_PRESENCIAL = (
    r"(100\s?% ?presencial|presencial|presenciais|on[\s-]?site|full office|in[\s-]?office)"
)
_ROTULO = (
    r"(modalidade|modelo|regime|formato|local de trabalho|atuacao|workplace|work model"
    r"|location|trabalho)"
    r"\s*(de trabalho)?\s*[:\-–]?\s*(?:[a-z ]{0,20}[-–/]\s*)?"
)
# Palavras que, coladas, mudam o sentido ("suporte remoto", "auxílio home office").
_NAO = (
    r"(suporte|atendimento|acesso|visitas?|gestao|nuvem|cloud|conectividade|connectivity|arquitetura|"
    r"ambiente|vendas?|treinamento|entrevista|integracao|curso|ensino|profissional|auxilio|"
    r"ajuda de custo|kit|subsidio)"
)
_MODALIDADES = (("HYBRID", _HIBRIDO), ("REMOTE", _REMOTO), ("ON_SITE", _PRESENCIAL))


def _ocorre_sem_falso_positivo(padrao: str, texto: str) -> bool:
    for correspondencia in re.finditer(r"\b" + padrao + r"\b", texto):
        antes = texto[max(0, correspondencia.start() - 40) : correspondencia.start()]
        depois = texto[correspondencia.end() : correspondencia.end() + 20]
        if re.search(
            _NAO + r"\W{0,3}(de\s|ao\s|a\s)?\w{0,12}(\s(e|ou)\s\w{0,12})?\W{0,3}$", antes
        ) or re.match(r"\W{0,3}" + _NAO, depois):
            continue
        return True
    return False


def interpretar_modalidade(
    titulo: str,
    texto: str,
    rotulo_estruturado: str | None = None,
) -> Leitura | Indefinido | None:
    """Modalidade em valor da API: ``REMOTE``, ``HYBRID`` ou ``ON_SITE``."""

    if rotulo_estruturado:
        rotulo = normalizar(rotulo_estruturado)
        for valor, padrao in _MODALIDADES:
            if re.search(padrao, rotulo):
                return Leitura(valor, "rotulo_estruturado", rotulo_estruturado)

    titulo_n, texto_n = normalizar(titulo), normalizar(texto)

    for valor, padrao in _MODALIDADES:
        if re.search(r"[\(\[\-–|/]\s*" + padrao + r"\b", titulo_n) or re.search(
            r"\b" + padrao + r"\s*[\)\]]", titulo_n
        ):
            return Leitura(valor, "titulo", titulo)

    no_rotulo = {valor for valor, padrao in _MODALIDADES if re.search(_ROTULO + padrao, texto_n)}
    livres = {
        valor for valor, padrao in _MODALIDADES if _ocorre_sem_falso_positivo(padrao, texto_n)
    }
    achados = no_rotulo or livres
    origem = "rotulo_no_texto" if no_rotulo else "texto_livre"

    if not achados:
        return None
    if "HYBRID" in achados:
        return Leitura("HYBRID", origem)
    if "ON_SITE" in achados and re.search(
        r"presencia\w*\s*(\d|uma|duas|tres)\s*(x|vez|vezes|dias?)\s*(por|na|a)\s*semana", texto_n
    ):
        return Leitura("HYBRID", origem)
    if {"REMOTE", "ON_SITE"} <= achados:
        if re.search(
            r"\d\s*(x|dias?)\s*(por semana\s*)?(de\s*|em\s*)?(home|remot|presenc)", texto_n
        ) or re.search(
            r"(home office|remoto)\s*(\d|uma|duas|1|2|3)\s*(x|vez|vezes|dias?)", texto_n
        ):
            return Leitura("HYBRID", origem)
        return Indefinido("conflito: presencial e remoto sem contagem de dias")

    return Leitura(achados.pop(), origem)


# --------------------------------------------------------------------------
# 3.2 Vínculo
# --------------------------------------------------------------------------

_VINCULOS = (
    ("INTERNSHIP", r"\b(estagio|estagiari[oa]s?|internship)\b"),
    ("TEMPORARY", r"\b(temporari[oa]s?|temporary)\b"),
    (
        "CONTRACT",
        r"\b(pj|pessoa juridica|freelancer?|freela|autonomo"
        r"|prestador(a)? de servicos?|prestador(a)?)\b",
    ),
    ("FULL_TIME", r"\b(clt|efetiv[oa]|full[\s-]?time|jornada integral|tempo integral)\b"),
)
_VINCULOS_SEM_ENUM = r"\b(jovem aprendiz|menor aprendiz|aprendiz|meio periodo|part[\s-]?time)\b"


def interpretar_vinculo(texto: str, origem: str) -> Leitura | Indefinido | None:
    """Vínculo em valor da API; PJ nunca vira FULL_TIME."""

    texto_n = normalizar(texto)
    achados = [valor for valor, padrao in _VINCULOS if re.search(padrao, texto_n)]

    if achados:
        # A ordem da tabela dá prioridade ao vínculo mais específico.
        return Leitura(achados[0], origem, texto[:120])

    sem_enum = re.search(_VINCULOS_SEM_ENUM, texto_n)
    if sem_enum:
        return Indefinido(f"vínculo sem valor na API: {sem_enum.group(0)}", texto[:120])

    return None


# --------------------------------------------------------------------------
# 3.3 Nível
# --------------------------------------------------------------------------

_NIVEIS = (
    ("INTERNSHIP", r"\b(estagio|estagiari[oa])\b"),
    ("ENTRY_LEVEL", r"\b(trainee|junior|jr\.?)\b"),
    # "Pl" sozinho fica de fora: aparece em "PL/SQL".
    ("MID_SENIOR_LEVEL", r"\b(pleno|senior|sr\.?|mid[\s-]?level|pl\b(?!\s*/|\s*sql))"),
    ("DIRECTOR", r"\b(diretor[a]?|director)\b"),
)


def interpretar_nivel(texto: str, origem: str) -> Leitura | None:
    """Só palavra inteira e nível explícito; cargo ("Gerente") não é nível."""

    texto_n = normalizar(texto)
    achados = {valor for valor, padrao in _NIVEIS if re.search(padrao, texto_n)}

    # Na dúvida (dois níveis diferentes), deixa vazio.
    if len(achados) != 1:
        return None

    return Leitura(achados.pop(), origem, texto[:120])


# --------------------------------------------------------------------------
# 3.4 Salário, CNPJ, CEP, recrutador, expiração
# --------------------------------------------------------------------------

_VALOR = r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)"
_ROTULO_SALARIO = r"(salario|remuneracao|bolsa(?: auxilio)?|faixa salarial|salary)"
_SALARIO_VAGO = r"(a combinar|compativel com o mercado|a definir|bonificacao|comissao|comissoes)"
_BENEFICIO = r"\b(vr|va|vale|refeicao|alimentacao|transporte|auxilio|ajuda de custo|plano)\b"


def _decimal(texto: str) -> Decimal | None:
    try:
        return Decimal(texto.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


@dataclass(frozen=True, slots=True)
class Salario:
    minimo: Decimal
    maximo: Decimal | None
    periodicidade: str | None


def interpretar_salario(texto: str, origem: str) -> Leitura | Indefinido | None:
    """Valores em R$ rotulados como salário, remuneração ou bolsa."""

    texto_n = normalizar(texto)

    for rotulo in re.finditer(_ROTULO_SALARIO, texto_n):
        trecho_n = texto_n[rotulo.end() : rotulo.end() + 120]
        trecho = texto[rotulo.end() : rotulo.end() + 120]
        valores = re.findall(_VALOR, trecho)
        antes_do_valor = trecho_n.split("r$", 1)[0]

        if not valores:
            if re.search(_SALARIO_VAGO, trecho_n[:40]):
                return Indefinido("salário não informado (a combinar/bonificação)", trecho[:80])
            continue

        # "Vale-refeição: R$ 30" logo depois do rótulo não é salário.
        if re.search(_BENEFICIO, antes_do_valor):
            continue

        numeros = [n for n in (_decimal(v) for v in valores[:2]) if n is not None and n > 0]
        if not numeros:
            continue

        periodicidade = None
        if re.search(r"\b(por hora|/h|hora)\b", trecho_n):
            periodicidade = "HOUR"
        elif re.search(r"\b(mensal|por mes|/mes|mes)\b", trecho_n):
            periodicidade = "MONTH"
        elif re.search(r"\b(anual|por ano|/ano)\b", trecho_n):
            periodicidade = "YEAR"

        minimo = min(numeros)
        maximo = max(numeros) if len(numeros) > 1 else None
        return Leitura(Salario(minimo, maximo, periodicidade), origem, trecho[:80].strip())

    return None


_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
_CEP = re.compile(r"\b\d{5}-?\d{3}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def interpretar_cnpj(texto: str, origem: str) -> Leitura | None:
    encontrado = _CNPJ.search(texto)
    return (
        Leitura(re.sub(r"\D", "", encontrado.group()), origem, encontrado.group())
        if encontrado
        else None
    )


def interpretar_cep(texto: str, origem: str) -> Leitura | None:
    """CEP só perto de palavras de endereço, para não pegar outros números."""

    texto_n = normalizar(texto)
    for encontrado in _CEP.finditer(texto):
        vizinhanca = texto_n[max(0, encontrado.start() - 60) : encontrado.start()]
        if re.search(
            r"\b(cep|rua|av\.?|avenida|endereco|bairro|rodovia|estrada|alameda)\b", vizinhanca
        ):
            numero = re.sub(r"\D", "", encontrado.group())
            return Leitura(f"{numero[:5]}-{numero[5:]}", origem, encontrado.group())
    return None


def interpretar_email_candidatura(texto: str, origem: str) -> Leitura | None:
    """E-mail logo depois de "envie seu currículo para" e variações."""

    texto_n = normalizar(texto)
    gatilho = re.search(
        r"(envie|encaminhe|mande|enviar)\s+(o\s+|seu\s+|teu\s+)?(curriculo|cv)[^@]{0,60}", texto_n
    )
    if not gatilho:
        return None
    encontrado = _EMAIL.search(texto, gatilho.start(), gatilho.end() + 80)
    return Leitura(encontrado.group(), origem, encontrado.group()) if encontrado else None


def interpretar_recrutador(texto: str, origem: str) -> Leitura | None:
    """Nome depois de "Recrutador(a)", "Responsável", "Falar com"."""

    encontrado = re.search(
        r"(?:recrutador(?:a)?|respons[aá]vel(?: pela vaga)?|falar com)\s*[:\-–]\s*"
        r"([A-ZÀ-Ý][\wÀ-ÿ'.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'.-]+){0,4})",
        texto,
        re.IGNORECASE,
    )
    return Leitura(encontrado.group(1).strip(), origem, encontrado.group(0)) if encontrado else None


_MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
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


def interpretar_expiracao(texto: str, origem: str, *, referencia: date) -> Leitura | None:
    """Data depois de "inscrições até", "vaga válida até", "expiração"."""

    texto_n = normalizar(texto)
    gatilho = re.search(
        r"(inscricoes?\s+(abertas\s+)?ate|vaga\s+valida\s+ate|valida\s+ate|expiracao|prazo\s+(de\s+inscricao\s+)?ate|candidaturas?\s+ate)\s*[:\-–]?\s*",
        texto_n,
    )
    if not gatilho:
        return None

    trecho = texto_n[gatilho.end() : gatilho.end() + 40]
    numerica = re.match(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", trecho)
    por_extenso = re.match(r"(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?", trecho)

    try:
        if numerica:
            dia, mes = int(numerica.group(1)), int(numerica.group(2))
            ano = int(numerica.group(3)) if numerica.group(3) else referencia.year
        elif por_extenso and por_extenso.group(2) in _MESES:
            dia, mes = int(por_extenso.group(1)), _MESES[por_extenso.group(2)]
            ano = int(por_extenso.group(3)) if por_extenso.group(3) else referencia.year
        else:
            return None
        if ano < 100:
            ano += 2000
        data = date(ano, mes, dia)
        # "Inscrições até 10/01" lido em dezembro é do ano seguinte.
        sem_ano = not (numerica and numerica.group(3)) and not (
            por_extenso and por_extenso.group(3)
        )
        if sem_ano and (referencia - data).days > 60:
            data = date(ano + 1, mes, dia)
    except ValueError:
        return None

    return Leitura(data, origem, trecho[:20].strip())
