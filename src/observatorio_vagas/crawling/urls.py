"""Identidade de URLs sem parâmetros exclusivamente publicitários."""

from urllib.parse import unquote_plus, urlsplit, urlunsplit

from w3lib.url import safe_url_string


def normalizar_url_vaga(url: str) -> str:
    """Preserva IDs, filtros e paginação; remove somente rastreamento conhecido."""
    partes = urlsplit(url)
    consulta = []
    for par in partes.query.split("&"):
        chave = unquote_plus(par.split("=", 1)[0]).casefold()
        if par and not chave.startswith("utm_") and chave not in {"fbclid", "gclid", "msclkid"}:
            consulta.append(par)
    return safe_url_string(
        urlunsplit((partes.scheme, partes.netloc, partes.path, "&".join(consulta), ""))
    )
