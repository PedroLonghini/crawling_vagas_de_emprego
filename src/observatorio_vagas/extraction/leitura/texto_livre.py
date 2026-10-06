"""Interpretação de informações escritas no texto da vaga.

Usado quando a informação não vem em campo estruturado nem em rótulo. Cada
função devolve o valor e a origem (ou ``None``), e algumas devolvem também um
motivo para o diagnóstico quando decidem deixar o campo vazio.

A regra de modalidade segue a referência ``modalidade_ref.py``, já conferida
no lote de 2.448 vagas.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from observatorio_vagas.domain.localizacao import NOMES_UFS_BRASIL


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


_UFS = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"
_PALAVRA_CIDADE = r"[A-ZÀ-Ý][\wÀ-ÿ'.]*"
_NOME_CIDADE = rf"{_PALAVRA_CIDADE}(?:\s+(?:(?:d[aeo]s?|e)\s+)?{_PALAVRA_CIDADE}){{0,3}}"
_CIDADE_UF = re.compile(
    rf"(?<![\wÀ-ÿ])(?P<cidade>{_NOME_CIDADE})\s*(?:[-–/,(]|\s)\s*(?P<uf>{_UFS})\)?(?![\wÀ-ÿ])"
)
_CIDADE_UF_COM_SEPARADOR = re.compile(
    rf"(?<![\wÀ-ÿ])(?P<cidade>{_NOME_CIDADE})\s*[-–/,(]\s*(?P<uf>{_UFS})\)?(?![\wÀ-ÿ])"
)
_ROTULO_LOCAL = re.compile(
    r"^\s*(?:local(?:iza[cç][aã]o)?(?:\s+(?:de\s+trabalho|da\s+vaga|de\s+atua[cç][aã]o))?|"
    r"cidade(?:\s*/\s*(?:uf|estado))?|lota[cç][aã]o|munic[ií]pio)\s*[:\-–]\s*(?P<valor>[^\n]{2,80})",
    re.IGNORECASE | re.MULTILINE,
)
# Rótulo no meio da linha ("Estágio Presencial Cidade: Presidente Prudente"):
# só com dois-pontos, para não confundir com um hífen qualquer.
_ROTULO_LOCAL_NO_MEIO = re.compile(
    r"(?<=\s)(?:cidade|local(?:iza[cç][aã]o)?(?:\s+(?:de\s+trabalho|da\s+vaga))?)\s*:\s*"
    r"(?P<valor>[^\n]{2,80})",
    re.IGNORECASE,
)
# Próximo rótulo na mesma linha ("Presidente Prudente Bolsa: R$ 1.100") encerra o valor.
_PROXIMO_ROTULO = re.compile(r"\s+[A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[a-zà-ÿ]+){0,2}\s*:")
_UF_COLADA = re.compile(rf"\b({_UFS})(?=[A-ZÀ-Ý][a-zà-ÿ])")
# Macrorregiões não são UF ("Recife, Nordeste" no JSON-LD de algumas plataformas).
REGIOES_DO_BRASIL = frozenset(
    {"norte", "nordeste", "sul", "sudeste", "centro-oeste", "centro oeste", "centro"}
)
# Capitais e cidades grandes, para aceitar "Vendedor em Recife" no título sem UF.
CIDADES_CONHECIDAS = frozenset(
    {
        "rio branco", "maceio", "macapa", "manaus", "salvador", "fortaleza", "brasilia",
        "vitoria", "goiania", "sao luis", "cuiaba", "campo grande", "belo horizonte",
        "belem", "joao pessoa", "curitiba", "recife", "teresina", "rio de janeiro", "natal",
        "porto alegre", "porto velho", "boa vista", "florianopolis", "sao paulo", "aracaju",
        "palmas", "guarulhos", "campinas", "sao goncalo", "duque de caxias",
        "sao bernardo do campo", "nova iguacu", "santo andre", "osasco",
        "jaboatao dos guararapes", "sao jose dos campos", "ribeirao preto", "uberlandia",
        "sorocaba", "contagem", "juiz de fora", "feira de santana", "joinville", "londrina",
        "aparecida de goiania", "niteroi", "ananindeua", "caxias do sul",
        "campos dos goytacazes", "vila velha", "mogi das cruzes", "santos", "betim",
        "diadema", "jundiai", "maringa", "montes claros", "piracicaba", "carapicuiba",
        "olinda", "bauru", "anapolis", "sao jose do rio preto", "blumenau", "petropolis",
        "uberaba", "caruaru", "vitoria da conquista", "cascavel", "ponta grossa", "franca",
        "camacari", "barueri", "cotia", "palhoca", "itajai", "chapeco", "novo hamburgo",
        "canoas", "pelotas", "santa maria", "gravatai", "sao leopoldo",
    }
)  # fmt: skip
ARQUIVO_MUNICIPIOS = Path(__file__).resolve().parents[4] / "config" / "municipios_ibge.csv"
# Nome repetido em mais de uma UF: vale a cidade grande (Palmas-TO, não Palmas-PR).
UF_DA_CIDADE_GRANDE = {
    "palmas": "TO", "santa maria": "RS", "cascavel": "PR", "belem": "PA", "boa vista": "RR",
    "campo grande": "MS", "rio branco": "AC", "santo andre": "SP",
}  # fmt: skip


@lru_cache(maxsize=1)
def _ufs_por_municipio() -> dict[str, frozenset[str]]:
    """Município (normalizado) -> UFs onde existe, pela lista oficial do IBGE."""

    ufs: dict[str, set[str]] = {}
    try:
        with ARQUIVO_MUNICIPIOS.open(encoding="utf-8", newline="") as arquivo:
            for linha in csv.DictReader(arquivo):
                ufs.setdefault(normalizar(linha["nome"]), set()).add(linha["uf"])
    except OSError:
        return {}
    return {nome: frozenset(siglas) for nome, siglas in ufs.items()}


def uf_da_cidade(cidade: str) -> str | None:
    """UF de um município pelo nome, só quando não há dúvida (nome único ou cidade grande)."""

    nome = normalizar(cidade).strip()
    if nome in UF_DA_CIDADE_GRANDE:
        return UF_DA_CIDADE_GRANDE[nome]
    siglas = _ufs_por_municipio().get(nome, frozenset())
    return next(iter(siglas)) if len(siglas) == 1 else None


# Municípios de nome único que também são palavras comuns em título de vaga
# ("Técnico em Saúde", "Agente em União"): depois de "em" não viram cidade.
MUNICIPIOS_QUE_SAO_PALAVRAS_COMUNS = frozenset(
    {"saude", "uniao", "progresso", "liberdade", "esperanca", "harmonia", "paraiso",
     "alianca", "futuro", "independencia", "concordia", "sucesso", "ouro", "planalto",
     "central", "porto", "areia", "campo", "colina", "mirante", "pedra", "cristal"}
)  # fmt: skip
_CIDADE_NO_TITULO = re.compile(
    rf"\b(?:em|para)\s+(?P<cidade>{_NOME_CIDADE})(?:\s*[-–/,]\s*(?P<uf>{_UFS}))?"
    rf"\s*(?:[|\-–(]|$)"
)


def cidade_do_titulo(titulo: str, origem: str) -> Leitura | None:
    """ "Vendedor em Recife" ou "Analista em Campinas - SP": cidade no título.

    Sem UF, aceita capitais e cidades grandes e, depois de "em", qualquer município de
    nome único no IBGE ("Estoquista em Tubarão" -> Tubarão, SC); "Analista em
    Tecnologia" continua de fora. Com UF, "para" só vale com cidade conhecida ("Vaga
    para Cuidador de Idoso, SP" é cargo).
    """

    for achado in _CIDADE_NO_TITULO.finditer(titulo or ""):
        cidade = _limpar_cidade(achado["cidade"])
        nome = normalizar(cidade)
        conhecida = nome in CIDADES_CONHECIDAS or (
            achado.group().lstrip().startswith("em")
            and nome not in MUNICIPIOS_QUE_SAO_PALAVRAS_COMUNS
            # "em Mato Grosso" é o estado, não o município Mato Grosso-PB.
            and nome not in NOMES_UFS_BRASIL
            and uf_da_cidade(cidade) is not None
        )
        if achado["uf"] and (conhecida or achado.group().lstrip().startswith("em")):
            return Leitura(f"{cidade}, {achado['uf']}", origem, achado.group().strip())
        if conhecida:
            uf = uf_da_cidade(cidade)
            valor = f"{cidade}, {uf}" if uf else cidade
            return Leitura(valor, origem, achado.group().strip())
    return None


_TITULOS_DE_LOCAL = {
    "local",
    "localizacao",
    "local de trabalho",
    "local da vaga",
    "local de atuacao",
    "cidade",
    "lotacao",
    "municipio",
}
_SO_MODALIDADE = re.compile(
    r"^(remot[oa]|presencial|h[ií]brid[oa]|home ?office|on-?site|remote|hybrid)\W*$", re.IGNORECASE
)
_JANELA_LOCAL_SEM_ROTULO = 1500


def _limpar_cidade(cidade: str) -> str:
    """Título em MAIÚSCULAS colado na cidade ("AUXILIAR DE TELECOM Panambi") fica de fora."""

    palavras = cidade.split()
    if all(p.isupper() for p in palavras):
        return cidade
    ultima = max((i for i, p in enumerate(palavras) if p.isupper() and len(p) > 3), default=-1)
    return " ".join(palavras[ultima + 1 :]) or cidade


def _formatar_local(valor: str) -> str | None:
    """ "Camaçari - BA" -> "Camaçari, BA"; outro texto curto e capitalizado fica como está."""

    # UF colada no rótulo seguinte ("São Paulo, SPFormação:") é separada antes do corte.
    valor = _UF_COLADA.sub(r"\1 ", valor)
    valor = _PROXIMO_ROTULO.split(valor, maxsplit=1)[0]
    valor = " ".join(valor.replace("|", " ").split()).strip(" .,;:-–")
    if not valor or _SO_MODALIDADE.match(valor):
        return None
    achado = _CIDADE_UF.search(valor)
    if achado:
        return f"{_limpar_cidade(achado['cidade'])}, {achado['uf']}"
    if len(valor) <= 60 and valor[:1].isupper() and not re.search(r"[.!?]\s+\w", valor):
        return valor
    return None


def interpretar_localizacao(texto: str, origem: str) -> Leitura | None:
    """Local escrito na descrição: "Local: Camaçari - BA", título "Local" + linha, ou "Cidade - UF".

    Ordem: rótulo na mesma linha, título sozinho seguido do valor, e por último o
    primeiro "Cidade - UF" do início do texto (UF válida, para não pegar siglas soltas).
    """

    for achado in _ROTULO_LOCAL.finditer(texto):
        local = _formatar_local(achado["valor"])
        if local:
            return Leitura(local, origem, achado.group().strip())

    for achado in _ROTULO_LOCAL_NO_MEIO.finditer(texto):
        local = _formatar_local(achado["valor"])
        if local:
            return Leitura(local, origem, achado.group().strip())

    linhas = [linha.strip() for linha in texto.splitlines()]
    for posicao, linha in enumerate(linhas):
        if normalizar(linha).strip(" :.-") in _TITULOS_DE_LOCAL:
            proxima = next((x for x in linhas[posicao + 1 : posicao + 4] if x), None)
            local = _formatar_local(proxima) if proxima else None
            if local:
                return Leitura(local, origem, f"{linha} / {proxima}")

    # Sem rótulo, só aceitamos o separador explícito ("Cidade - UF", "Cidade/UF", "Cidade, UF").
    achado = _CIDADE_UF_COM_SEPARADOR.search(texto[:_JANELA_LOCAL_SEM_ROTULO])
    if achado:
        return Leitura(
            f"{_limpar_cidade(achado['cidade'])}, {achado['uf']}", origem, achado.group()
        )
    return None


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
        elif sem_ano and (data - referencia).days > 300:
            # "até 10/12" lido em janeiro é do dezembro anterior (já venceu).
            data = date(ano - 1, mes, dia)
    except ValueError:
        return None

    return Leitura(data, origem, trecho[:20].strip())
