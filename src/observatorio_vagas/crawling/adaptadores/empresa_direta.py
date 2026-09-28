"""Adaptador para páginas próprias de carreira de empresas.

Não usa plataformas de ATS: reaproveita somente a descoberta HTML/JSON-LD
genérica e mantém a autorização da fonte sob responsabilidade do catálogo.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urldefrag, urlencode, urlsplit, urlunsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.crawling.descoberta import LinkCandidatoVaga, _normalizar_texto

DOMINIOS_ARTIGOS_VAGAS = frozenset(
    {
        "dataprivacybr.org",
        "internetlab.org.br",
        "news.fiquemsabendo.com.br",
        "ok.org.br",
        "www.dataprivacybr.org",
    }
)


DOMINIO_LEVER = "jobs.lever.co"
DOMINIO_CODAM = "vagas.codam.com.br"
DOMINIO_ARI = "trabalheconosco.aridesa.com.br"
DOMINIOS_ESTRELA = frozenset({"rh.estreladolar.com.br", "www.rh.estreladolar.com.br"})
DOMINIO_API_SOLIDES = "apigw.solides.com.br"
DOMINIO_SENIOR = "platform.senior.com.br"
DOMINIO_ABLER = "ats.abler.com.br"
DOMINIO_RANDSTAD = "www.randstad.com.br"
DOMINIO_API_ABLER = "hulk-smash.abler.com.br"
DOMINIO_SMARTRECRUITERS = "jobs.smartrecruiters.com"
DOMINIO_CARREIRAS_SMARTRECRUITERS = "careers.smartrecruiters.com"
DOMINIO_API_SMARTRECRUITERS = "api.smartrecruiters.com"
CAMINHO_API_ABLER = "/api/company/v1/careers_pages"
CAMINHO_API_SMARTRECRUITERS = "/v1/companies"
SUFIXO_PORTAL_SOLIDES = ".vagas.solides.com.br"
CAMINHO_API_SOLIDES = "/jobs/v3/home/vacancy"
CAMINHO_LISTAGEM_SENIOR = (
    "/t/senior.com.br/bridge/1.0/rest/hcm/recruitment/queries/searchPublicVacancies"
)
CAMINHO_DETALHE_SENIOR = (
    "/t/senior.com.br/bridge/1.0/rest/hcm/recruitment/queries/publishedVacancyDetails"
)
PADRAO_ID_VAGA_LEVER = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AdaptadorEmpresaDireta(AdaptadorGenericoHTML):
    """Lê páginas públicas mantidas no domínio da própria empresa."""

    nome: str = "empresa_direta_html"

    def descobrir(self, resposta: Response) -> tuple[LinkCandidatoVaga, ...]:
        """Inclui artigos cujo cartão anuncia uma vaga explicitamente."""

        if isinstance(resposta, TextResponse):
            dominio = (urlsplit(resposta.url).hostname or "").casefold()
            if dominio == DOMINIO_API_ABLER:
                return _descobrir_resultado_abler(resposta)
            if dominio == DOMINIO_API_SMARTRECRUITERS:
                return _descobrir_resultado_smartrecruiters(resposta)
            if dominio.endswith(SUFIXO_PORTAL_SOLIDES):
                return _descobrir_listagem_solides(resposta)
            if (
                dominio == DOMINIO_API_SOLIDES
                and urlsplit(resposta.url).path == CAMINHO_API_SOLIDES
            ):
                return _descobrir_resultado_solides(resposta)
            if dominio == DOMINIO_SENIOR:
                caminho = urlsplit(resposta.url).path
                if caminho == CAMINHO_LISTAGEM_SENIOR:
                    return _descobrir_resultado_senior(resposta)
                if "tenant=" in urlsplit(resposta.url).query:
                    return _descobrir_listagem_senior(resposta)

        genericos = AdaptadorGenericoHTML.descobrir(self, resposta)
        encontrados = {item.url: item for item in genericos}
        if not isinstance(resposta, TextResponse):
            return tuple(encontrados.values())
        dominio = (urlsplit(resposta.url).hostname or "").casefold()
        if dominio == DOMINIO_LEVER:
            return _descobrir_vagas_lever(resposta, encontrados)
        if dominio in {DOMINIO_SMARTRECRUITERS, DOMINIO_CARREIRAS_SMARTRECRUITERS}:
            return _descobrir_vagas_smartrecruiters(resposta, encontrados)
        if dominio == DOMINIO_CODAM:
            return _descobrir_vagas_codam(resposta, encontrados)
        if dominio == DOMINIO_RANDSTAD:
            return _descobrir_vagas_randstad(resposta, encontrados)
        if dominio == DOMINIO_ARI:
            return _descobrir_vagas_ari(resposta, encontrados)
        if dominio == DOMINIO_ABLER:
            return _descobrir_vagas_abler(resposta, encontrados)
        if dominio in DOMINIOS_ESTRELA:
            return _descobrir_vagas_estrela(resposta, encontrados)
        if dominio not in DOMINIOS_ARTIGOS_VAGAS:
            return tuple(encontrados.values())
        encontrados = {
            url: item
            for url, item in encontrados.items()
            if (urlsplit(url).hostname or "").casefold() == dominio
            and "inscricoes encerradas" not in _normalizar_texto(item.texto)
        }

        for link in resposta.css("a[href]"):
            url = urldefrag(resposta.urljoin(link.attrib["href"]))[0]
            destino = urlsplit(url)
            if (destino.hostname or "").casefold() != dominio or url == resposta.url:
                continue
            contexto = _contexto_curto_do_link(link)
            normalizado = _normalizar_texto(contexto)
            if not any(sinal in normalizado for sinal in ("vaga", "contratacao", "selecao")):
                continue
            if any(sinal in normalizado for sinal in ("concurso publico", "inscricoes encerradas")):
                continue
            encontrados.setdefault(
                url,
                LinkCandidatoVaga(
                    url=url,
                    texto=" ".join(contexto.split())[:300],
                    evidencias=("vaga_no_cartao_institucional",),
                ),
            )
        return tuple(encontrados.values())


def _descobrir_vagas_codam(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Segue apenas os botões de candidatura que abrem detalhes no site da Codam."""

    if urlsplit(resposta.url).path.strip("/"):
        return tuple(encontrados.values())
    for link in resposta.css("a[href]"):
        texto = " ".join((link.xpath("string(.)").get() or "").split())
        if _normalizar_texto(texto) != "candidatar-se":
            continue
        url = urldefrag(resposta.urljoin(link.attrib["href"]))[0]
        destino = urlsplit(url)
        segmentos = [parte for parte in destino.path.split("/") if parte]
        if (destino.hostname or "").casefold() != DOMINIO_CODAM or len(segmentos) != 1:
            continue
        encontrados.setdefault(
            url,
            LinkCandidatoVaga(url=url, texto=texto, evidencias=("botao_vaga_codam",)),
        )
    return tuple(encontrados.values())


def _descobrir_vagas_randstad(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Classifica as páginas públicas da lista Randstad como navegação.

    A própria listagem publica links literais no formato ``/vagas/page-N/``.
    Eles não são detalhes de vagas e precisam receber uma evidência própria
    para o spider continuar até o limite configurado para a fonte.
    """

    caminho_atual = urlsplit(resposta.url).path.rstrip("/")
    if not (caminho_atual == "/vagas" or re.fullmatch(r"/vagas/page-\d+", caminho_atual)):
        return tuple(encontrados.values())

    for url, candidato in tuple(encontrados.items()):
        destino = urlsplit(url)
        caminho = destino.path.rstrip("/")
        if (
            (destino.hostname or "").casefold() == DOMINIO_RANDSTAD
            and re.fullmatch(r"/vagas/page-\d+", caminho)
        ):
            encontrados[url] = LinkCandidatoVaga(
                url=url,
                texto=candidato.texto or "Próxima página de vagas Randstad",
                evidencias=("paginacao_randstad",),
            )

    return tuple(encontrados.values())


def _descobrir_vagas_ari(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """O portal Ari usa IDs numéricos em vez de `/vagas/...`."""

    if urlsplit(resposta.url).path.strip("/"):
        return tuple(encontrados.values())
    for link in resposta.css("a[href]"):
        url = urldefrag(resposta.urljoin(link.attrib["href"]))[0]
        partes = urlsplit(url)
        if partes.hostname != DOMINIO_ARI or not re.fullmatch(r"/\d+/?", partes.path):
            continue
        if _normalizar_texto(link.xpath("string(.)").get() or "") == "ver detalhes":
            encontrados.setdefault(
                url,
                LinkCandidatoVaga(url=url, texto="Ver detalhes", evidencias=("rota_vaga_ari",)),
            )
    return tuple(encontrados.values())


def _descobrir_vagas_abler(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Lê a listagem pública da Abler sem depender da renderização JavaScript."""

    partes_origem = [parte for parte in urlsplit(resposta.url).path.split("/") if parte]
    if len(partes_origem) != 2 or partes_origem[0] != "jobs":
        return tuple(encontrados.values())
    # O adaptador genérico pode confundir ``slug`` com um sinal de candidatura
    # e aceitar outro locatário no mesmo domínio compartilhado. Nesta
    # plataforma, somente a regra abaixo decide quais detalhes são válidos.
    encontrados = {}
    locatario = partes_origem[1].casefold()

    for link in resposta.css("a[href*='slug=']"):
        url = urldefrag(resposta.urljoin(link.attrib["href"]))[0]
        destino = urlsplit(url)
        partes_destino = [parte for parte in destino.path.split("/") if parte]
        slug = (parse_qs(destino.query).get("slug") or [""])[0].strip()
        if (
            (destino.hostname or "").casefold() != DOMINIO_ABLER
            or len(partes_destino) != 2
            or partes_destino[0] != "jobs"
            or partes_destino[1].casefold() != locatario
            or not slug
        ):
            continue
        texto = " ".join((link.xpath("string(.)").get() or "").split())
        candidato = LinkCandidatoVaga(
            url=url,
            texto=texto or "Detalhe de vaga Abler",
            evidencias=("detalhe_vaga_abler",),
        )
        encontrados[url] = candidato
    if encontrados:
        return tuple(encontrados.values())
    # As páginas atuais da Abler enviam somente a estrutura da interface no
    # HTML. A própria página consulta esta API pública para listar as vagas.
    # Usar 100 itens reduz chamadas sem passar da paginação documentada pela
    # resposta (``meta.next``).
    url_api = (
        f"https://{DOMINIO_API_ABLER}{CAMINHO_API_ABLER}/{locatario}/vacancies"
        "?per_page=100&page=1"
    )
    return (
        LinkCandidatoVaga(
            url=url_api,
            texto="Listagem pública de vagas Abler",
            evidencias=("listagem_abler_api",),
        ),
    )


def _descobrir_resultado_abler(resposta: TextResponse) -> tuple[LinkCandidatoVaga, ...]:
    """Avança a paginação indicada pela API pública da Abler."""

    try:
        dados = json.loads(resposta.text)
    except (TypeError, ValueError):
        return ()
    endereco = urlsplit(resposta.url)
    partes = [parte for parte in endereco.path.split("/") if parte]
    # /api/company/v1/careers_pages/{subdominio}/vacancies
    if (
        len(partes) != 6
        or partes[:4] != ["api", "company", "v1", "careers_pages"]
        or partes[-1] != "vacancies"
    ):
        return ()
    subdominio = partes[4].strip()
    if not subdominio:
        return ()
    meta = dados.get("meta") if isinstance(dados, dict) else None
    proxima = meta.get("next") if isinstance(meta, dict) else None
    if not isinstance(proxima, int) or proxima < 1:
        return ()
    parametros = dict(parse_qs(endereco.query))
    parametros["page"] = [str(proxima)]
    parametros.setdefault("per_page", ["100"])
    query = urlencode({chave: valores[-1] for chave, valores in parametros.items()})
    url = urlunsplit((endereco.scheme, endereco.netloc, endereco.path, query, ""))
    return (
        LinkCandidatoVaga(
            url=url,
            texto=f"Página {proxima} da listagem Abler ({subdominio})",
            evidencias=("paginacao_abler_api",),
        ),
    )


def _descobrir_vagas_estrela(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Segue os detalhes cidade/cargo, mas ignora banco de talentos."""

    if urlsplit(resposta.url).path.strip("/"):
        return tuple(encontrados.values())
    encontrados = {
        url: item for url, item in encontrados.items()
        if "banco" not in urlsplit(url).path.casefold()
    }
    for link in resposta.css("a[href]"):
        if _normalizar_texto(link.xpath("string(.)").get() or "") != "ver detalhes":
            continue
        url = urldefrag(resposta.urljoin(link.attrib["href"]))[0]
        partes = urlsplit(url)
        segmentos = [segmento for segmento in partes.path.split("/") if segmento]
        if (
            partes.hostname not in DOMINIOS_ESTRELA
            or len(segmentos) != 2
            or "banco" in segmentos[1].casefold()
        ):
            continue
        encontrados.setdefault(
            url,
            LinkCandidatoVaga(url=url, texto="Ver detalhes", evidencias=("rota_vaga_estrela",)),
        )
    return tuple(encontrados.values())


def _descobrir_vagas_lever(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Reconhece a rota pública estável de detalhes do Lever.

    A listagem usa ``/<empresa>/<uuid>`` e alguns cargos não possuem as
    palavras "vaga" ou "job" no título ou na URL. Restringir a descoberta ao
    mesmo quadro da empresa e a um UUID evita seguir links institucionais.
    """

    segmentos_origem = [parte for parte in urlsplit(resposta.url).path.split("/") if parte]
    if len(segmentos_origem) != 1:
        return tuple(encontrados.values())

    empresa = segmentos_origem[0].casefold()
    for link in resposta.css("a[href]"):
        url = urldefrag(resposta.urljoin(link.attrib["href"]))[0]
        destino = urlsplit(url)
        segmentos = [parte for parte in destino.path.split("/") if parte]
        if (
            (destino.hostname or "").casefold() != DOMINIO_LEVER
            or len(segmentos) != 2
            or segmentos[0].casefold() != empresa
            or not PADRAO_ID_VAGA_LEVER.fullmatch(segmentos[1])
        ):
            continue

        texto = " ".join((link.xpath("string(.)").get() or "").split())
        candidato = LinkCandidatoVaga(
            url=url,
            texto=texto,
            evidencias=("padrao_url_lever",),
        )
        anterior = encontrados.get(url)
        if anterior is None or len(candidato.texto) > len(anterior.texto):
            encontrados[url] = candidato

    return tuple(encontrados.values())


def _descobrir_vagas_smartrecruiters(
    resposta: TextResponse,
    encontrados: dict[str, LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Agenda a API pública do quadro SmartRecruiters da própria empresa.

    A página HTML é renderizada no cliente e pode omitir cards e paginação.
    A API pública documentada pelo fornecedor lista somente anúncios ativos do
    identificador presente em ``jobs.smartrecruiters.com/<empresa>``.
    """

    segmentos = [parte for parte in urlsplit(resposta.url).path.split("/") if parte]
    if len(segmentos) != 1:
        return tuple(encontrados.values())
    empresa = segmentos[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", empresa):
        return tuple(encontrados.values())
    url_api = (
        f"https://{DOMINIO_API_SMARTRECRUITERS}{CAMINHO_API_SMARTRECRUITERS}"
        f"/{empresa}/postings?limit=100&offset=0"
    )
    return (
        LinkCandidatoVaga(
            url=url_api,
            texto=f"Listagem pública SmartRecruiters ({empresa})",
            evidencias=("listagem_smartrecruiters_api",),
        ),
    )


def _descobrir_resultado_smartrecruiters(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Converte a lista pública em páginas de detalhe e avança a paginação."""

    try:
        dados = json.loads(resposta.text)
    except (TypeError, ValueError):
        return ()
    if not isinstance(dados, dict):
        return ()
    endereco = urlsplit(resposta.url)
    segmentos = [parte for parte in endereco.path.split("/") if parte]
    # /v1/companies/{empresa}/postings
    if len(segmentos) != 4 or segmentos[:3] != ["v1", "companies", segmentos[2]]:
        return ()
    if segmentos[-1] != "postings":
        return ()
    empresa = segmentos[2]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", empresa):
        return ()

    encontrados: list[LinkCandidatoVaga] = []
    conteudo = dados.get("content")
    if isinstance(conteudo, list):
        for vaga in conteudo:
            if not isinstance(vaga, dict):
                continue
            identificador = vaga.get("id") or vaga.get("uuid")
            if not isinstance(identificador, (str, int)) or isinstance(identificador, bool):
                continue
            titulo = vaga.get("name") if isinstance(vaga.get("name"), str) else "Vaga"
            encontrados.append(
                LinkCandidatoVaga(
                    url=f"https://{DOMINIO_SMARTRECRUITERS}/{empresa}/{identificador}",
                    texto=" ".join(titulo.split())[:300],
                    evidencias=("detalhe_smartrecruiters_api",),
                )
            )

    limite = dados.get("limit")
    deslocamento = dados.get("offset")
    total = dados.get("totalFound")
    if (
        isinstance(limite, int)
        and isinstance(deslocamento, int)
        and isinstance(total, int)
        and limite > 0
        and deslocamento >= 0
        and total > deslocamento + limite
    ):
        parametros = parse_qs(endereco.query)
        parametros["limit"] = [str(limite)]
        parametros["offset"] = [str(deslocamento + limite)]
        query = urlencode({chave: valores[-1] for chave, valores in parametros.items()})
        encontrados.append(
            LinkCandidatoVaga(
                url=urlunsplit((endereco.scheme, endereco.netloc, endereco.path, query, "")),
                texto=f"Próxima página SmartRecruiters ({empresa})",
                evidencias=("paginacao_smartrecruiters_api",),
            )
        )
    return tuple(encontrados)


def _descobrir_listagem_solides(resposta: TextResponse) -> tuple[LinkCandidatoVaga, ...]:
    """Agenda somente a API pública que abastece um portal Sólides."""

    dominio = (urlsplit(resposta.url).hostname or "").casefold()
    if not dominio.endswith(SUFIXO_PORTAL_SOLIDES):
        return ()
    slug = dominio.removesuffix(SUFIXO_PORTAL_SOLIDES)
    if not slug or "." in slug:
        return ()
    parametros = urlencode({"take": 12, "slug": slug, "page": 1})
    return (
        LinkCandidatoVaga(
            url=f"https://{DOMINIO_API_SOLIDES}{CAMINHO_API_SOLIDES}?{parametros}",
            texto="Listagem pública de vagas Sólides",
            evidencias=("listagem_solides_api",),
        ),
    )


def _descobrir_resultado_solides(resposta: TextResponse) -> tuple[LinkCandidatoVaga, ...]:
    """Converte IDs retornados pela API em detalhes no portal da empresa."""

    try:
        dados = json.loads(resposta.text)
    except (TypeError, ValueError):
        return ()
    raiz = dados.get("data") if isinstance(dados, dict) else None
    if not isinstance(raiz, dict):
        return ()
    parametros = parse_qs(urlsplit(resposta.url).query)
    slug = (parametros.get("slug") or [""])[0].strip()
    if not slug or not re.fullmatch(r"[a-z0-9-]+", slug):
        return ()
    dominio_portal = f"{slug}{SUFIXO_PORTAL_SOLIDES}"
    encontrados: list[LinkCandidatoVaga] = []
    for vaga in raiz.get("data", ()):
        if not isinstance(vaga, dict) or not isinstance(vaga.get("id"), int):
            continue
        titulo = vaga.get("title") if isinstance(vaga.get("title"), str) else "Vaga Sólides"
        encontrados.append(
            LinkCandidatoVaga(
                url=f"https://{dominio_portal}/vaga/{vaga['id']}",
                texto=" ".join(titulo.split())[:300],
                evidencias=("detalhe_solides_api",),
            )
        )
    pagina = raiz.get("currentPage")
    total = raiz.get("totalPages")
    if isinstance(pagina, int) and isinstance(total, int) and pagina < total:
        proxima = urlencode({"take": 12, "slug": slug, "page": pagina + 1})
        encontrados.append(
            LinkCandidatoVaga(
                url=f"https://{DOMINIO_API_SOLIDES}{CAMINHO_API_SOLIDES}?{proxima}",
                texto="Próxima página da listagem Sólides",
                evidencias=("listagem_solides_api", "paginacao_solides_api"),
            )
        )
    return tuple(encontrados)


def _descobrir_listagem_senior(resposta: TextResponse) -> tuple[LinkCandidatoVaga, ...]:
    """Usa a consulta pública do tenant Senior sem executar JavaScript."""

    consulta = parse_qs(urlsplit(resposta.url).query)
    tenant = (consulta.get("tenant") or [""])[0].strip()
    tenant_domain = (consulta.get("tenantdomain") or [""])[0].strip()
    if not re.fullmatch(r"[a-z0-9-]+", tenant, re.IGNORECASE):
        return ()
    if not re.fullmatch(r"[a-z0-9.-]+", tenant_domain, re.IGNORECASE):
        return ()
    parametros = urlencode(
        {
            "observatorio_senior_listagem": "1",
            "tenant": tenant,
            "tenantdomain": tenant_domain,
            "page": 0,
        }
    )
    return (
        LinkCandidatoVaga(
            url=f"https://{DOMINIO_SENIOR}{CAMINHO_LISTAGEM_SENIOR}?{parametros}",
            texto="Listagem pública de vagas Senior",
            evidencias=("listagem_senior_api",),
        ),
    )


def _descobrir_resultado_senior(resposta: TextResponse) -> tuple[LinkCandidatoVaga, ...]:
    """Agenda detalhes públicos Senior a partir do retorno da listagem."""

    try:
        dados = json.loads(resposta.text)
    except (TypeError, ValueError):
        return ()
    if not isinstance(dados, dict):
        return ()
    parametros = parse_qs(urlsplit(resposta.url).query)
    tenant = (parametros.get("tenant") or [""])[0]
    tenant_domain = (parametros.get("tenantdomain") or [""])[0]
    encontrados: list[LinkCandidatoVaga] = []
    for vaga in dados.get("vacancies", ()):
        if not isinstance(vaga, dict) or not isinstance(vaga.get("id"), (str, int)):
            continue
        titulo = vaga.get("title") if isinstance(vaga.get("title"), str) else "Vaga Senior"
        consulta = urlencode(
            {
                "observatorio_senior_detalhe": "1",
                "tenant": tenant,
                "tenantdomain": tenant_domain,
                "vacancy_id": str(vaga["id"]),
            }
        )
        encontrados.append(
            LinkCandidatoVaga(
                url=f"https://{DOMINIO_SENIOR}{CAMINHO_DETALHE_SENIOR}?{consulta}",
                texto=" ".join(titulo.split())[:300],
                evidencias=("detalhe_senior_api",),
            )
        )
    return tuple(encontrados)


def _contexto_curto_do_link(link) -> str:
    """Obtém o menor cartão que contextualiza o link, sem usar a página toda."""

    proprio = link.xpath("string(.)").get() or ""
    for ancestral in link.xpath(
        "ancestor::*[self::article or self::li or self::section or self::div]"
    ):
        texto = " ".join(ancestral.xpath("string(.)").get().split())
        if len(proprio) <= len(texto) <= 1200:
            return texto
    return proprio
