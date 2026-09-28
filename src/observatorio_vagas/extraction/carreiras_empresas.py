"""Extração conservadora de páginas próprias com estrutura incomum."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from parsel import Selector


@dataclass(frozen=True, slots=True)
class ResultadoCarreirasEmpresas:
    dominio_reconhecido: bool
    vagas: tuple[dict[str, Any], ...]


def _texto(valor: str | None) -> str:
    return " ".join((valor or "").split())


def extrair_vagas_carreiras_empresas(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoCarreirasEmpresas:
    """Separa cards da ProCatálogo e lê detalhes numéricos do portal Ari."""

    partes = urlsplit(url)
    dominio = (partes.hostname or "").casefold()
    if dominio == "procatalogo.com.br":
        if partes.path.rstrip("/") != "/trabalhe-conosco":
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_procatalogo(corpo, url, codificacao))
    if dominio == "trabalheconosco.aridesa.com.br":
        if not re.fullmatch(r"/\d+/?", partes.path):
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_ari(corpo, url, codificacao))
    if dominio in {"rh.estreladolar.com.br", "www.rh.estreladolar.com.br"}:
        if "banco" in partes.path.casefold():
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_estrela(corpo, url, codificacao))
    if dominio in {"lullyhair.com.br", "www.lullyhair.com.br"}:
        if not partes.path.startswith("/vagas/"):
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_lully(corpo, url, codificacao))
    if dominio in {"viatectelecom.com.br", "www.viatectelecom.com.br"}:
        if partes.path.rstrip("/") != "/trabalheconosco":
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_viatec(corpo, url, codificacao))
    if dominio == "vagas.gtgrupo.com.br":
        if partes.path.rstrip("/") not in {"", "/index.php"}:
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_gt_grupo(corpo, url, codificacao))
    if dominio in {"marvi.com.br", "www.marvi.com.br"}:
        if partes.path.rstrip("/") != "/carreiras":
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_marvi(corpo, url, codificacao))
    if dominio in {"perigozero.com.br", "www.perigozero.com.br"}:
        if partes.path.rstrip("/") != "/carreiras":
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_perigo_zero(corpo, url, codificacao))
    if dominio == "vagas.asscont.com.br":
        return ResultadoCarreirasEmpresas(True, _extrair_asscont(corpo, url, codificacao))
    if dominio in {"setrata.com.br", "www.setrata.com.br"}:
        if partes.path.rstrip("/") != "/trabalhe-conosco":
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_setrata(corpo, url, codificacao))
    if dominio in {"marquezim.com.br", "www.marquezim.com.br"}:
        if partes.path.rstrip("/") != "/trabalhe-conosco":
            return ResultadoCarreirasEmpresas(True, ())
        return ResultadoCarreirasEmpresas(True, _extrair_marquezim(corpo, url, codificacao))
    if dominio == "platform.senior.com.br" and "observatorio_senior_detalhe=1" in partes.query:
        return ResultadoCarreirasEmpresas(True, _extrair_senior(corpo, url, codificacao))
    return ResultadoCarreirasEmpresas(False, ())


def _extrair_procatalogo(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []
    for secao in seletor.css("div.elementor-inner-column"):
        botoes = secao.css("a[href]")
        formularios = [
            botao.attrib["href"]
            for botao in botoes
            if urlsplit(botao.attrib["href"]).hostname in {"forms.gle", "docs.google.com"}
            and "candidatar" in _texto(botao.xpath("string(.)").get()).casefold()
        ]
        titulos = secao.css("h4.elementor-icon-box-title")
        if len(formularios) != 1 or len(titulos) != 1:
            continue
        titulo = _texto(titulos[0].xpath("string(.)").get())
        descricao = _texto(secao.css("p.elementor-icon-box-description").xpath("string(.)").get())
        if len(titulo) < 5 or not descricao:
            continue
        formulario = formularios[0]
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": sha256(formulario.encode()).hexdigest()[:40],
                "title": titulo,
                "description": descricao,
                "url": url,
                "_observatorio_apply_url": formulario,
                "hiringOrganization": {"@type": "Organization", "name": "ProCatálogo"},
                "jobLocationType": "TELECOMMUTE" if "remoto" in descricao.casefold() else None,
                "_observatorio_extrator": "procatalogo_cards",
            }
        )
    return tuple(vagas)


def _extrair_ari(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    titulo = _texto(seletor.css("main h1").xpath("string(.)").get())
    if not titulo:
        return ()
    conteudo = _texto(seletor.css("main").xpath("string(.)").get())
    if not re.search(r"\bStatus:\s*Aberta\b", conteudo, re.IGNORECASE):
        return ()
    secoes = []
    for cabecalho in seletor.css("main h2"):
        nome = _texto(cabecalho.xpath("string(.)").get())
        if nome not in {"Requisitos", "Responsabilidades da Vaga", "Benefícios"}:
            continue
        texto = _texto(cabecalho.xpath("string(parent::*)").get())
        if texto.startswith(nome):
            secoes.append(texto)
    descricao = "\n".join(secoes)
    if len(descricao) < 80:
        return ()
    empresa = re.search(r"\bEmpresa:\s*(.+?)\s*$", conteudo)
    localidade = re.search(r"\bLocalização:\s*(.+?)\s+Empresa:", conteudo)
    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": url,
        "_observatorio_extrator": "ari_detalhe",
    }
    if empresa:
        documento["hiringOrganization"] = {
            "@type": "Organization",
            "name": empresa.group(1).strip(),
        }
    if localidade:
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": localidade.group(1).strip(),
            },
        }
    return (documento,)


def _extrair_estrela(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    partes = [parte for parte in urlsplit(url).path.split("/") if parte]
    if len(partes) != 2:
        return ()
    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    titulo = _texto(seletor.css("main h1").xpath("string(.)").get())
    if not titulo or titulo.casefold().startswith("banco "):
        return ()
    secoes = []
    for cabecalho in seletor.css("main h2"):
        nome = _texto(cabecalho.xpath("string(.)").get())
        if nome not in {"Descrição da Vaga", "Requisitos", "Benefícios"}:
            continue
        conteudo = _texto(
            cabecalho.xpath("string(../div[contains(@class, 'prose-job')][1])").get()
        )
        if conteudo:
            secoes.append(f"{nome}: {conteudo}")
    descricao = "\n".join(secoes)
    if len(descricao) < 80:
        return ()
    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": url,
        "hiringOrganization": {"@type": "Organization", "name": "Estrela do Lar"},
        "_observatorio_extrator": "estrela_detalhe",
    }
    cidade = _texto(
        seletor.css("main i[data-lucide='map-pin']").xpath("following-sibling::text()[1]").get()
    )
    if cidade:
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressLocality": cidade},
        }
    return (documento,)


def _extrair_lully(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Extrai a vaga WordPress e preserva a candidatura no formulário da página."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    titulo = _texto(seletor.css("h1.elementor-heading-title").xpath("string(.)").get())
    formulario = seletor.css("form[name='Vagas']")
    conteudo = _texto(seletor.css(".elementor-location-single").xpath("string(.)").get())
    descricao = conteudo.split("Preencha o formulário e candidate-se", maxsplit=1)[0].strip()
    if not titulo or not formulario or len(descricao) < 80:
        return ()
    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "title": titulo,
        "description": descricao[:8000],
        "url": url,
        "_observatorio_apply_url": url,
        "hiringOrganization": {"@type": "Organization", "name": "Lully Hair"},
        "_observatorio_extrator": "lully_formulario_proprio",
    }
    localidade = re.search(r"\bLocal:\s*([^\n]+)", descricao, re.IGNORECASE)
    if localidade:
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressLocality": localidade.group(1).strip()},
        }
    return (documento,)


def _extrair_viatec(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Extrai cards Viatec sem visitar a tela autenticada de inscrição."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for botao in seletor.css("a[href*='/trabalheconosco/user/acesso/']"):
        candidatura = urljoin(url, botao.attrib.get("href", ""))
        if not re.fullmatch(r"https?://(?:www\.)?viatectelecom\.com\.br/trabalheconosco/user/acesso/\d+/?", candidatura):
            continue
        if candidatura in vistos:
            continue
        card = botao.xpath("ancestor::*[self::article or self::li or self::div][.//h4][1]")
        titulo = _texto(card.css("h4").xpath("string(.)").get())
        texto_card = _texto(card.xpath("string(.)").get())
        if not titulo or len(titulo) < 4:
            continue
        marcador_codigo = re.search(r"c[oó]digo:\s*\d+", texto_card, re.IGNORECASE)
        local = texto_card[: marcador_codigo.start()].strip() if marcador_codigo else ""
        descricao = (
            f"Vaga publicada pela Viatec Telecom. Cargo: {titulo}. "
            + (f"Local de trabalho: {local}. " if local else "")
            + "Use o link de candidatura para iniciar sua inscrição no portal da empresa."
        )
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": sha256(candidatura.encode()).hexdigest()[:40],
                "title": titulo,
                "description": descricao,
                "url": url,
                "_observatorio_apply_url": candidatura,
                "hiringOrganization": {"@type": "Organization", "name": "Viatec Telecom"},
                "_observatorio_extrator": "viatec_cards_publicos",
            }
        )
        vistos.add(candidatura)
    return tuple(vagas)


def _extrair_gt_grupo(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Lê os cards públicos do GT; todos levam ao mesmo portal de inscrição."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for card in seletor.css("#vagas > div"):
        titulo = _texto(card.css("h3").xpath("string(.)").get())
        candidatura = urljoin(
            url, card.css("a[href*='/candidato/login.php']").attrib.get("href", "")
        )
        conteudo = _texto(card.xpath("string(.)").get())
        chave = f"{titulo}|{conteudo}"
        if not titulo or not candidatura or chave in vistos:
            continue
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": sha256(chave.encode()).hexdigest()[:40],
                "title": titulo,
                "description": conteudo[:8000],
                "url": url,
                "_observatorio_apply_url": candidatura,
                "hiringOrganization": {"@type": "Organization", "name": "GT Grupo"},
                "_observatorio_extrator": "gt_grupo_cards_publicos",
            }
        )
        vistos.add(chave)
    return tuple(vagas)


def _extrair_marvi(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Associa cada acordeão de vaga Marvi ao seu formulário público próprio."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []
    for item in seletor.css(".elementor-accordion-item, .e-n-accordion-item"):
        titulo = _texto(
            item.css(".elementor-tab-title, .e-n-accordion-item-title-text, button")
            .xpath("string(.)")
            .get()
        )
        formulario = item.css("form[id]").attrib.get("id", "")
        if not titulo or not formulario or len(titulo) < 4:
            continue
        candidatura = f"{url.split('#', 1)[0]}#{formulario}"
        descricao = _texto(item.xpath("string(.)").get())
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": sha256(candidatura.encode()).hexdigest()[:40],
                "title": titulo,
                "description": (
                    f"Vaga publicada pela Marvi Alimentos: {titulo}. "
                    f"{descricao}"[:8000]
                ),
                "url": url,
                "_observatorio_apply_url": candidatura,
                "hiringOrganization": {"@type": "Organization", "name": "Marvi Alimentos"},
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "@type": "PostalAddress",
                        "addressCountry": "BR",
                        "addressRegion": "SP",
                        "addressLocality": "Ourinhos",
                    },
                },
                "_observatorio_extrator": "marvi_acordeoes_formularios",
            }
        )
    return tuple(vagas)


def _extrair_perigo_zero(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Extrai vagas estáticas; a candidatura por e-mail permanece na página própria."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for botao in seletor.css("a[href]"):
        texto_botao = _texto(botao.xpath("string(.)").get()).casefold()
        href = botao.attrib.get("href", "")
        if "candidat" not in texto_botao or not href.startswith("mailto:"):
            continue
        card = botao.xpath("ancestor::*[self::article or self::section or self::div][.//h2 or .//h3][1]")
        titulo = _texto(card.css("h2, h3").xpath("string(.)").get())
        conteudo = _texto(card.xpath("string(.)").get())
        chave = f"{titulo}|{href}"
        if not titulo or chave in vistos:
            continue
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": sha256(chave.encode()).hexdigest()[:40],
                "title": titulo,
                "description": conteudo[:8000],
                "url": url,
                "_observatorio_apply_url": url,
                "hiringOrganization": {"@type": "Organization", "name": "Perigo Zero"},
                "_observatorio_extrator": "perigo_zero_cards_email",
            }
        )
        vistos.add(chave)
    return tuple(vagas)


def _extrair_asscont(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Extrai cards Asscont; o botão abre a candidatura mantida na própria página."""

    return _extrair_cards_de_pagina(
        corpo,
        url=url,
        codificacao=codificacao,
        seletor_cards="div.modal",
        empresa="Asscont",
        extrator="asscont_cards_publicos",
    )


def _extrair_setrata(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Extrai os cards públicos Setrata, sem seguir o botão de candidatura."""

    return _extrair_cards_de_pagina(
        corpo,
        url=url,
        codificacao=codificacao,
        seletor_cards="div.job.reveal",
        empresa="Setrata",
        extrator="setrata_cards_publicos",
    )


def _extrair_marquezim(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Cria uma vaga por opção do formulário próprio da Marquezim."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    formulario = seletor.css("form#custom-form")
    if not formulario:
        return ()
    descricoes = [_texto(paragrafo.xpath("string(.)").get()) for paragrafo in seletor.css("#content p")]
    candidatura = f"{url.split('#', 1)[0]}#custom-form"
    vagas: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for opcao in formulario.css("select option[value]"):
        titulo = _texto(opcao.attrib.get("value"))
        if not titulo or titulo.casefold() in {"outros", "outras"} or titulo in vistos:
            continue
        descricao = next(
            (texto for texto in descricoes if texto.casefold().startswith(titulo.casefold())),
            "",
        )
        if not descricao:
            descricao = (
                f"Vaga publicada pela Marquezim: {titulo}. "
                "Candidate-se pelo formulário oficial da empresa."
            )
        documento: dict[str, Any] = {
            "@type": "JobPosting",
            "identifier": sha256(f"{candidatura}|{titulo}".encode()).hexdigest()[:40],
            "title": titulo,
            "description": descricao[:8000],
            "url": url,
            "_observatorio_apply_url": candidatura,
            "hiringOrganization": {"@type": "Organization", "name": "Marquezim"},
            "jobLocation": {
                "@type": "Place",
                "address": {"@type": "PostalAddress", "addressCountry": "BR"},
            },
            "_observatorio_extrator": "marquezim_formulario_vagas",
        }
        localidade = re.search(r"-\s*([^:]+)$", titulo)
        if localidade:
            documento["jobLocation"]["address"]["addressLocality"] = localidade.group(1).strip()
            documento["jobLocation"]["address"]["addressRegion"] = "SP"
        vagas.append(documento)
        vistos.add(titulo)
    return tuple(vagas)


def _extrair_cards_de_pagina(
    corpo: bytes,
    *,
    url: str,
    codificacao: str,
    seletor_cards: str,
    empresa: str,
    extrator: str,
) -> tuple[dict[str, Any], ...]:
    """Converte cards estáticos cuja inscrição é controlada pela própria página."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for card in seletor.css(seletor_cards):
        titulo = _texto(card.css("h2, h3, h4").xpath("string(.)").get())
        conteudo = _texto(card.xpath("string(.)").get())
        tem_candidatura = any(
            "candidat" in _texto(botao.xpath("string(.)").get()).casefold()
            for botao in card.css("a, button")
        )
        chave = f"{titulo}|{conteudo}"
        if not titulo or not tem_candidatura or chave in vistos:
            continue
        documento: dict[str, Any] = {
            "@type": "JobPosting",
            "identifier": sha256(chave.encode()).hexdigest()[:40],
            "title": titulo,
            "description": conteudo[:8000],
            "url": url,
            "_observatorio_apply_url": url,
            "hiringOrganization": {"@type": "Organization", "name": empresa},
            "jobLocation": {
                "@type": "Place",
                "address": {"@type": "PostalAddress", "addressCountry": "BR"},
            },
            "_observatorio_extrator": extrator,
        }
        local = re.search(
            r"([A-Za-z .]+)/(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b",
            conteudo,
        )
        if local:
            endereco = documento["jobLocation"]["address"]
            endereco["addressRegion"] = local.group(2)
            endereco["addressLocality"] = local.group(1).strip()
        vagas.append(documento)
        vistos.add(chave)
    return tuple(vagas)


def _extrair_senior(corpo: bytes, url: str, codificacao: str) -> tuple[dict[str, Any], ...]:
    """Converte o detalhe público JSON do Portal Senior em JobPosting."""

    try:
        dados = json.loads(corpo.decode(codificacao, errors="replace"))
    except (TypeError, ValueError):
        return ()
    if not isinstance(dados, dict):
        return ()
    titulo = dados.get("title") or dados.get("vacancyTitle")
    descricao = dados.get("description")
    if not isinstance(titulo, str) or not isinstance(descricao, str):
        return ()
    titulo, descricao = _texto(titulo), _texto(descricao)
    if not titulo or len(descricao) < 80:
        return ()
    parametros = parse_qs(urlsplit(url).query)
    vacancy_id = (parametros.get("vacancy_id") or [""])[0]
    tenant = (parametros.get("tenant") or [""])[0]
    tenant_domain = (parametros.get("tenantdomain") or [""])[0]
    candidatura = (
        "https://platform.senior.com.br/hcmrs/hcm/curriculo/"
        f"?tenant={tenant}&tenantdomain={tenant_domain}#!/vacancies/details/{vacancy_id}/?fromRecruitment=true"
    )
    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "title": titulo,
        "description": descricao,
        "url": candidatura,
        "_observatorio_apply_url": candidatura,
        "_observatorio_extrator": "senior_api_publica",
    }
    empresa = dados.get("companyName") or dados.get("company")
    if isinstance(empresa, str) and _texto(empresa):
        documento["hiringOrganization"] = {"@type": "Organization", "name": _texto(empresa)}
    localidade = dados.get("city") or dados.get("location")
    if isinstance(localidade, str) and _texto(localidade):
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressLocality": _texto(localidade)},
        }
    return (documento,)
