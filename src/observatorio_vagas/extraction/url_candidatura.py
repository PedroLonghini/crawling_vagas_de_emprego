"""Descoberta segura de URLs de candidatura em páginas HTML."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

# Palavras que indicam candidatura quando aparecem na própria URL.
_SINAIS_URL = (
    "apply",
    "application",
    "candidatura",
    "candidatar",
    "candidate",
)

# Palavras aceitas quando aparecem no texto visível
# ou na descrição acessível do link.
_SINAIS_TEXTO = (
    "apply",
    "apply now",
    "how to apply",
    "candidatar",
    "candidate-se",
    "enviar currículo",
    "enviar curriculo",
    "envie seu currículo",
    "envie seu curriculo",
)


@dataclass(frozen=True, slots=True)
class UrlCandidaturaEncontrada:
    """URL escolhida e evidências que justificam a escolha."""

    # Endereço completo encontrado na página.
    url: str

    # Explica por que o link foi considerado uma candidatura.
    #
    # Exemplos:
    #
    # palavra_na_url
    # palavra_no_texto
    # atributo_de_candidatura
    evidencias: tuple[str, ...]

    # Quanto maior a pontuação, mais confiável é o candidato.
    pontuacao: int


@dataclass(slots=True)
class _LinkHtml:
    """Representação interna de uma tag HTML de link."""

    # Valor original do atributo href.
    href: str

    # Todos os atributos existentes no link.
    atributos: dict[str, str]

    # Partes do texto visível existente dentro do link.
    partes_texto: list[str]

    # Posição do link dentro da página.
    ordem: int


class _LeitorLinksHtml(HTMLParser):
    """Lê somente links sem executar JavaScript da página."""

    def __init__(self) -> None:
        """Inicializa o leitor sem links."""

        # convert_charrefs transforma entidades HTML.
        #
        # Exemplo:
        #
        # &amp; vira &
        super().__init__(convert_charrefs=True)

        # Links encontrados na página.
        self.links: list[_LinkHtml] = []

        # Link que está sendo lido neste momento.
        self._link_atual: _LinkHtml | None = None

        # Número usado para preservar a ordem da página.
        self._proxima_ordem = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Começa a registrar uma tag de link."""

        # Transforma a lista de atributos em um dicionário.
        #
        # Exemplo:
        #
        # href="https://example.com/apply"
        #
        # vira:
        #
        # {"href": "https://example.com/apply"}
        atributos = {nome.casefold(): valor or "" for nome, valor in attrs}

        # Alguns frontends usam um botão ou card no lugar de uma âncora. Só
        # aceitamos atributos que declaram explicitamente que a URL é de
        # candidatura; ``data-url`` genérico, por exemplo, não é suficiente.
        href_candidatura = (
            atributos.get("data-apply-url") or atributos.get("data-application-url") or ""
        ).strip()
        if href_candidatura:
            atributos_candidatura = dict(atributos)
            atributos_candidatura.setdefault("data-link-type", "apply")
            self.links.append(
                _LinkHtml(
                    href=href_candidatura,
                    atributos=atributos_candidatura,
                    partes_texto=[],
                    ordem=self._proxima_ordem,
                )
            )
            self._proxima_ordem += 1

        # Somente <a> possui texto próprio que precisamos acumular até o
        # fechamento. Os atributos explícitos acima funcionam em qualquer tag.
        if tag.casefold() != "a":
            return

        href = atributos.get("href", "").strip()

        self._link_atual = _LinkHtml(
            href=href,
            atributos=atributos,
            partes_texto=[],
            ordem=self._proxima_ordem,
        )

        self._proxima_ordem += 1

    def handle_data(self, data: str) -> None:
        """Guarda o texto visível existente dentro do link."""

        # Se não estamos dentro de uma tag <a>, ignoramos o texto.
        if self._link_atual is None:
            return

        # Remove espaços e quebras de linha desnecessárias.
        texto = " ".join(data.split())

        if texto:
            self._link_atual.partes_texto.append(texto)

    def handle_endtag(self, tag: str) -> None:
        """Finaliza o link quando encontramos </a>."""

        if tag.casefold() != "a" or self._link_atual is None:
            return

        self.links.append(self._link_atual)
        self._link_atual = None

    def close(self) -> None:
        """Finaliza o parser e preserva um link HTML incompleto."""

        super().close()

        # Algumas páginas podem possuir HTML imperfeito.
        #
        # Caso um <a> não tenha sido fechado corretamente,
        # ainda preservamos o link que estava sendo lido.
        if self._link_atual is not None:
            self.links.append(self._link_atual)
            self._link_atual = None


def _possui_sinal(
    texto: str,
    sinais: tuple[str, ...],
) -> bool:
    """Informa se ao menos um sinal aparece no texto."""

    # casefold permite comparar maiúsculas e minúsculas.
    texto_normalizado = texto.casefold()

    return any(sinal in texto_normalizado for sinal in sinais)


def _normalizar_url(
    href: str,
    *,
    url_base: str,
) -> str | None:
    """Converte links relativos e rejeita protocolos inseguros."""

    if not href:
        return None

    # urljoin transforma um caminho relativo em URL completa.
    #
    # Exemplo:
    #
    # URL base:
    # https://empresa.com/vagas/123
    #
    # href:
    # /apply/123
    #
    # Resultado:
    # https://empresa.com/apply/123
    url = urljoin(url_base, href)

    partes = urlsplit(url)

    # Aceitamos somente páginas HTTP ou HTTPS com domínio definido.
    #
    # Isso elimina:
    #
    # mailto:
    # javascript:
    # tel:
    # links quebrados
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        return None

    return url


def _classificar_link(
    link: _LinkHtml,
    *,
    url_base: str,
) -> UrlCandidaturaEncontrada | None:
    """Pontua um link utilizando evidências explícitas."""

    url = _normalizar_url(
        link.href,
        url_base=url_base,
    )

    if url is None:
        return None

    # Um botão que apenas rola para outra seção da mesma página não é o
    # destino de candidatura. A seção pode conter o formulário verdadeiro.
    if urlsplit(url).fragment and urldefrag(url)[0] == urldefrag(url_base)[0]:
        return None

    evidencias: list[str] = []
    pontuacao = 0

    # A própria URL é a evidência mais forte.
    #
    # Exemplos:
    #
    # /jobs/123/apply
    # /candidatura/123
    # /application/123
    if _possui_sinal(url, _SINAIS_URL):
        evidencias.append("palavra_na_url")
        pontuacao += 100

    # Também consideramos:
    #
    # - texto visível;
    # - aria-label;
    # - title.
    #
    # O aria-label é importante porque alguns links possuem
    # um texto visual genérico, mas uma descrição mais detalhada.
    texto_link = " ".join(
        (
            *link.partes_texto,
            link.atributos.get("aria-label", ""),
            link.atributos.get("title", ""),
        )
    )

    if _possui_sinal(
        texto_link,
        _SINAIS_TEXTO,
    ):
        evidencias.append("palavra_no_texto")
        pontuacao += 60

    # Alguns sites identificam a finalidade do link
    # utilizando o atributo data-link-type.
    tipo_link = link.atributos.get(
        "data-link-type",
        "",
    ).casefold()

    if any(
        sinal in tipo_link
        for sinal in (
            "apply",
            "application",
            "candidatura",
        )
    ):
        evidencias.append("atributo_de_candidatura")
        pontuacao += 80

    # Sem uma evidência explícita, o link não será considerado.
    #
    # Isso impede que a página inicial, contato ou redes sociais
    # sejam classificadas como candidatura.
    if not evidencias:
        return None

    # Formulários externos explícitos costumam usar URLs opacas (forms.gle,
    # docs.google.com/forms/d/ID), que não contêm `apply` no caminho.
    partes = urlsplit(url)
    if (
        partes.hostname in {"forms.gle", "docs.google.com"}
        and (partes.hostname == "forms.gle" or partes.path.startswith("/forms/"))
    ):
        evidencias.append("formulario_de_candidatura")
        pontuacao += 100

    return UrlCandidaturaEncontrada(
        url=url,
        evidencias=tuple(evidencias),
        pontuacao=pontuacao,
    )


def extrair_url_candidatura_html(
    conteudo: bytes | str,
    *,
    url_base: str,
) -> UrlCandidaturaEncontrada | None:
    """Encontra a URL de candidatura mais confiável da página.

    A função apenas lê o HTML.

    Nenhum link é aberto, visitado ou executado.
    """

    # O armazenamento bruto trabalha com bytes.
    #
    # Durante os testes também permitimos receber uma string.
    html = (
        conteudo.decode(
            "utf-8",
            errors="replace",
        )
        if isinstance(conteudo, bytes)
        else conteudo
    )

    leitor = _LeitorLinksHtml()

    # Entrega o HTML ao leitor.
    leitor.feed(html)

    # Finaliza a leitura.
    leitor.close()

    # Classifica todos os links da página.
    candidatos = [
        candidato
        for link in leitor.links
        if (
            candidato := _classificar_link(
                link,
                url_base=url_base,
            )
        )
        is not None
    ]

    if not candidatos:
        return None

    # A maior pontuação vence.
    #
    # Se dois links tiverem a mesma pontuação,
    # o primeiro da página será preservado.
    return max(
        candidatos,
        key=lambda candidato: candidato.pontuacao,
    )
