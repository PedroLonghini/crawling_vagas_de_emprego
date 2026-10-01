"""Interpretação dos campos da API Empregos a partir do inventário da página.

Para cada campo, as fontes são consultadas em ordem (plataforma/estruturado →
cabeçalho → seção do corpo → texto livre). A primeira que traz um valor
válido vence. Tudo fica registrado no ``_diagnostico``: de onde veio cada
campo, onde cada campo vazio foi procurado e o que foi lido e não usado.
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from observatorio_vagas.extraction.leitura.camadas import (
    LIXO_INTERFACE,
    InventarioPagina,
    secoes_do_texto,
)
from observatorio_vagas.extraction.leitura.texto_livre import (
    Indefinido,
    Leitura,
    Salario,
    interpretar_cep,
    interpretar_cnpj,
    interpretar_email_candidatura,
    interpretar_expiracao,
    interpretar_modalidade,
    interpretar_nivel,
    interpretar_recrutador,
    interpretar_salario,
    interpretar_vinculo,
    normalizar,
)

# Abaixo disso, é marcador de "não informado" (ex.: R$ 0,01 na Abler).
SALARIO_MINIMO_PLAUSIVEL = 100

MODALIDADE_API = {"REMOTE": "Remote", "HYBRID": "Hybrid", "ON_SITE": "On-site"}

# Hosts de plataformas: rodapé, logo e CNPJ da página são da plataforma.
HOSTS_PLATAFORMA = ("abler.com.br", "quickin.io", "lever.co", "randstad.com.br", "jobconvo.com")


def limpar_texto(texto: str | None) -> str | None:
    """Decodifica entidades e converte ``\\n`` literal em quebra de linha."""

    if texto is None:
        return None
    texto = html_mod.unescape(html_mod.unescape(texto))
    texto = texto.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", " ")
    texto = "\n".join(" ".join(linha.split()) for linha in texto.split("\n"))
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    return texto or None


def html_para_texto(valor: str | None) -> str | None:
    from observatorio_vagas.extraction.normalizacao_vaga import _limpar_html_formatado

    if not valor:
        return None
    # O parser já decodifica entidades; decodificar antes transformaria
    # "&lt; 3000" em tag e apagaria o texto.
    return limpar_texto(_limpar_html_formatado(valor) if "<" in valor else valor)


@dataclass(slots=True)
class Candidato:
    """Valor oferecido por uma fonte para um campo."""

    valor: Any
    origem: str
    bruto: Any = None


@dataclass(slots=True)
class LeituraVaga:
    """Campos da API e o diagnóstico que os explica."""

    campos: dict[str, Any] = field(default_factory=dict)
    diagnostico: dict[str, Any] = field(default_factory=dict)
    # Dados úteis fora da API (ex.: período do salário).
    extras: dict[str, Any] = field(default_factory=dict)


class _Diagnostico:
    def __init__(self, camadas: list[str]) -> None:
        self.camadas = camadas
        self.campos: dict[str, dict[str, Any]] = {}
        self.nao_mapeado: list[dict[str, Any]] = []

    def preenchido(self, campo: str, candidato: Candidato) -> None:
        bruto = candidato.bruto if candidato.bruto is not None else candidato.valor
        self.campos[campo] = {"origem": candidato.origem, "valor_bruto": _curto(bruto)}

    def vazio(self, campo: str, procurado_em: list[str], motivo: str | None = None) -> None:
        registro: dict[str, Any] = {"vazio": True, "procurado_em": procurado_em}
        if motivo:
            registro["motivo"] = motivo
        self.campos[campo] = registro

    def exportar(self) -> dict[str, Any]:
        return {
            "camadas_encontradas": self.camadas,
            "campos": self.campos,
            "nao_mapeado": self.nao_mapeado,
        }


def _curto(valor: Any) -> Any:
    if isinstance(valor, str) and len(valor) > 160:
        return valor[:157] + "..."
    if isinstance(valor, (Decimal, date, datetime)):
        return str(valor)
    if isinstance(valor, Salario):
        return {"min": str(valor.minimo), "max": str(valor.maximo), "periodo": valor.periodicidade}
    return valor


def _primeiro(diag: _Diagnostico, campo: str, fontes: list[tuple[str, Any]], validar=None):
    """Percorre as fontes em ordem e grava a escolha (ou o vazio) no diagnóstico."""

    procurado: list[str] = []
    motivos: list[str] = []

    for nome, obter in fontes:
        procurado.append(nome)
        try:
            resultado = obter() if callable(obter) else obter
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            resultado = None

        if resultado is None or resultado == "" or resultado == []:
            continue
        if isinstance(resultado, Indefinido):
            motivos.append(f"{nome}: {resultado.motivo}")
            continue
        candidato = (
            resultado
            if isinstance(resultado, Candidato)
            else Candidato(resultado.valor, f"{nome}.{resultado.origem}", resultado.valor_bruto)
            if isinstance(resultado, Leitura)
            else Candidato(resultado, nome)
        )
        if validar is not None:
            problema = validar(candidato.valor)
            if problema:
                motivos.append(f"{nome}: {problema}")
                continue
        diag.preenchido(campo, candidato)
        return candidato.valor

    diag.vazio(campo, procurado, "; ".join(motivos) or None)
    return None


# --------------------------------------------------------------------------
# Tradutores por plataforma: onde cada dado está em cada site.
# --------------------------------------------------------------------------


def plataforma_do_host(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").casefold()
    for nome, sufixo in (
        ("abler", "abler.com.br"),
        ("quickin", "quickin.io"),
        ("lever", "lever.co"),
        ("randstad", "randstad.com.br"),
    ):
        if host.endswith(sufixo):
            return nome
    return None


def _abler(atributos: dict[str, Any]) -> tuple[dict[str, Candidato], list[dict[str, Any]]]:
    """API pública da Abler (JSON:API ``data[].attributes``)."""

    usados = {
        "title",
        "full_url",
        "company_name",
        "customer_name",
        "company_description",
        "role_description",
        "mandatory_requirements",
        "desirable_requirements",
        "description",
        "working_journey",
        "city",
        "state",
        "salary",
        "salary_value",
        "initial_salary_range",
        "final_salary_range",
        "salary_periodicity",
        "work_type",
        "contracting_regime",
        "seniority_level_formatted",
        "close_on",
        "responsible_name",
    }
    c: dict[str, Candidato] = {}

    if atributos.get("full_url"):
        c["company.applyUrl"] = Candidato(atributos["full_url"], "plataforma.full_url")
    nome = atributos.get("company_name") or atributos.get("customer_name")
    if nome:
        c["company.name"] = Candidato(nome, "plataforma.company_name")
    if limpo := html_para_texto(atributos.get("company_description")):
        c["company.description"] = Candidato(limpo, "plataforma.company_description")

    blocos = [
        ("", atributos.get("role_description") or atributos.get("description")),
        ("Requisitos", atributos.get("mandatory_requirements")),
        ("Diferenciais", atributos.get("desirable_requirements")),
        ("Jornada", atributos.get("working_journey")),
    ]
    partes = [
        (f"{titulo}\n{texto}" if titulo else texto)
        for titulo, bruto in blocos
        if (texto := html_para_texto(bruto))
    ]
    if partes:
        c["description"] = Candidato("\n\n".join(partes), "plataforma.role_description+requisitos")

    cidade, estado = atributos.get("city"), atributos.get("state")
    if cidade:
        c["location.address"] = Candidato(
            ", ".join(x for x in (cidade, estado) if x), "plataforma.city/state"
        )

    minimo = (
        atributos.get("initial_salary_range")
        or atributos.get("salary_value")
        or atributos.get("salary")
    )
    maximo = atributos.get("final_salary_range")
    # Valores simbólicos (R$ 0,01) são o "não informado" da plataforma.
    if minimo and float(minimo) >= SALARIO_MINIMO_PLAUSIVEL:
        c["salary"] = Candidato(
            Salario(
                Decimal(str(minimo)),
                Decimal(str(maximo)) if maximo else None,
                atributos.get("salary_periodicity"),
            ),
            "plataforma.salary",
        )
    if atributos.get("work_type"):
        rotulo = (
            " ".join(atributos["work_type"])
            if isinstance(atributos["work_type"], list)
            else str(atributos["work_type"])
        )
        c["workplaceTypes"] = Candidato(rotulo, "plataforma.work_type")
    if atributos.get("contracting_regime"):
        c["employmentStatus"] = Candidato(
            atributos["contracting_regime"], "plataforma.contracting_regime"
        )
    if atributos.get("seniority_level_formatted"):
        c["experienceLevel"] = Candidato(
            atributos["seniority_level_formatted"], "plataforma.seniority_level"
        )
    if atributos.get("close_on"):
        c["expireAt"] = Candidato(str(atributos["close_on"])[:10], "plataforma.close_on")
    if atributos.get("responsible_name"):
        c["company.recruiterName"] = Candidato(
            atributos["responsible_name"], "plataforma.responsible_name"
        )

    sobras = [
        {"origem": "plataforma", "rotulo": chave, "valor": _curto(valor)}
        for chave, valor in atributos.items()
        if chave.endswith(("_formatted", "_without_tags")) is False
        and chave not in usados
        and chave
        in {
            "educational_level",
            "courses",
            "requisition_type_formatted",
            "cbo",
            "nomenclature_cbo",
            "quantity",
            "neighborhood",
            "for_internship",
            "candidature_email_address",
            "tech_skills",
            "soft_skills",
            "experience_time_min",
            "required_experience",
            "cnh",
        }
        and valor not in (None, "", [], False)
    ]
    return c, sobras


def _quickin(inv: InventarioPagina) -> tuple[dict[str, Candidato], list[dict[str, Any]]]:
    """Estado Nuxt 2 da Quickin: ``data[0].job``."""

    c: dict[str, Candidato] = {}
    try:
        job = inv.json_embutido["__NUXT__"]["data"][0]["job"]
    except (KeyError, IndexError, TypeError):
        return c, []

    if job.get("workplace_type"):
        c["workplaceTypes"] = Candidato(job["workplace_type"], "json_embutido.job.workplace_type")
    partes = [
        texto
        for titulo, bruto in (
            ("", job.get("description")),
            ("Requisitos", job.get("requirements")),
            ("Benefícios", job.get("benefits")),
        )
        if (texto := html_para_texto(bruto))
        and (texto := f"{titulo}\n{texto}" if titulo else texto)
    ]
    if partes:
        c["description"] = Candidato(
            "\n\n".join(partes), "json_embutido.job.description+requirements"
        )
    local = ", ".join(x for x in (job.get("city"), job.get("region")) if x)
    if local:
        c["location.address"] = Candidato(local, "json_embutido.job.city/region")

    sobras = [
        {"origem": "json_embutido", "rotulo": chave, "valor": _curto(job[chave])}
        for chave in ("code", "position_category", "contract_id", "career_url")
        if job.get(chave)
    ]
    return c, sobras


def _lever(inv: InventarioPagina) -> tuple[dict[str, Candidato], list[dict[str, Any]]]:
    c: dict[str, Candidato] = {}
    rotulos = inv.classes_cabecalho
    if rotulos.get("workplaceTypes"):
        c["workplaceTypes"] = Candidato(rotulos["workplaceTypes"], "cabecalho.workplaceTypes")
    if rotulos.get("commitment"):
        c["employmentStatus"] = Candidato(rotulos["commitment"], "cabecalho.commitment")
    if rotulos.get("location"):
        c["location.address"] = Candidato(rotulos["location"], "cabecalho.location")
    sobras = [
        {"origem": "cabecalho", "rotulo": classe, "valor": rotulos[classe]}
        for classe in ("department", "team")
        if rotulos.get(classe)
    ]
    return c, sobras


# --------------------------------------------------------------------------
# Validação
# --------------------------------------------------------------------------

_SEGMENTOS_LOGIN = {"login", "signin", "sign-in", "sign_in", "entrar", "auth", "account"}
_CARREIRAS_GERAL = re.compile(
    r"/(trabalhe-?conosco|carreiras?|careers?|vagas|jobs|oportunidades)/?$", re.IGNORECASE
)


def validar_apply_url(url: str, *, url_vaga: str, id_vaga: str | None) -> str | None:
    partes = urlsplit(url)
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        return "não é http(s)"
    segmentos = {segmento.casefold() for segmento in partes.path.split("/") if segmento}
    if segmentos & _SEGMENTOS_LOGIN or "sitemap" in partes.path.casefold():
        return "login/sitemap"
    if partes.path in {"", "/"}:
        return "raiz do site"
    if _CARREIRAS_GERAL.search(partes.path) and not (id_vaga and id_vaga in url):
        return "página de carreiras geral, não a da vaga"
    caminho_e_query = (partes.path + "?" + partes.query).casefold()
    if id_vaga and id_vaga.casefold() in caminho_e_query:
        return None
    if (
        url.rstrip("/") == url_vaga.rstrip("/")
        or urlsplit(url_vaga).path.rstrip("/") in partes.path
    ):
        return None
    return "não contém o ID nem leva à página da vaga"


def validar_descricao(texto: str) -> str | None:
    linhas = [linha for linha in texto.split("\n") if linha.strip()]
    uteis = [linha for linha in linhas if not LIXO_INTERFACE.match(normalizar(linha.strip()))]
    if sum(len(linha) for linha in uteis) < 100:
        return "menos de um parágrafo útil"
    return None


def limpar_descricao(texto: str) -> str:
    """Remove linhas que são só texto de interface (cookies, ✕, destaque...)."""

    linhas = []
    for linha in texto.split("\n"):
        if LIXO_INTERFACE.match(normalizar(linha.strip())):
            continue
        linhas.append(linha)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


def validar_endereco(endereco: str) -> str | None:
    if len(endereco) > 100:
        return "mais de 100 caracteres"
    if endereco[:1].islower():
        return "começa no meio de palavra"
    if re.search(r"[.!?]\s+\w", endereco) or len(endereco.split()) > 14:
        return "parece frase da vaga"
    return None


# --------------------------------------------------------------------------
# Interpretação campo a campo
# --------------------------------------------------------------------------


def _jsonld(inv: InventarioPagina, *caminho: str) -> Any:
    for posting in inv.json_ld:
        atual: Any = posting
        for chave in caminho:
            if isinstance(atual, list):
                atual = atual[0] if atual else None
            atual = atual.get(chave) if isinstance(atual, dict) else None
        if atual not in (None, "", []):
            return atual
    return None


def _endereco_jsonld(inv: InventarioPagina) -> str | None:
    endereco = _jsonld(inv, "jobLocation", "address")
    if isinstance(endereco, dict):
        partes = [endereco.get("addressLocality"), endereco.get("addressRegion")]
        return ", ".join(str(p) for p in partes if p) or None
    return endereco if isinstance(endereco, str) else None


def _salario_jsonld(inv: InventarioPagina) -> Leitura | Indefinido | None:
    base = _jsonld(inv, "baseSalary")
    if not isinstance(base, dict):
        return None
    valor = base.get("value", base)
    if isinstance(valor, dict):
        minimo = valor.get("minValue") or valor.get("value")
        maximo = valor.get("maxValue")
        periodo = valor.get("unitText")
    else:
        minimo, maximo, periodo = valor, None, base.get("unitText")
    try:
        minimo_d = Decimal(str(minimo)) if minimo not in (None, "") else Decimal(0)
        maximo_d = Decimal(str(maximo)) if maximo not in (None, "") else None
    except ArithmeticError:
        return None
    if minimo_d < SALARIO_MINIMO_PLAUSIVEL and not (
        maximo_d and maximo_d >= SALARIO_MINIMO_PLAUSIVEL
    ):
        return Indefinido("salário 0 ou simbólico na fonte (não informado)", str(base)[:80])
    return Leitura(
        Salario(minimo_d, maximo_d if maximo_d and maximo_d > 0 else None, periodo), "baseSalary"
    )


def _expiracao_jsonld(inv: InventarioPagina, plataforma: str | None) -> Leitura | Indefinido | None:
    validade = _jsonld(inv, "validThrough")
    if not validade:
        return None
    publicada = _jsonld(inv, "datePosted")
    try:
        if publicada and plataforma == "quickin":
            dias = (
                date.fromisoformat(str(validade)[:10]) - date.fromisoformat(str(publicada)[:10])
            ).days
            if dias == 90:
                return Indefinido(
                    "data calculada pela plataforma (publicação + 90 dias)", str(validade)
                )
        return Leitura(date.fromisoformat(str(validade)[:10]).isoformat(), "validThrough")
    except ValueError:
        numerica = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(validade))
        if numerica:
            dia, mes, ano = map(int, numerica.groups())
            try:
                return Leitura(date(ano, mes, dia).isoformat(), "validThrough", str(validade))
            except ValueError:
                pass
        return Indefinido("validThrough em formato desconhecido", str(validade))


def _organizacao(documento: dict[str, Any]) -> dict[str, Any]:
    """``hiringOrganization`` pode vir como objeto, lista ou só o nome."""

    organizacao = documento.get("hiringOrganization")
    if isinstance(organizacao, list):
        organizacao = next((item for item in organizacao if isinstance(item, dict)), None)
    if isinstance(organizacao, str):
        return {"name": organizacao}
    return organizacao if isinstance(organizacao, dict) else {}


def cnpj_valido(numero: str) -> bool:
    """14 dígitos com os dois dígitos verificadores corretos."""

    digitos = re.sub(r"\D", "", numero)
    if len(digitos) != 14 or digitos == digitos[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d) * p for d, p in zip(digitos[:tamanho], pesos, strict=True))
        verificador = 0 if soma % 11 < 2 else 11 - soma % 11
        if int(digitos[tamanho]) != verificador:
            return False
    return True


def _posting_da_vaga(
    postings: list[dict[str, Any]], *, documento: dict[str, Any], titulo: str
) -> list[dict[str, Any]]:
    """Só o JobPosting desta vaga; numa página com várias, nenhum que não bata."""

    if len(postings) <= 1:
        return postings

    def chaves(item: dict[str, Any]) -> set[str]:
        identificador = item.get("identifier")
        if isinstance(identificador, dict):
            identificador = identificador.get("value") or identificador.get("@id")
        return {str(v).strip().casefold() for v in (item.get("url"), identificador) if v}

    alvo = chaves(documento)
    iguais = [item for item in postings if alvo & chaves(item)]
    if not iguais:
        iguais = [
            item
            for item in postings
            if str(item.get("title", "")).strip().casefold() == titulo.strip().casefold()
        ]
    return iguais[:1]


def _nome_empresa(bruto: str) -> str:
    """Separa a cidade grudada no nome: "TAESA (João Pessoa/PB)" -> "TAESA"."""

    return re.sub(r"\s*\((?:[^()]*/[A-Z]{2}|[^()]*\b[A-Z]{2})\)\s*$", "", bruto).strip()


def _vinculo(valor: str, origem: str) -> Leitura | Indefinido | None:
    return interpretar_vinculo(valor, origem)


def _nivel_rotulo(valor: str) -> Leitura | None:
    return interpretar_nivel(valor, "rotulo")


def ler_vaga(
    inv: InventarioPagina,
    *,
    titulo: str,
    id_externo: str,
    url_vaga: str,
    documento: dict[str, Any] | None = None,
    atributos_plataforma: dict[str, Any] | None = None,
    url_candidatura_html: str | None = None,
    hoje: date | None = None,
    varias_vagas_na_pagina: bool = False,
) -> LeituraVaga:
    """Preenche os campos da API procurando em todas as camadas.

    Numa página com várias vagas (listagem), as camadas da página inteira não
    pertencem a esta vaga: só o JobPosting que bate com ela e o rodapé valem.
    """

    hoje = hoje or date.today()
    documento = documento or {}
    postings = _posting_da_vaga(inv.json_ld, documento=documento, titulo=titulo)
    if varias_vagas_na_pagina or len(inv.json_ld) > 1:
        inv = replace(
            inv,
            json_ld=postings,
            rotulos=[] if varias_vagas_na_pagina else inv.rotulos,
            secoes=[] if varias_vagas_na_pagina else inv.secoes,
            texto_corpo="" if varias_vagas_na_pagina else inv.texto_corpo,
            meta={} if varias_vagas_na_pagina else inv.meta,
        )
    plataforma = plataforma_do_host(url_vaga)
    eh_plataforma = plataforma is not None or any(
        (urlsplit(url_vaga).hostname or "").endswith(h) for h in HOSTS_PLATAFORMA
    )
    diag = _Diagnostico(inv.camadas())
    p: dict[str, Candidato] = {}
    sobras: list[dict[str, Any]] = []

    if plataforma == "abler" and atributos_plataforma:
        p, sobras = _abler(atributos_plataforma)
        diag.camadas.insert(0, "api_plataforma")
    elif plataforma == "quickin":
        p, sobras = _quickin(inv)
    elif plataforma == "lever":
        p, sobras = _lever(inv)
    diag.nao_mapeado.extend(sobras)

    def plat(campo: str) -> Candidato | None:
        return p.get(campo)

    # Texto completo da vaga para as regras de texto livre.
    descricao_doc = html_para_texto(documento.get("description"))
    texto_vaga = (
        (p["description"].valor if "description" in p else None) or descricao_doc or inv.texto_corpo
    )
    secoes = inv.secoes if inv.secoes else secoes_do_texto(texto_vaga or "")
    secoes_vaga = secoes_do_texto(texto_vaga or "")

    def secao(nome: str) -> str | None:
        for lista in (secoes_vaga, secoes):
            for item in lista:
                if item.nome == nome and item.texto:
                    return item.texto
        return None

    campos: dict[str, Any] = {}

    # 11, 12, 13: preservados (seção 5 da especificação).
    campos["externalJobPostingId"] = id_externo
    campos["jobPostingOperationType"] = "CREATE"
    # Mesmo título de antes, só com entidades HTML decodificadas (&amp; -> &).
    titulo = html_mod.unescape(titulo)
    campos["title"] = titulo
    diag.preenchido("externalJobPostingId", Candidato(id_externo, "preservado"))
    diag.preenchido("title", Candidato(titulo, "preservado"))

    empresa: dict[str, Any] = {}

    # 1. applyUrl
    def _validar_apply(url: str) -> str | None:
        return validar_apply_url(url, url_vaga=url_vaga, id_vaga=_id_curto(id_externo))

    empresa["applyUrl"] = _primeiro(
        diag,
        "company.applyUrl",
        [
            ("plataforma", plat("company.applyUrl")),
            ("json_ld.url", lambda: _jsonld(inv, "url")),
            (
                "acoes.botao_candidatura",
                lambda: urljoin(url_vaga, url_candidatura_html) if url_candidatura_html else None,
            ),
            (
                "documento.url",
                lambda: documento.get("_observatorio_apply_url") or documento.get("url"),
            ),
            ("meta.canonical", lambda: inv.meta.get("canonical")),
            ("pagina_da_vaga", None if varias_vagas_na_pagina else url_vaga),
        ],
        validar=_validar_apply,
    )

    # 2. name
    nome = _primeiro(
        diag,
        "company.name",
        [
            ("plataforma", plat("company.name")),
            (
                "json_ld.hiringOrganization.name",
                lambda: (
                    None if plataforma == "randstad" else _jsonld(inv, "hiringOrganization", "name")
                ),
            ),
            (
                "documento.hiringOrganization.name",
                lambda: _organizacao(documento).get("name"),
            ),
            ("meta.og:site_name", lambda: None if eh_plataforma else inv.meta.get("og:site_name")),
        ],
        validar=lambda v: (
            "nome de conta/página de carreiras"
            if re.search(r"^(nova pagina|abler|demo\d*|teste)$", normalizar(str(v)).strip())
            else None
        ),
    )
    if nome:
        empresa["name"] = _nome_empresa(str(nome))

    # 3. logo
    logo = _primeiro(
        diag,
        "company.logoUrl",
        [
            ("plataforma", plat("company.logoUrl")),
            (
                "json_ld.hiringOrganization.logo",
                lambda: (
                    None if plataforma == "randstad" else _jsonld(inv, "hiringOrganization", "logo")
                ),
            ),
            (
                "documento.hiringOrganization.logo",
                lambda: _organizacao(documento).get("logo"),
            ),
        ],
        validar=lambda v: "favicon" if "favicon" in str(v).casefold() else None,
    )
    if plataforma == "randstad" and not logo:
        diag.campos["company.logoUrl"]["motivo"] = (
            "logo do JSON-LD é da Randstad, não da contratante"
        )
    if isinstance(logo, dict):
        logo = logo.get("url")
    if logo:
        logo = urljoin(url_vaga, str(logo))
        if urlsplit(logo).scheme not in {"http", "https"}:
            diag.campos["company.logoUrl"] = {"vazio": True, "motivo": "logo sem URL http(s)"}
            logo = None
    if logo:
        empresa["logoUrl"] = logo

    # 4. description da empresa
    descricao_empresa = _primeiro(
        diag,
        "company.description",
        [
            ("plataforma", plat("company.description")),
            ("corpo.secao_sobre_empresa", lambda: secao("sobre_empresa")),
            (
                "json_ld.hiringOrganization.description",
                lambda: html_para_texto(_jsonld(inv, "hiringOrganization", "description")),
            ),
        ],
    )
    if descricao_empresa:
        empresa["description"] = descricao_empresa

    # 5. industries
    setor = _primeiro(
        diag,
        "company.industries",
        [
            ("json_ld.industry", lambda: _jsonld(inv, "industry")),
            (
                "cabecalho.rotulo_setor",
                lambda: (r := inv.rotulo(r"^setor", r"^segmento", r"^industria")) and r[1],
            ),
        ],
    )
    if setor:
        empresa["industries"] = setor

    # 8, 9. recrutador
    recrutador = _primeiro(
        diag,
        "company.recruiterName",
        [
            ("plataforma", plat("company.recruiterName")),
            ("texto_livre", lambda: interpretar_recrutador(texto_vaga or "", "corpo")),
        ],
    )
    if recrutador:
        empresa["recruiterName"] = recrutador
    email = _primeiro(
        diag,
        "company.recruiterEmail",
        [
            ("acoes.mailto", lambda: next((e for e in inv.emails if not eh_plataforma), None)),
            ("texto_livre", lambda: interpretar_email_candidatura(texto_vaga or "", "corpo")),
        ],
    )
    if email:
        empresa["recruiterEmail"] = email

    # 10. CNPJ (em plataforma, o do rodapé é da plataforma)
    cnpj = _primeiro(
        diag,
        "company.nationalRegister",
        [
            ("plataforma", plat("company.nationalRegister")),
            (
                "json_ld.hiringOrganization.taxID",
                lambda: _jsonld(inv, "hiringOrganization", "taxID"),
            ),
            ("corpo", lambda: interpretar_cnpj(texto_vaga or "", "corpo")),
            (
                "rodape",
                lambda: None if eh_plataforma else interpretar_cnpj(inv.texto_rodape, "rodape"),
            ),
        ],
        validar=lambda v: None if cnpj_valido(str(v)) else "CNPJ inválido (dígito verificador)",
    )
    if cnpj:
        empresa["nationalRegister"] = re.sub(r"\D", "", str(cnpj))

    campos["company"] = empresa

    # 14. description
    descricao = _primeiro(
        diag,
        "description",
        [
            ("plataforma", plat("description")),
            ("json_ld.description", lambda: html_para_texto(_jsonld(inv, "description"))),
            ("documento.description", descricao_doc),
            ("corpo", lambda: inv.texto_corpo),
        ],
        validar=lambda v: validar_descricao(limpar_descricao(str(v))),
    )
    if descricao:
        campos["description"] = limpar_descricao(str(descricao))

    # 15, 16, 17. location
    local: dict[str, Any] = {}
    endereco = _primeiro(
        diag,
        "location.address",
        [
            ("plataforma", plat("location.address")),
            ("json_ld.jobLocation.address", lambda: _endereco_jsonld(inv)),
            (
                "cabecalho.rotulo_local",
                lambda: (
                    (r := inv.rotulo(r"^local", r"^localizacao", r"^cidade", r"^location")) and r[1]
                ),
            ),
            ("documento.jobLocation", lambda: _endereco_documento(documento)),
        ],
        validar=lambda v: validar_endereco(limpar_texto(str(v)) or ""),
    )
    if endereco:
        local["address"] = limpar_texto(str(endereco))
    cep = _primeiro(
        diag,
        "location.postalCode",
        [
            ("json_ld.postalCode", lambda: _jsonld(inv, "jobLocation", "address", "postalCode")),
            ("corpo", lambda: interpretar_cep(texto_vaga or "", "corpo")),
            (
                "rodape",
                lambda: None if eh_plataforma else interpretar_cep(inv.texto_rodape, "rodape"),
            ),
        ],
    )
    if cep:
        local["postalCode"] = cep
    geo = _primeiro(
        diag, "location.geolocation", [("json_ld.geo", lambda: _jsonld(inv, "jobLocation", "geo"))]
    )
    if isinstance(geo, dict) and geo.get("latitude") and geo.get("longitude"):
        local["geolocation"] = f"{geo['latitude']},{geo['longitude']}"
        if plataforma == "randstad":
            diag.campos["location.geolocation"]["observacao"] = (
                "coordenada da fonte; pode ser o centro da cidade"
            )
    campos["location"] = local

    # 18, 19. salary
    salario = _primeiro(
        diag,
        "salary",
        [
            ("plataforma", plat("salary")),
            ("json_ld.baseSalary", lambda: _salario_jsonld(inv)),
            (
                "corpo.secao_remuneracao",
                lambda: interpretar_salario(
                    secao("remuneracao") and f"Salário: {secao('remuneracao')}" or "", "secao"
                ),
            ),
            ("texto_livre", lambda: interpretar_salario(texto_vaga or "", "texto")),
        ],
    )
    if isinstance(salario, Salario):
        campos["salary"] = {"min": int(salario.minimo)} | (
            {"max": int(salario.maximo)} if salario.maximo else {}
        )

    # 20. workplaceTypes
    modalidade = _primeiro(
        diag,
        "workplaceTypes",
        [
            (
                "plataforma",
                lambda: (
                    plat("workplaceTypes")
                    and interpretar_modalidade("", "", str(plat("workplaceTypes").valor))
                ),
            ),
            (
                "json_ld.jobLocationType",
                lambda: (
                    (v := _jsonld(inv, "jobLocationType"))
                    and ("REMOTE" if "TELECOMMUTE" in str(v).upper() else None)
                ),
            ),
            (
                "documento.rotulo_modalidade",
                lambda: (
                    (v := _modalidade_documento(documento)) and interpretar_modalidade("", "", v)
                ),
            ),
            ("titulo+texto", lambda: interpretar_modalidade(titulo, texto_vaga or "")),
        ],
    )
    if modalidade:
        campos["workplaceTypes"] = MODALIDADE_API[modalidade]

    # 21. employmentStatus
    vinculo = _primeiro(
        diag,
        "employmentStatus",
        [
            (
                "plataforma",
                lambda: (
                    plat("employmentStatus")
                    and _vinculo(str(plat("employmentStatus").valor), "plataforma")
                ),
            ),
            (
                "json_ld.employmentType",
                lambda: (
                    (v := _jsonld(inv, "employmentType"))
                    and _vinculo(
                        " ".join(v) if isinstance(v, list) else str(v).replace("_", " "), "json_ld"
                    )
                ),
            ),
            (
                "documento.employmentType",
                lambda: (
                    (v := documento.get("employmentType"))
                    and _vinculo(
                        " ".join(v) if isinstance(v, list) else str(v).replace("_", " "),
                        "documento",
                    )
                ),
            ),
            ("titulo", lambda: _vinculo(titulo, "titulo")),
            ("texto_livre", lambda: _vinculo(texto_vaga or "", "texto")),
        ],
    )
    if vinculo:
        campos["employmentStatus"] = vinculo
    elif plat("employmentStatus"):
        diag.nao_mapeado.append(
            {
                "origem": plat("employmentStatus").origem,
                "rotulo": "vinculo",
                "valor": plat("employmentStatus").valor,
            }
        )

    # 22. experienceLevel
    nivel = _primeiro(
        diag,
        "experienceLevel",
        [
            (
                "plataforma",
                lambda: (
                    plat("experienceLevel") and _nivel_rotulo(str(plat("experienceLevel").valor))
                ),
            ),
            (
                "cabecalho.rotulo_nivel",
                lambda: (
                    (r := inv.rotulo(r"^nivel", r"^senioridade", r"^level")) and _nivel_rotulo(r[1])
                ),
            ),
            ("titulo", lambda: interpretar_nivel(titulo, "titulo")),
            (
                "corpo.requisitos",
                lambda: interpretar_nivel(secao("requisitos") or "", "requisitos"),
            ),
        ],
    )
    if nivel:
        campos["experienceLevel"] = nivel

    # 24. expireAt (nunca calculado)
    expira = _primeiro(
        diag,
        "expireAt",
        [
            ("plataforma", plat("expireAt")),
            ("json_ld.validThrough", lambda: _expiracao_jsonld(inv, plataforma)),
            (
                "texto_livre",
                lambda: interpretar_expiracao(texto_vaga or "", "texto", referencia=hoje),
            ),
        ],
    )
    if expira:
        campos["expireAt"] = str(expira)

    # Rótulos e seções lidos e não usados.
    usados_rotulo = {
        "local",
        "localizacao",
        "cidade",
        "location",
        "setor",
        "segmento",
        "nivel",
        "senioridade",
    }
    for rotulo, valor in inv.rotulos[:40]:
        palavras = normalizar(rotulo).split()
        if palavras and palavras[0] not in usados_rotulo and not str(valor).startswith("//"):
            diag.nao_mapeado.append(
                {"origem": "cabecalho", "rotulo": rotulo, "valor": _curto(valor)}
            )
    for item in secoes_vaga:
        if item.nome in {"jornada", "beneficios", "diferenciais"} and item.texto:
            diag.nao_mapeado.append(
                {"origem": "corpo", "secao": item.titulo, "valor": _curto(item.texto)}
            )
    for chave in ("jornada",):
        if inv.secao(chave) and not any(n.get("secao") for n in diag.nao_mapeado):
            diag.nao_mapeado.append(
                {"origem": "corpo", "secao": chave, "valor": _curto(inv.secao(chave).texto)}
            )

    extras = {"salary_periodo": salario.periodicidade} if isinstance(salario, Salario) else {}
    return LeituraVaga(campos=campos, diagnostico=diag.exportar(), extras=extras)


def _modalidade_documento(documento: dict[str, Any]) -> str | None:
    """Rótulo de modalidade já lido pelo extrator atual (microdado ou rótulo)."""

    valores = [documento.get("jobLocationType")]
    local = documento.get("jobLocation")
    for item in local if isinstance(local, list) else [local]:
        if isinstance(item, dict):
            propriedades = item.get("additionalProperty")
            for propriedade in propriedades if isinstance(propriedades, list) else [propriedades]:
                if isinstance(propriedade, dict):
                    valores.append(propriedade.get("value"))
    texto = " ".join(str(v) for v in valores if v)
    return texto or None


def _id_curto(id_externo: str) -> str:
    """Parte do ID que aparece na URL ("abler-340518" -> "340518")."""

    return (
        id_externo.split("-", 1)[1] if id_externo.startswith(("abler-", "eTalent_")) else id_externo
    )


def _endereco_documento(documento: dict[str, Any]) -> str | None:
    local = documento.get("jobLocation")
    if isinstance(local, list):
        local = local[0] if local else None
    endereco = (local or {}).get("address") if isinstance(local, dict) else None
    if isinstance(endereco, dict):
        return (
            ", ".join(
                str(x)
                for x in (endereco.get("addressLocality"), endereco.get("addressRegion"))
                if x
            )
            or None
        )
    return endereco if isinstance(endereco, str) else None


def job_id_da_query(url: str) -> str | None:
    return (parse_qs(urlsplit(url).query).get("job_id") or [None])[0]
