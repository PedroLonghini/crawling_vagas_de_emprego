"""Registro central das plataformas de vagas conhecidas pelo crawler.

As regras ficam em ``plataformas.toml``. Antes, cada plataforma era declarada
à mão na fábrica de requisições, na barreira de domínios e no spider, e as três
listas saíam de sincronia (a API da Sólides, por exemplo, era aceita pela
fábrica e barrada pela barreira). Agora as três camadas consultam este módulo.
"""

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from urllib.parse import parse_qs, urlsplit

SITEMAP_PADRAO = "padrao"
SITEMAP_NENHUM = "nenhum"
SITEMAP_POR_LOCATARIO = "por_locatario"
_POLITICAS_SITEMAP = frozenset({SITEMAP_PADRAO, SITEMAP_NENHUM, SITEMAP_POR_LOCATARIO})


class ErroRegistroPlataformas(ValueError):
    """O arquivo plataformas.toml está inconsistente."""


@dataclass(frozen=True, slots=True)
class RegraCompanheiro:
    """Host externo que a página de carreira consulta (API ou portal)."""

    host: str
    caminho: re.Pattern[str]
    consulta: tuple[tuple[str, str], ...]
    exemplo: str

    def aceita(self, url: str) -> bool:
        partes = urlsplit(url)
        return (
            partes.scheme == "https"
            and partes.netloc == self.host
            and self.caminho.fullmatch(partes.path) is not None
            and _consulta_confere(partes.query, self.consulta)
        )


@dataclass(frozen=True, slots=True)
class RegraRedirecionamento:
    """Redirecionamento oficial de um portal para outro host."""

    host: str
    mesmo_caminho: bool
    caminho: re.Pattern[str] | None
    consulta: tuple[tuple[str, str], ...]
    consulta_opcional: bool
    exemplo_origem: str
    exemplo: str

    def aceita(self, *, url_origem: str, url_destino: str) -> bool:
        origem, destino = urlsplit(url_origem), urlsplit(url_destino)
        if origem.scheme != "https" or destino.scheme != "https" or destino.netloc != self.host:
            return False
        if self.mesmo_caminho:
            return origem.path == destino.path and origem.query == destino.query
        if self.caminho is None or self.caminho.fullmatch(destino.path) is None:
            return False
        if self.consulta_opcional and not destino.query:
            return True
        return _consulta_confere(destino.query, self.consulta)


@dataclass(frozen=True, slots=True)
class Plataforma:
    """Uma plataforma de vagas e as exceções de rede que ela exige."""

    nome: str
    exemplo_portal: str
    portais: frozenset[str]
    portais_sufixo: tuple[str, ...]
    multi_locatario: bool
    sitemap: str
    sufixo_sitemap_locatario: str
    hosts_json: frozenset[str]
    sufixos_json: tuple[str, ...]
    companheiros: tuple[RegraCompanheiro, ...]
    redirecionamentos: tuple[RegraRedirecionamento, ...]

    def casa_portal(self, host: str) -> bool:
        return host in self.portais or any(host.endswith(s) for s in self.portais_sufixo)

    def casa_qualquer_host(self, host: str) -> bool:
        """Portal, host de API, destino de redirecionamento ou host JSON."""

        return (
            self.casa_portal(host)
            or host in self.hosts_json
            or any(host.endswith(s) for s in self.sufixos_json)
            or any(regra.host == host for regra in self.companheiros)
            or any(regra.host == host for regra in self.redirecionamentos)
        )


def _consulta_confere(consulta: str, exigidos: tuple[tuple[str, str], ...]) -> bool:
    parametros = parse_qs(consulta)
    return all(
        (parametros.get(nome) or [""])[0].casefold() == valor.casefold() for nome, valor in exigidos
    )


def _host_valido(valor: object, campo: str, plataforma: str) -> str:
    if not isinstance(valor, str) or not valor or valor != valor.casefold() or "/" in valor:
        raise ErroRegistroPlataformas(
            f"{plataforma}: {campo} precisa ser um host em minúsculas, sem barra: {valor!r}"
        )
    return valor


def _pares_consulta(valor: object, plataforma: str) -> tuple[tuple[str, str], ...]:
    if valor is None:
        return ()
    if not isinstance(valor, dict) or not all(isinstance(v, str) for v in valor.values()):
        raise ErroRegistroPlataformas(f"{plataforma}: consulta precisa ser {{ nome = 'valor' }}")
    return tuple(valor.items())


def _regex(valor: object, plataforma: str) -> re.Pattern[str]:
    if not isinstance(valor, str) or not valor.startswith("/"):
        raise ErroRegistroPlataformas(f"{plataforma}: caminho precisa começar com '/'")
    try:
        return re.compile(valor)
    except re.error as erro:
        raise ErroRegistroPlataformas(f"{plataforma}: regex inválida {valor!r}: {erro}") from erro


def _montar_plataforma(bruto: dict[str, object]) -> Plataforma:
    nome = bruto.get("nome")
    if not isinstance(nome, str) or not nome:
        raise ErroRegistroPlataformas("toda plataforma precisa de 'nome'")

    sitemap = bruto.get("sitemap", SITEMAP_PADRAO)
    if sitemap not in _POLITICAS_SITEMAP:
        raise ErroRegistroPlataformas(f"{nome}: sitemap inválido: {sitemap!r}")
    multi = bool(bruto.get("multi_locatario", False))
    if sitemap == SITEMAP_POR_LOCATARIO and not multi:
        raise ErroRegistroPlataformas(f"{nome}: sitemap por_locatario exige multi_locatario")

    portais = frozenset(_host_valido(h, "portais", nome) for h in bruto.get("portais", []))
    sufixos = tuple(str(s) for s in bruto.get("portais_sufixo", []))
    if not portais and not sufixos:
        raise ErroRegistroPlataformas(f"{nome}: informe 'portais' ou 'portais_sufixo'")
    if any(not s.startswith(".") for s in (*sufixos, *bruto.get("sufixos_json", []))):
        raise ErroRegistroPlataformas(f"{nome}: sufixos de host precisam começar com '.'")

    companheiros = []
    for item in bruto.get("companheiro", []):
        regra = RegraCompanheiro(
            host=_host_valido(item.get("host"), "companheiro.host", nome),
            caminho=_regex(item.get("caminho"), nome),
            consulta=_pares_consulta(item.get("consulta"), nome),
            exemplo=str(item.get("exemplo", "")),
        )
        if not regra.aceita(regra.exemplo):
            raise ErroRegistroPlataformas(
                f"{nome}: o exemplo {regra.exemplo!r} não casa com a própria regra"
            )
        companheiros.append(regra)

    redirecionamentos = []
    for item in bruto.get("redirecionamento", []):
        mesmo = bool(item.get("mesmo_caminho", False))
        regra_r = RegraRedirecionamento(
            host=_host_valido(item.get("host"), "redirecionamento.host", nome),
            mesmo_caminho=mesmo,
            caminho=None if mesmo else _regex(item.get("caminho"), nome),
            consulta=_pares_consulta(item.get("consulta"), nome),
            consulta_opcional=bool(item.get("consulta_opcional", False)),
            exemplo_origem=str(item.get("exemplo_origem", "")),
            exemplo=str(item.get("exemplo", "")),
        )
        if not regra_r.aceita(url_origem=regra_r.exemplo_origem, url_destino=regra_r.exemplo):
            raise ErroRegistroPlataformas(
                f"{nome}: o redirecionamento de exemplo não casa com a própria regra"
            )
        redirecionamentos.append(regra_r)

    plataforma = Plataforma(
        nome=nome,
        exemplo_portal=str(bruto.get("exemplo_portal", "")),
        portais=portais,
        portais_sufixo=sufixos,
        multi_locatario=multi,
        sitemap=str(sitemap),
        sufixo_sitemap_locatario=str(bruto.get("sufixo_sitemap_locatario", "")),
        hosts_json=frozenset(
            _host_valido(h, "hosts_json", nome) for h in bruto.get("hosts_json", [])
        ),
        sufixos_json=tuple(str(s) for s in bruto.get("sufixos_json", [])),
        companheiros=tuple(companheiros),
        redirecionamentos=tuple(redirecionamentos),
    )
    host_exemplo = (urlsplit(plataforma.exemplo_portal).hostname or "").casefold()
    if not plataforma.casa_portal(host_exemplo):
        raise ErroRegistroPlataformas(
            f"{nome}: exemplo_portal não pertence aos portais da plataforma"
        )
    return plataforma


@lru_cache(maxsize=1)
def carregar_plataformas() -> tuple[Plataforma, ...]:
    """Lê e valida ``plataformas.toml`` uma única vez por processo."""

    texto = (resources.files(__package__) / "plataformas.toml").read_text(encoding="utf-8")
    plataformas = tuple(_montar_plataforma(item) for item in tomllib.loads(texto)["plataforma"])
    nomes = [p.nome for p in plataformas]
    if len(set(nomes)) != len(nomes):
        raise ErroRegistroPlataformas("nomes de plataforma duplicados")
    return plataformas


def _portais_de(host: str) -> tuple[Plataforma, ...]:
    host = host.casefold()
    return tuple(p for p in carregar_plataformas() if p.casa_portal(host))


def plataforma_do_host(host: str) -> Plataforma | None:
    """Plataforma dona do host (portal, API, redirecionamento ou JSON)."""

    host = host.casefold()
    return next((p for p in carregar_plataformas() if p.casa_qualquer_host(host)), None)


def permite_companheiro(*, dominio_origem: str, url: str) -> bool:
    """A URL é um host externo autorizado para o portal de origem?"""

    return any(
        regra.aceita(url) for p in _portais_de(dominio_origem) for regra in p.companheiros
    )


def host_companheiro_permitido(*, dominio_origem: str, dominio_destino: str) -> bool:
    """Versão só por host, usada ao filtrar links de detalhe."""

    destino = dominio_destino.casefold()
    return any(
        regra.host == destino for p in _portais_de(dominio_origem) for regra in p.companheiros
    )


def permite_redirecionamento(*, dominio_origem: str, url_origem: str, url_destino: str) -> bool:
    """O redirecionamento entre portais é um dos cadastrados?"""

    if (urlsplit(url_origem).netloc or "").casefold() != dominio_origem.casefold():
        return False
    return any(
        regra.aceita(url_origem=url_origem, url_destino=url_destino)
        for p in _portais_de(dominio_origem)
        for regra in p.redirecionamentos
    )


def aceita_json(host: str) -> bool:
    """O host responde JSON e deve receber o cabeçalho Accept correspondente."""

    host = host.casefold()
    return any(
        host in p.hosts_json or any(host.endswith(s) for s in p.sufixos_json)
        for p in carregar_plataformas()
    )


def hosts_companheiros_de(dominios_alvo: set[str] | frozenset[str]) -> list[str]:
    """Hosts externos a liberar para os alvos informados, sem repetição."""

    encontrados = {
        regra.host
        for dominio in dominios_alvo
        for p in _portais_de(dominio)
        for regra in (*p.companheiros, *p.redirecionamentos)
    }
    return sorted(encontrados)


def politica_sitemap(host: str) -> str:
    """Política de sitemap da plataforma dona do host (padrão: baixar)."""

    plataforma = plataforma_do_host(host)
    return plataforma.sitemap if plataforma is not None else SITEMAP_PADRAO


def locatario_do_alvo(url_inicial: str) -> str | None:
    """Empresa dentro de um host compartilhado: primeiro segmento do caminho."""

    segmentos = [s for s in urlsplit(url_inicial).path.split("/") if s]
    return segmentos[0].casefold() if segmentos else None


def filtrar_urls_do_locatario(
    urls: list[str] | tuple[str, ...] | set[str], *, host: str, locatario: str
) -> list[str]:
    """Mantém só entradas de sitemap que pertencem à empresa do alvo."""

    plataforma = plataforma_do_host(host)
    sufixo = plataforma.sufixo_sitemap_locatario if plataforma is not None else ""
    aceitos = {locatario, f"{locatario}{sufixo}"}
    mantidas = []
    for url in urls:
        segmentos = [s.casefold() for s in urlsplit(url).path.split("/") if s]
        if any(s.removesuffix(".xml") in aceitos for s in segmentos):
            mantidas.append(url)
    return mantidas
