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

from observatorio_vagas.extraction.agregadores import (
    e_agregador,
    e_site_de_terceiros,
    nome_e_do_site,
)
from observatorio_vagas.extraction.leitura.camadas import (
    LIXO_INTERFACE,
    InventarioPagina,
    secoes_do_texto,
)
from observatorio_vagas.extraction.leitura.texto_livre import (
    REGIOES_DO_BRASIL,
    Indefinido,
    Leitura,
    Salario,
    cidade_do_titulo,
    interpretar_cep,
    interpretar_cnpj,
    interpretar_email_candidatura,
    interpretar_expiracao,
    interpretar_localizacao,
    interpretar_modalidade,
    interpretar_nivel,
    interpretar_recrutador,
    interpretar_salario,
    interpretar_vinculo,
    normalizar,
    uf_da_cidade,
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
    if "<" not in valor and "&lt;" in valor:
        # HTML escapado ("&lt;p&gt;..."): desfaz o escape e lê como HTML.
        valor = html_mod.unescape(valor)
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


# Fim do bloco da vaga no corpo: botão de candidatura, compartilhar, vagas
# relacionadas, rodapé legal. Comparado com a linha normalizada (sem acento).
_FIM_DO_BLOCO = re.compile(
    r"^(candidat|inscreva|compartilh|share|vagas? (relacionad|semelhant|similar|recentes)|"
    r"outras vagas|veja tamb|leia tamb|newsletter|politica de privacidade|"
    r"todos os direitos|copyright|©|outras pessoas tambem|voce tambem pode|quem viu esta|"
    r"mais vagas|vagas em destaque|cadastre-se para|ocorreu um erro|"
    # Lista lateral de outras vagas (Drugovich: "Vagas Abertas" com ~60 títulos).
    r"vagas abertas|outras oportunidades|vagas dispon[ií]veis|todas as vagas)"
)
_RESUMO = re.compile(r"(\.\.\.|…)\s*$|\b(saiba|leia|ver|veja) mais\W*$", re.IGNORECASE)
TAMANHO_MAXIMO_RESUMO = 300
TAMANHO_MAXIMO_BLOCO = 8000


def parece_resumo(texto: str | None) -> bool:
    """Texto curto ou cortado ("...", "Saiba mais"): típico de meta description."""

    texto = (texto or "").strip()
    return bool(texto) and (len(texto) < TAMANHO_MAXIMO_RESUMO or bool(_RESUMO.search(texto)))


_AUTOR_E_DATA = re.compile(r"^(por|publicad[oa]|postad[oa])\b.{0,60}\d{4}\s*$")
# "Vagas de emprego em Curitiba" é listagem; "Vaga de emprego: Vendedor" é a vaga.
_TITULO_GENERICO = re.compile(r"^(tag|categoria|arquivo|vagas de emprego)\b|^\d[\d.]*\s+vagas")
TAMANHO_MINIMO_TITULO = 8
TAMANHO_MAXIMO_LINHA_DE_FIM = 40
REPETICOES_QUE_INDICAM_LISTAGEM = 3


LINHAS_ATE_O_TEXTO = 15


def _linha_do_titulo(linhas: list[str], alvo: str) -> int | None:
    """Ocorrência do título que é seguida de texto de verdade (e não a do menu ou formulário).

    Prefere as linhas que SÃO o título; entre elas (ou entre as que o contêm), a primeira
    que tem uma frase longa logo abaixo.
    """

    normalizadas = [normalizar(linha).strip(" -–|:") for linha in linhas]
    exatas = [i for i, linha in enumerate(normalizadas) if linha == alvo]
    candidatas = exatas or [i for i, linha in enumerate(normalizadas) if alvo in linha]
    for indice in candidatas:
        seguintes = linhas[indice + 1 : indice + 1 + LINHAS_ATE_O_TEXTO]
        if any(len(linha.strip()) >= 80 for linha in seguintes):
            return indice
    return candidatas[0] if candidatas else None


def bloco_da_vaga(texto_corpo: str, titulo: str) -> str | None:
    """Texto do corpo entre o título da vaga e o fim dela (candidatura, rodapé...)."""

    if not texto_corpo or not titulo:
        return None
    alvo = normalizar(html_mod.unescape(titulo)).strip(" -–|:")[:60]
    # Título curto ("Tag:", "Vendedor") casa com qualquer linha; título genérico é
    # de listagem.
    if len(alvo) < TAMANHO_MINIMO_TITULO or _TITULO_GENERICO.match(alvo):
        return None
    linhas = texto_corpo.split("\n")
    inicio = _linha_do_titulo(linhas, alvo)
    if inicio is None:
        return None
    bloco: list[str] = []
    achou_fim = False
    for linha in linhas[inicio + 1 :]:
        limpa = linha.strip()
        normalizada = normalizar(limpa)
        # Marcador de fim só em linha curta (botão, rodapé): "Candidatos interessados
        # enviar currículo para..." é parte da vaga.
        if len(limpa) <= TAMANHO_MAXIMO_LINHA_DE_FIM and _FIM_DO_BLOCO.match(normalizada):
            achou_fim = True
            break
        # No começo do bloco: pula o título repetido (breadcrumb + h1) e "Por: Fulano - data".
        if not bloco and (
            not limpa or normalizada.strip(" -–|:") == alvo or _AUTOR_E_DATA.match(normalizada)
        ):
            continue
        bloco.append(linha)
    texto = "\n".join(bloco).strip()
    # Sem fim e grande demais: é listagem (cards até o rodapé), não uma vaga.
    if not achou_fim and len(texto) > TAMANHO_MAXIMO_BLOCO:
        return None
    # O mesmo botão várias vezes ("Se candidatar") = vários cards.
    # Só linhas curtas contam: "Candidatos devem ter..." em parágrafo não é botão.
    botoes = sum(
        1
        for linha in bloco
        if len(linha.strip()) <= TAMANHO_MAXIMO_LINHA_DE_FIM
        and re.match(r"^\s*(se )?candidat", normalizar(linha))
    )
    if botoes >= REPETICOES_QUE_INDICAM_LISTAGEM:
        return None
    # Lista de cards ("Presencial", "Efetivo/CLT", "R$ 6.000") não é texto de vaga:
    # exige frases de verdade ocupando boa parte do bloco.
    frases = [linha for linha in texto.split("\n") if len(linha.strip()) >= 40]
    if len(frases) < 2 or sum(len(frase) for frase in frases) < 0.4 * len(texto):
        return None
    return texto[:TAMANHO_MAXIMO_BLOCO] or None


def limpar_descricao(texto: str) -> str:
    """Remove linhas que são só texto de interface (cookies, ✕, destaque...)."""

    # Entidade que sobrou no texto por codificação dupla no site ("Requisitos&nbsp;Exp…",
    # Drugovich) vira o caractere; o espaço rígido vira quebra de linha de seção.
    if "&" in texto:
        texto = html_mod.unescape(texto).replace("\xa0", " ")
    linhas = []
    for linha in texto.split("\n"):
        if LIXO_INTERFACE.match(normalizar(linha.strip())):
            continue
        linhas.append(linha)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


UF_POR_NOME_DE_ESTADO = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA",
    "ceara": "CE", "distrito federal": "DF", "espirito santo": "ES", "goias": "GO",
    "maranhao": "MA", "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE", "piaui": "PI",
    "rio de janeiro": "RJ", "rio grande do norte": "RN", "rio grande do sul": "RS",
    "rondonia": "RO", "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP",
    "sergipe": "SE", "tocantins": "TO",
}  # fmt: skip

_CIDADE_UF_PARENTESES = re.compile(
    r"(?P<cidade>[^()]{2,60}?)\s*\((?P<uf>AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|"
    r"RJ|RN|RS|RO|RR|SC|SP|SE|TO)\)"
)

# Consultoria de RH que esconde o cliente: a vaga sai como "confidential" (combinado
# com o usuário em 05/10/2026). Host -> marca, para recusar a consultoria como empresa.
CONSULTORIAS_DE_RH = {
    "michaelpage.com.br": "michael page",
    "pageexecutive.com": "page executive",
    "roberthalf.com": "robert half",
    "roberthalf.com.br": "robert half",
    "hays.com.br": "hays",
    "randstad.com.br": "randstad",
    "adecco.com.br": "adecco",
    "manpower.com.br": "manpower",
    "manpowergroup.com.br": "manpower",
    "gigroup.com.br": "gi group",
    "robertwalters.com.br": "robert walters",
}
NOME_CONFIDENCIAL = "confidential"
# Texto padrão que o site põe no lugar da empresa (amanha.com.br: "Empregador", 606
# vagas na coleta de 06/10/2026) ou que diz que ela é oculta. Nunca é o nome.
_NOME_GENERICO = re.compile(
    r"^(o |a )?(empregador|empresa|anunciante|contratante|empresa contratante|empresa parceira|"
    r"cliente|nao informad[oa]|empresa nao informada|n/?a|-+)$"
)
_NOME_OCULTO = re.compile(r"^(empresa |cliente )?(confidencial|confidential|sigilos[ao])$")


def _nome_generico_ou_oculto(nome: object) -> bool:
    if not isinstance(nome, str):
        return False
    limpo = " ".join(normalizar(nome).split()).strip(" .")
    return bool(_NOME_GENERICO.match(limpo) or _NOME_OCULTO.match(limpo))


# Nome do site igual ao domínio e com palavra de portal: o site é um portal de vagas
# ("Empregos Pernambuco" em empregospernambuco.com.br, 2.073 vagas).
_PALAVRA_DE_PORTAL = re.compile(r"\b(vagas?|empreg\w*|jobs?|carreiras?)\b|^mais ?vagas")
_EMPRESA_CONFIDENCIAL = re.compile(
    r"\b(empresa|cliente|contratante)\s+(confidencial|sigilos[ao])\b", re.IGNORECASE
)


def consultoria_de_rh(url: str) -> str | None:
    """Marca da consultoria de RH dona do endereço, ou None."""

    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    for dominio, marca in CONSULTORIAS_DE_RH.items():
        if host == dominio or host.endswith("." + dominio):
            return marca
    return None


_SINAIS_DE_CODIGO = re.compile(r"[{}<\\$`=^]|window\.|function\b|=>|\(\?:")


def validar_endereco(endereco: str) -> str | None:
    if _SINAIS_DE_CODIGO.search(endereco):
        # Ex.: "window.location.href,page:`" e "/^(?:about" lidos do JavaScript da página.
        return "parece trecho de código"
    if len(endereco) > 100:
        return "mais de 100 caracteres"
    if endereco[:1].islower():
        return "começa no meio de palavra"
    # Abreviação ("Av. Paulista", "R. das Flores", "Jd. América") não é fim de frase.
    if re.search(r"\w{4,}[.!?]\s+\w", endereco) or len(endereco.split()) > 14:
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


def _cidade_e_uf(endereco: dict[str, Any]) -> str | None:
    """Cidade e estado do endereço; macrorregião ("Nordeste") não entra como estado."""

    regiao = endereco.get("addressRegion")
    if isinstance(regiao, str) and normalizar(regiao).strip() in REGIOES_DO_BRASIL:
        regiao = None
    partes = [endereco.get("addressLocality"), regiao]
    return ", ".join(str(p) for p in partes if p) or None


def _endereco_jsonld(inv: InventarioPagina) -> str | None:
    endereco = _jsonld(inv, "jobLocation", "address")
    if isinstance(endereco, dict):
        return _cidade_e_uf(endereco)
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
    return _expiracao(_jsonld(inv, "validThrough"), _jsonld(inv, "datePosted"), plataforma)


def _expiracao(
    validade: Any, publicada: Any, plataforma: str | None
) -> Leitura | Indefinido | None:
    """Data de validade da fonte; recusa a calculada pela plataforma (Quickin +90)."""

    if not validade:
        return None
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
    postings: list[dict[str, Any]],
    *,
    documento: dict[str, Any],
    titulo: str,
    varias_vagas: bool = False,
) -> list[dict[str, Any]]:
    """Só o JobPosting desta vaga.

    Numa página com uma vaga e um JobPosting, ele é dela. Nos outros casos,
    precisa casar com o anúncio por identificador, depois URL, depois título;
    se nada casar, ou se mais de um casar, nenhum é usado.
    """

    if not postings:
        return []
    if len(postings) == 1 and not varias_vagas:
        return postings

    def identificador(item: dict[str, Any]) -> str:
        valor = item.get("identifier")
        if isinstance(valor, dict):
            valor = valor.get("value") or valor.get("@id")
        return str(valor or "").strip().casefold()

    def url(item: dict[str, Any]) -> str:
        return str(item.get("url") or "").strip().casefold().rstrip("/")

    def nome(item: dict[str, Any]) -> str:
        return str(item.get("title") or "").strip().casefold()

    for chave, alvo in (
        (identificador, identificador(documento)),
        (url, url(documento)),
        (nome, (documento.get("title") or titulo).strip().casefold()),
    ):
        if not alvo:
            continue
        iguais = [item for item in postings if chave(item) == alvo]
        if len(iguais) == 1:
            return iguais
        if len(iguais) > 1:
            continue
    return []


def _nome_empresa(bruto: str) -> str:
    """Separa a cidade grudada no nome: "TAESA (João Pessoa/PB)" -> "TAESA".

    Também tira o slogan: "Assaí Atacadista - O atacadista com 50 anos de tradição!
    #VemserAssaí" -> "Assaí Atacadista". Só corta quando o resto tem cara de frase
    (!, ?, # ou 6+ palavras); "RD Saúde - Farmácias" fica como está.
    """

    nome = re.sub(r"\s*\((?:[^()]*/[A-Z]{2}|[^()]*\b[A-Z]{2})\)\s*$", "", bruto).strip()
    partes = re.split(r"\s+[-–|:]\s+", nome, maxsplit=1)
    if len(partes) == 2 and (re.search(r"[!?#]", partes[1]) or len(partes[1].split()) >= 6):
        return partes[0].strip()
    return nome


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
    postings = _posting_da_vaga(
        inv.json_ld, documento=documento, titulo=titulo, varias_vagas=varias_vagas_na_pagina
    )
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
    nomes_do_site = (
        inv.meta.get("og:site_name"),
        _organizacao(documento).get("name"),
        _jsonld(inv, "hiringOrganization", "name"),
    )
    agregador = e_agregador(url_vaga) or any(
        isinstance(nome, str)
        and nome_e_do_site(nome, url_vaga)
        and _PALAVRA_DE_PORTAL.search(normalizar(nome))
        for nome in nomes_do_site
    )
    site_de_terceiros = agregador or eh_plataforma or e_site_de_terceiros(url_vaga)
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
    bloco = bloco_da_vaga(inv.texto_corpo, titulo)
    # A descrição do documento costuma vir da meta description (resumo cortado em
    # "...Saiba mais"). Se o corpo tem a vaga inteira, ele vale mais — e também é
    # onde estão local, modalidade e salário escritos.
    doc_e_resumo = (
        bool(descricao_doc)
        and parece_resumo(descricao_doc)
        and bool(bloco)
        and len(bloco) > 1.5 * len(descricao_doc)
    )
    texto_vaga = (
        (p["description"].valor if "description" in p else None)
        or (bloco if doc_e_resumo else descricao_doc)
        or inv.texto_corpo
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
    def _nomes_brutos_da_empresa() -> list[object]:
        candidato_plataforma = p.get("company.name")
        return [
            candidato_plataforma.valor if candidato_plataforma else None,
            _jsonld(inv, "hiringOrganization", "name"),
            _organizacao(documento).get("name"),
            inv.meta.get("microdata:hiringOrganization"),
        ]

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
            (
                "microdata.hiringOrganization.name",
                lambda: inv.meta.get("microdata:hiringOrganization"),
            ),
            (
                "texto.empresa_confidencial",
                lambda: (
                    NOME_CONFIDENCIAL if _EMPRESA_CONFIDENCIAL.search(texto_vaga or "") else None
                ),
            ),
            (
                "cabecalho.rotulo_empresa",
                lambda: (
                    (r := inv.rotulo(r"^empresa$", r"^empresa contratante", r"^contratante$"))
                    and r[1]
                ),
            ),
            # Vem antes do nome do site: amanha.com.br põe "Empregador" na vaga e
            # "Grupo AMANHÃ" (o jornal) no site; a empresa é oculta, não o jornal.
            # A fonte disse que a empresa é oculta ou pôs um texto padrão no lugar dela
            # ("Empresa confidencial", "Empregador", "Não informado").
            (
                "fonte.empresa_oculta",
                lambda: (
                    NOME_CONFIDENCIAL
                    if any(_nome_generico_ou_oculto(n) for n in _nomes_brutos_da_empresa())
                    else None
                ),
            ),
            (
                "meta.og:site_name",
                # Em agregador, o nome do site nunca é a empresa que contrata.
                lambda: None if eh_plataforma or agregador else inv.meta.get("og:site_name"),
            ),
            (
                "consultoria.cliente_oculto",
                lambda: NOME_CONFIDENCIAL if consultoria_de_rh(url_vaga) else None,
            ),
            # Agregador que não diz quem contrata: a empresa fica oculta.
            ("agregador.cliente_oculto", lambda: NOME_CONFIDENCIAL if agregador else None),
        ],
        validar=lambda v: (
            "nome de conta/página de carreiras"
            if re.search(r"^(nova pagina|abler|demo\d*|teste)$", normalizar(str(v)).strip())
            else "texto padrão ou empresa oculta, não é o nome"
            if str(v) != NOME_CONFIDENCIAL and _nome_generico_ou_oculto(v)
            else "nome da consultoria, não do cliente"
            if (marca := consultoria_de_rh(url_vaga)) and marca in normalizar(str(v))
            else "nome do próprio site (agregador/plataforma), não da empresa"
            if site_de_terceiros and nome_e_do_site(str(v), url_vaga)
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
                # O rodapé é do dono do site: em portal/agregador o CNPJ é do portal
                # (Folha, turismoemfoco na auditoria de 06/10/2026), não da empresa.
                lambda: None if site_de_terceiros else interpretar_cnpj(inv.texto_rodape, "rodape"),
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
            ("documento.description", None if doc_e_resumo else descricao_doc),
            ("corpo.bloco_da_vaga", bloco),
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
            # Último recurso: o local escrito na própria descrição da vaga e,
            # depois, a cidade no título ("Vendedor em Recife").
            ("descricao.local", lambda: interpretar_localizacao(texto_vaga or "", "descricao")),
            ("titulo.local", lambda: cidade_do_titulo(titulo, "titulo")),
        ],
        validar=lambda v: validar_endereco(limpar_texto(str(v)) or ""),
    )
    # "Campinas , São Paulo" (eu.dev.br) -> "Campinas, SP": estado por extenso vira UF.
    if endereco and (com_estado := re.fullmatch(r"(.+?)\s*,\s*([^,]+)", str(endereco).strip())):
        uf = UF_POR_NOME_DE_ESTADO.get(normalizar(com_estado[2]).strip())
        if uf:
            endereco = f"{com_estado[1].strip()}, {uf}"
    # "São Paulo (SP)" (Jobijoba) -> "São Paulo, SP".
    if endereco and (uf_entre_parenteses := _CIDADE_UF_PARENTESES.fullmatch(str(endereco).strip())):
        endereco = f"{uf_entre_parenteses['cidade'].strip()}, {uf_entre_parenteses['uf']}"
    if endereco and not re.search(r",\s*[A-Z]{2}$", str(endereco)):
        # Só a cidade ("Recife"): completa com a UF se o texto ou o título a trazem.
        for leitura in (
            interpretar_localizacao(texto_vaga or "", "descricao"),
            cidade_do_titulo(titulo, "titulo"),
        ):
            valor = str(leitura.valor) if leitura else ""
            if valor and normalizar(valor).startswith(normalizar(str(endereco)) + ","):
                endereco = valor
                break
        else:
            # Senão, a UF vem da lista de municípios do IBGE, se o nome não deixa dúvida.
            uf = uf_da_cidade(str(endereco))
            if uf:
                endereco = f"{str(endereco).strip()}, {uf}"
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
                # Rodapé de portal/agregador é o endereço do portal, não o local da vaga
                # (CEP 58037-005 do turismoemfoco). No site da empresa, vale.
                lambda: None if site_de_terceiros else interpretar_cep(inv.texto_rodape, "rodape"),
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
            # Fontes JSON (Workday, CKAN, Querido Diário...) trazem a data no documento.
            (
                "documento.validThrough",
                lambda: _expiracao(
                    documento.get("validThrough"), documento.get("datePosted"), plataforma
                ),
            ),
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
        return _cidade_e_uf(endereco)
    return endereco if isinstance(endereco, str) else None


def job_id_da_query(url: str) -> str | None:
    return (parse_qs(urlsplit(url).query).get("job_id") or [None])[0]
