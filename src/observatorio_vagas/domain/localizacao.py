"""Regras conservadoras para reconhecer localizações brasileiras."""

import re
import unicodedata

PAISES_BRASIL = frozenset({"br", "bra", "brasil", "brazil"})
SIGLAS_UFS_BRASIL = frozenset(
    {
        "ac",
        "al",
        "ap",
        "am",
        "ba",
        "ce",
        "df",
        "es",
        "go",
        "ma",
        "mt",
        "ms",
        "mg",
        "pa",
        "pb",
        "pr",
        "pe",
        "pi",
        "rj",
        "rn",
        "rs",
        "ro",
        "rr",
        "sc",
        "sp",
        "se",
        "to",
    }
)
NOMES_UFS_BRASIL = frozenset(
    {
        "acre",
        "alagoas",
        "amapa",
        "amazonas",
        "bahia",
        "ceara",
        "distrito federal",
        "espirito santo",
        "goias",
        "maranhao",
        "mato grosso",
        "mato grosso do sul",
        "minas gerais",
        "para",
        "paraiba",
        "parana",
        "pernambuco",
        "piaui",
        "rio de janeiro",
        "rio grande do norte",
        "rio grande do sul",
        "rondonia",
        "roraima",
        "santa catarina",
        "sao paulo",
        "sergipe",
        "tocantins",
    }
)

# Apenas cidades suficientemente específicas para servir de evidência quando a
# fonte omite país e UF. Uma cidade ausente da lista permanece desconhecida.
CIDADES_BRASILEIRAS_CONHECIDAS = frozenset(
    {
        "alphaville",
        "belo horizonte",
        "brasilia",
        "campinas",
        "curitiba",
        "fortaleza",
        "goiania",
        "guarulhos",
        "porto alegre",
        "recife",
        "rio de janeiro",
        "salvador",
        "sao paulo",
        "taboao da serra",
    }
)


def _normalizar_texto(valor: str) -> str:
    """Remove acentos e reduz espaços para comparações geográficas."""

    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.casefold().split())


def pais_eh_brasil(pais: str | None) -> bool:
    """Aceita somente representações conhecidas do país Brasil."""

    return bool(pais) and _normalizar_texto(pais) in PAISES_BRASIL


def endereco_indica_brasil(*valores: str | None) -> bool:
    """Procura evidência verificável de que um endereço é brasileiro."""

    texto = " ".join(valor for valor in valores if valor).strip()

    if not texto:
        return False

    normalizado = _normalizar_texto(texto)

    if "brasil" in normalizado or "brazil" in normalizado:
        return True

    # CEP brasileiro: 00000-000 ou 00000000.
    if re.search(r"(?<!\d)\d{5}-?\d{3}(?!\d)", normalizado):
        return True

    tokens = set(re.findall(r"\b[a-z]{2}\b", normalizado))

    if tokens & SIGLAS_UFS_BRASIL:
        return True

    if any(estado in normalizado for estado in NOMES_UFS_BRASIL):
        return True

    return any(cidade in normalizado for cidade in CIDADES_BRASILEIRAS_CONHECIDAS)


def localizacao_eh_brasileira(
    pais: str | None,
    *enderecos: str | None,
) -> bool:
    """Reconhece Brasil por país declarado ou evidência postal/territorial.

    Um país explicitamente estrangeiro continua bloqueado. Quando o site omite
    o país, CEP, UF ou nome de estado brasileiro são evidência suficiente e
    evitam rejeitar vagas nacionais por uma omissão comum de ATS.
    """

    if pais and not pais_eh_brasil(pais):
        return False
    return endereco_indica_brasil(*enderecos)
