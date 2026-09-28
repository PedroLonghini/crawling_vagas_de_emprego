"""Extração segura de coordenadas geográficas presentes no HTML."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from parsel import Selector

# Priorizamos marcadores explicitamente identificados como organização.
#
# O último seletor funciona como alternativa para páginas que não
# informam o tipo do marcador.
_SELETORES_DATA_POINT = (
    '//*[@data-marker-type="organisation"]/@data-point',
    '//*[@data-marker-type="organization"]/@data-point',
    "//*[@data-point]/@data-point",
)


@dataclass(frozen=True, slots=True)
class CoordenadasHtml:
    """Coordenadas encontradas na página e sua evidência."""

    latitude: float
    longitude: float
    evidencia: str


def _converter_numero(
    valor: object,
) -> float | None:
    """Converte um valor em número finito."""

    if valor is None or isinstance(valor, bool):
        return None

    try:
        numero = float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None

    if not isfinite(numero):
        return None

    return numero


def _coordenadas_validas(
    *,
    latitude: object,
    longitude: object,
) -> tuple[float, float] | None:
    """Valida os limites geográficos de latitude e longitude."""

    latitude_convertida = _converter_numero(
        latitude,
    )

    longitude_convertida = _converter_numero(
        longitude,
    )

    if latitude_convertida is None or longitude_convertida is None:
        return None

    if not -90 <= latitude_convertida <= 90:
        return None

    if not -180 <= longitude_convertida <= 180:
        return None

    return (
        latitude_convertida,
        longitude_convertida,
    )


def _interpretar_data_point(
    valor: str,
) -> CoordenadasHtml | None:
    """Interpreta um objeto GeoJSON armazenado em data-point."""

    try:
        documento = json.loads(valor)

    except (
        json.JSONDecodeError,
        TypeError,
    ):
        return None

    if not isinstance(
        documento,
        Mapping,
    ):
        return None

    tipo = documento.get(
        "type",
    )

    if not isinstance(tipo, str) or tipo.casefold() != "point":
        return None

    coordenadas = documento.get(
        "coordinates",
    )

    if not isinstance(coordenadas, list) or len(coordenadas) < 2:
        return None

    # GeoJSON utiliza a ordem:
    #
    # longitude, latitude
    longitude = coordenadas[0]
    latitude = coordenadas[1]

    coordenadas_validadas = _coordenadas_validas(
        latitude=latitude,
        longitude=longitude,
    )

    if coordenadas_validadas is None:
        return None

    (
        latitude_validada,
        longitude_validada,
    ) = coordenadas_validadas

    return CoordenadasHtml(
        latitude=latitude_validada,
        longitude=longitude_validada,
        evidencia="geojson_data_point",
    )


def extrair_coordenadas_html(
    conteudo: bytes | str,
) -> CoordenadasHtml | None:
    """Extrai coordenadas de um mapa presente no HTML.

    A função somente lê o HTML armazenado.

    Ela não consulta serviços de mapas e não realiza
    geocodificação externa.
    """

    html = (
        conteudo.decode(
            "utf-8",
            errors="replace",
        )
        if isinstance(conteudo, bytes)
        else conteudo
    )

    seletor = Selector(
        text=html,
    )

    for xpath in _SELETORES_DATA_POINT:
        for valor in seletor.xpath(xpath).getall():
            resultado = _interpretar_data_point(
                valor,
            )

            if resultado is not None:
                return resultado

    return None


def _geo_estruturado_valido(
    valor: object,
) -> bool:
    """Verifica se o JSON-LD já possui coordenadas válidas."""

    if not isinstance(
        valor,
        Mapping,
    ):
        return False

    return (
        _coordenadas_validas(
            latitude=valor.get("latitude"),
            longitude=valor.get("longitude"),
        )
        is not None
    )


def _enriquecer_local(
    local_original: Mapping[str, Any],
    coordenadas: CoordenadasHtml,
) -> dict[str, Any]:
    """Preenche geo somente quando o local ainda não o possui."""

    local = dict(
        local_original,
    )

    if _geo_estruturado_valido(
        local.get("geo"),
    ):
        return local

    local["geo"] = {
        "@type": "GeoCoordinates",
        "latitude": coordenadas.latitude,
        "longitude": coordenadas.longitude,
    }

    return local


def enriquecer_job_posting_com_geolocalizacao(
    documento: Mapping[str, Any],
    coordenadas: CoordenadasHtml | None,
) -> dict[str, Any]:
    """Adiciona coordenadas ao JobPosting sem sobrescrever dados válidos.

    O documento original não é alterado.
    """

    documento_enriquecido = dict(
        documento,
    )

    if coordenadas is None:
        return documento_enriquecido

    local_original = documento.get(
        "jobLocation",
    )
    if isinstance(
        local_original,
        Mapping,
    ):
        documento_enriquecido["jobLocation"] = _enriquecer_local(
            local_original,
            coordenadas,
        )

        return documento_enriquecido

    # Schema.org também permite uma lista de locais.
    if isinstance(
        local_original,
        list,
    ):
        locais = list(
            local_original,
        )

        for indice, local in enumerate(
            locais,
        ):
            if not isinstance(
                local,
                Mapping,
            ):
                continue

            locais[indice] = _enriquecer_local(
                local,
                coordenadas,
            )

            documento_enriquecido["jobLocation"] = locais

            return documento_enriquecido

    # Sem jobLocation não associamos coordenadas automaticamente.
    #
    # Isso evita atribuir um mapa qualquer da página à vaga.
    return documento_enriquecido
