"""Clientes mínimos e auditáveis para APIs licenciadas de vagas.

As credenciais só existem dentro da chamada HTTPS. Elas não aparecem na URL
preservada, no corpo salvo, nas exceções ou nos relatórios operacionais.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from observatorio_vagas.config import Settings
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta

URL_ADZUNA_BRASIL = "https://api.adzuna.com/v1/api/jobs/br/search"
URL_JOOBLE_BRASIL = "https://br.jooble.org/api"


class ConfiguracaoAgregadorInvalida(ValueError):
    """Indica que uma chave ou parâmetro obrigatório não foi configurado."""


class ErroColetaAgregador(RuntimeError):
    """Erro de rede ou resposta inválida, sem dados sensíveis."""


@dataclass(frozen=True, slots=True)
class RespostaTransporteAgregador:
    """Resposta HTTP pequena, suficiente para auditoria e armazenamento bruto."""

    status_http: int
    corpo: bytes
    url_final: str
    cabecalhos: Mapping[str, str]


class TransporteAgregador(Protocol):
    """Contrato injetável para que os testes permaneçam completamente offline."""

    def obter(
        self,
        *,
        url: str,
        parametros: Mapping[str, str | int],
        timeout_seconds: float,
    ) -> RespostaTransporteAgregador:
        """Executa um GET sem seguir redirecionamentos."""

    def enviar_json(
        self,
        *,
        url: str,
        payload: Mapping[str, str | int],
        timeout_seconds: float,
    ) -> RespostaTransporteAgregador:
        """Executa um POST JSON sem seguir redirecionamentos."""

    def fechar(self) -> None:
        """Libera recursos do transporte."""


class TransporteRequestsAgregador:
    """Transporte HTTPS real, criado somente no momento da coleta confirmada."""

    def __init__(self) -> None:
        self._sessao = requests.Session()

    def obter(
        self,
        *,
        url: str,
        parametros: Mapping[str, str | int],
        timeout_seconds: float,
    ) -> RespostaTransporteAgregador:
        return self._executar(
            metodo="get",
            url=url,
            timeout_seconds=timeout_seconds,
            params=dict(parametros),
        )

    def enviar_json(
        self,
        *,
        url: str,
        payload: Mapping[str, str | int],
        timeout_seconds: float,
    ) -> RespostaTransporteAgregador:
        return self._executar(
            metodo="post",
            url=url,
            timeout_seconds=timeout_seconds,
            json=dict(payload),
        )

    def _executar(
        self,
        *,
        metodo: str,
        url: str,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> RespostaTransporteAgregador:
        try:
            resposta = getattr(self._sessao, metodo)(
                url,
                timeout=timeout_seconds,
                allow_redirects=False,
                headers={"Accept": "application/json"},
                **kwargs,
            )
        except requests.RequestException as erro:
            raise ErroColetaAgregador("Falha de comunicação com o agregador de vagas.") from erro
        return RespostaTransporteAgregador(
            status_http=resposta.status_code,
            corpo=resposta.content,
            url_final=resposta.url.split("?", maxsplit=1)[0],
            cabecalhos=dict(resposta.headers),
        )

    def fechar(self) -> None:
        self._sessao.close()


@dataclass(frozen=True, slots=True)
class ClienteAdzunaApi:
    """Busca uma página da API brasileira da Adzuna."""

    settings: Settings

    def coletar(self, *, pagina: int, consulta: str = "") -> RespostaBruta:
        """Busca uma página, mantendo a chave fora do registro bruto."""

        if pagina < 1:
            raise ValueError("pagina precisa ser pelo menos 1")
        app_id = _segredo_obrigatorio(self.settings.adzuna_app_id, "OBS_ADZUNA_APP_ID")
        app_key = _segredo_obrigatorio(self.settings.adzuna_app_key, "OBS_ADZUNA_APP_KEY")
        parametros: dict[str, str | int] = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": 50,
        }
        if consulta.strip():
            parametros["what"] = consulta.strip()
        url = f"{URL_ADZUNA_BRASIL}/{pagina}"
        transporte = TransporteRequestsAgregador()
        try:
            resposta = transporte.obter(
                url=url,
                parametros=parametros,
                timeout_seconds=self.settings.request_timeout_seconds,
            )
        finally:
            transporte.fechar()
        return _resposta_bruta(
            fonte=Fonte.ADZUNA,
            url_publica=f"{url}?page={pagina}",
            resposta=resposta,
            pagina=pagina,
        )


@dataclass(frozen=True, slots=True)
class ClienteJoobleApi:
    """Busca uma página da API brasileira da Jooble."""

    settings: Settings

    def coletar(self, *, pagina: int, consulta: str = "") -> RespostaBruta:
        """Busca uma página, sem escrever a chave na URL armazenada."""

        if pagina < 1:
            raise ValueError("pagina precisa ser pelo menos 1")
        chave = _segredo_obrigatorio(self.settings.jooble_api_key, "OBS_JOOBLE_API_KEY")
        transporte = TransporteRequestsAgregador()
        try:
            resposta = transporte.enviar_json(
                url=f"{URL_JOOBLE_BRASIL}/{chave}",
                payload={"keywords": consulta.strip(), "page": pagina},
                timeout_seconds=self.settings.request_timeout_seconds,
            )
        finally:
            transporte.fechar()
        return _resposta_bruta(
            fonte=Fonte.JOOBLE,
            url_publica=f"{URL_JOOBLE_BRASIL}/[CHAVE_OCULTA]?page={pagina}",
            resposta=resposta,
            pagina=pagina,
        )


def validar_json_agregador(corpo: bytes) -> None:
    """Confirma que uma resposta bem-sucedida é um JSON estruturado."""

    try:
        documento = json.loads(corpo)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ErroColetaAgregador("O agregador respondeu com um JSON inválido.") from erro
    if not isinstance(documento, dict):
        raise ErroColetaAgregador("O agregador respondeu com uma estrutura inesperada.")


def _segredo_obrigatorio(valor: Any, nome: str) -> str:
    """Obtém uma credencial sem revelá-la em mensagens de erro."""

    if valor is None or not valor.get_secret_value().strip():
        raise ConfiguracaoAgregadorInvalida(f"Configure {nome} antes de coletar.")
    return valor.get_secret_value()


def _resposta_bruta(
    *,
    fonte: Fonte,
    url_publica: str,
    resposta: RespostaTransporteAgregador,
    pagina: int,
) -> RespostaBruta:
    """Cria o registro bruto com URL sanitizada e contexto de listagem."""

    if 200 <= resposta.status_http < 300:
        validar_json_agregador(resposta.corpo)
    return RespostaBruta(
        fonte=fonte,
        url_solicitada=url_publica,
        url_final=url_publica,
        status_http=resposta.status_http,
        corpo=resposta.corpo,
        numero_pagina=pagina,
        tipo_pagina=TipoPaginaColeta.INICIAL,
        tipo_conteudo="application/json",
        codificacao="utf-8",
        cabecalhos=tuple(resposta.cabecalhos.items()),
    )
