"""Cliente seguro para a futura publicação de vagas no Empregos.

O cliente permanece em simulação por padrão. Um POST real somente pode
acontecer quando todas as condições abaixo forem satisfeitas:

1. a preparação recebeu aprovação de campos e política;
2. o chamador passa ``confirmar_publicacao=True``;
3. o kill switch da configuração está habilitado;
4. o ambiente é staging ou production;
5. endpoint HTTPS e autenticação foram configurados explicitamente.

POSTs ainda não são repetidos automaticamente. Antes disso, precisamos
confirmar a garantia de idempotência da API e persistir a tentativa no MongoDB.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from observatorio_vagas.config import Settings
from observatorio_vagas.integrations.empregos.payload import (
    PayloadEmpregosInvalido,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    PublicacaoEmpregosBloqueada,
    ResultadoPreparacaoEmpregos,
)

# Nomes de cabeçalho HTTP são tokens e não podem conter espaços,
# quebras de linha ou outros caracteres de controle.
PADRAO_NOME_CABECALHO = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

# O corpo de uma resposta externa é útil para diagnóstico, mas não deve
# crescer sem limite dentro do resultado ou de um futuro histórico.
LIMITE_TEXTO_RESPOSTA = 4000


class SituacaoEnvioEmpregos(StrEnum):
    """Situação final conhecida pelo cliente."""

    SIMULADO = "simulado"
    SUCESSO = "sucesso"


class ConfiguracaoClienteEmpregosInvalida(ValueError):
    """Informa que uma trava ou configuração obrigatória está ausente."""


class ErroClienteEmpregos(RuntimeError):
    """Erro operacional seguro, sem credenciais nem payload na mensagem."""

    def __init__(
        self,
        mensagem: str,
        *,
        status_http: int | None = None,
        tentativas: int = 1,
    ) -> None:
        """Guarda metadados pequenos que podem ser usados no diagnóstico."""

        self.status_http = status_http
        self.tentativas = tentativas
        super().__init__(mensagem)


class FalhaTransporteEmpregos(ErroClienteEmpregos):
    """Falha técnica ocorrida durante a chamada HTTPS."""


class AutenticacaoEmpregosRecusada(ErroClienteEmpregos):
    """A API recusou a credencial configurada."""


class RequisicaoEmpregosRecusada(ErroClienteEmpregos):
    """A API recusou permanentemente os dados desta requisição."""


class ResultadoEmpregosIndeterminado(ErroClienteEmpregos):
    """Não é seguro concluir se a API processou a operação."""


@dataclass(frozen=True, slots=True)
class RespostaTransporteEmpregos:
    """Resposta mínima devolvida por um transporte HTTP."""

    status_http: int
    corpo: Any | None = None
    cabecalhos: Mapping[str, str] | None = None


class TransporteEmpregos(Protocol):
    """Contrato injetável que mantém os testes completamente offline."""

    def postar(
        self,
        *,
        url: str,
        payload: Mapping[str, Any],
        cabecalhos: Mapping[str, str],
        timeout_seconds: float,
    ) -> RespostaTransporteEmpregos:
        """Executa exatamente um POST, sem repetição automática."""

    def fechar(self) -> None:
        """Libera os recursos do transporte."""


@dataclass(frozen=True, slots=True)
class ResultadoEnvioEmpregos:
    """Resultado pequeno e sanitizado de uma simulação ou publicação."""

    situacao: SituacaoEnvioEmpregos
    external_job_posting_id: str
    operation_type: str
    payload_sha256: str
    tentativas: int
    status_http: int | None = None
    request_id: str | None = None
    corpo_resposta: Any | None = None

    @property
    def simulado(self) -> bool:
        """Indica que nenhuma chamada de rede foi realizada."""

        return self.situacao is SituacaoEnvioEmpregos.SIMULADO

    @property
    def sucesso(self) -> bool:
        """Indica que a API respondeu com um status HTTP 2xx."""

        return self.situacao is SituacaoEnvioEmpregos.SUCESSO


def _limitar_texto(valor: str) -> str:
    """Limita um texto externo sem alterar mensagens pequenas."""

    if len(valor) <= LIMITE_TEXTO_RESPOSTA:
        return valor

    return f"{valor[:LIMITE_TEXTO_RESPOSTA]}...[TRUNCADO]"


def _sanitizar_resposta(
    valor: Any,
    *,
    segredo: str,
) -> Any:
    """Remove a credencial e limita textos devolvidos pela API."""

    if isinstance(valor, str):
        texto = valor.replace(segredo, "[REDACTED]") if segredo else valor
        return _limitar_texto(texto)

    if isinstance(valor, Mapping):
        return {
            str(chave): _sanitizar_resposta(conteudo, segredo=segredo)
            for chave, conteudo in valor.items()
        }

    if isinstance(valor, (list, tuple)):
        return [_sanitizar_resposta(item, segredo=segredo) for item in valor]

    return valor


def _extrair_request_id(
    cabecalhos: Mapping[str, str] | None,
) -> str | None:
    """Procura um identificador técnico sem assumir um único fornecedor."""

    if not cabecalhos:
        return None

    normalizados = {str(nome).casefold(): str(valor) for nome, valor in cabecalhos.items()}

    for nome in (
        "x-request-id",
        "request-id",
        "x-correlation-id",
        "correlation-id",
    ):
        valor = normalizados.get(nome)

        if valor:
            return _limitar_texto(valor)

    return None


def _preparar_payload(
    preparacao: ResultadoPreparacaoEmpregos,
) -> tuple[dict[str, Any], str, str, str]:
    """Valida e copia o payload antes de entregá-lo ao transporte."""

    if not preparacao.pronto_para_envio or preparacao.payload is None:
        if preparacao.motivos_bloqueio:
            raise PublicacaoEmpregosBloqueada(preparacao.motivos_bloqueio)

        raise PayloadEmpregosInvalido("A preparação não liberou um payload para envio.")

    try:
        texto_canonico = json.dumps(
            preparacao.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload = json.loads(texto_canonico)
    except (TypeError, ValueError) as erro:
        raise PayloadEmpregosInvalido(
            "O payload preparado não pode ser convertido para JSON."
        ) from erro

    if not isinstance(payload, dict):
        raise PayloadEmpregosInvalido("O payload preparado deve ser um objeto JSON.")

    external_id = payload.get("externalJobPostingId")
    operation_type = payload.get("jobPostingOperationType")

    if not isinstance(external_id, str) or not external_id.strip():
        raise PayloadEmpregosInvalido(
            "O payload não possui externalJobPostingId utilizável.",
            campos=("externalJobPostingId",),
        )

    if not isinstance(operation_type, str) or not operation_type.strip():
        raise PayloadEmpregosInvalido(
            "O payload não possui jobPostingOperationType utilizável.",
            campos=("jobPostingOperationType",),
        )

    payload_sha256 = hashlib.sha256(texto_canonico.encode()).hexdigest()

    return payload, external_id.strip(), operation_type.strip(), payload_sha256


class TransporteRequestsEmpregos:
    """Transporte HTTPS baseado em requests, sem retry automático."""

    def __init__(self) -> None:
        """Cria uma sessão somente quando um envio real foi autorizado."""

        self._sessao = requests.Session()

    def postar(
        self,
        *,
        url: str,
        payload: Mapping[str, Any],
        cabecalhos: Mapping[str, str],
        timeout_seconds: float,
    ) -> RespostaTransporteEmpregos:
        """Executa um POST e impede redirecionamento de credenciais."""

        try:
            resposta = self._sessao.post(
                url,
                json=dict(payload),
                headers=dict(cabecalhos),
                timeout=timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as erro:
            raise FalhaTransporteEmpregos("Falha de comunicação com a API do Empregos.") from erro

        corpo: Any | None = None

        if resposta.content:
            try:
                corpo = resposta.json()
            except requests.exceptions.JSONDecodeError:
                corpo = resposta.text
            except ValueError:
                corpo = resposta.text

        return RespostaTransporteEmpregos(
            status_http=resposta.status_code,
            corpo=corpo,
            cabecalhos=dict(resposta.headers),
        )

    def fechar(self) -> None:
        """Fecha conexões mantidas pela sessão."""

        self._sessao.close()


class ClienteEmpregos:
    """Coordena simulação, travas de segurança e um único POST real."""

    def __init__(
        self,
        settings: Settings,
        *,
        criar_transporte: Callable[[], TransporteEmpregos] = TransporteRequestsEmpregos,
    ) -> None:
        """Guarda configurações sem criar conexão nem revelar a chave."""

        self._settings = settings
        self._criar_transporte = criar_transporte

    def __repr__(self) -> str:
        """Mostra somente informações operacionais não sensíveis."""

        return (
            "ClienteEmpregos("
            f"environment={self._settings.environment!r}, "
            f"publicacao_habilitada={self._settings.empregos_publicacao_habilitada!r}"
            ")"
        )

    def validar_configuracao_publicacao(self) -> None:
        """Confirma as travas antes de persistir o estado ``enviando``."""

        self._configuracao_real()

    def _configuracao_real(self) -> tuple[str, dict[str, str], str]:
        """Valida todas as configurações necessárias para o POST."""

        if not self._settings.empregos_publicacao_habilitada:
            raise ConfiguracaoClienteEmpregosInvalida(
                "A publicação real no Empregos está desabilitada pela configuração."
            )

        if self._settings.environment == "development":
            raise ConfiguracaoClienteEmpregosInvalida(
                "A publicação real no Empregos não é permitida em development."
            )

        base_url = self._settings.empregos_api_base_url
        caminho = self._settings.empregos_api_publication_path
        nome_cabecalho = self._settings.empregos_api_auth_header
        chave_protegida = self._settings.empregos_api_key

        if base_url is None or not caminho or not caminho.strip():
            raise ConfiguracaoClienteEmpregosInvalida(
                "A URL e o caminho oficial de publicação não foram configurados."
            )

        if not nome_cabecalho or not PADRAO_NOME_CABECALHO.fullmatch(nome_cabecalho.strip()):
            raise ConfiguracaoClienteEmpregosInvalida(
                "O cabeçalho oficial de autenticação não foi configurado corretamente."
            )

        if chave_protegida is None or not chave_protegida.get_secret_value():
            raise ConfiguracaoClienteEmpregosInvalida(
                "A credencial da API do Empregos não foi configurada."
            )

        caminho_normalizado = caminho.strip()

        if not caminho_normalizado.startswith("/"):
            raise ConfiguracaoClienteEmpregosInvalida(
                "O caminho de publicação deve começar com uma barra."
            )

        if "?" in caminho_normalizado or "#" in caminho_normalizado:
            raise ConfiguracaoClienteEmpregosInvalida(
                "O caminho de publicação não pode conter consulta ou fragmento."
            )

        url = f"{str(base_url).rstrip('/')}{caminho_normalizado}"
        partes_url = urlsplit(url)

        if partes_url.scheme != "https" or not partes_url.hostname:
            raise ConfiguracaoClienteEmpregosInvalida(
                "A publicação real exige um endpoint HTTPS válido."
            )

        if partes_url.username is not None or partes_url.password is not None:
            raise ConfiguracaoClienteEmpregosInvalida(
                "O endpoint não pode conter credenciais na URL."
            )

        chave = chave_protegida.get_secret_value()
        prefixo = self._settings.empregos_api_auth_prefix.strip()

        if "\r" in prefixo or "\n" in prefixo:
            raise ConfiguracaoClienteEmpregosInvalida(
                "O prefixo de autenticação contém caracteres inválidos."
            )

        valor_autenticacao = f"{prefixo} {chave}" if prefixo else chave
        cabecalhos = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            nome_cabecalho.strip(): valor_autenticacao,
        }

        return url, cabecalhos, chave

    def publicar(
        self,
        preparacao: ResultadoPreparacaoEmpregos,
        *,
        confirmar_publicacao: bool = False,
    ) -> ResultadoEnvioEmpregos:
        """Simula por padrão ou realiza um POST explicitamente autorizado."""

        payload, external_id, operation_type, payload_sha256 = _preparar_payload(preparacao)

        if not confirmar_publicacao:
            return ResultadoEnvioEmpregos(
                situacao=SituacaoEnvioEmpregos.SIMULADO,
                external_job_posting_id=external_id,
                operation_type=operation_type,
                payload_sha256=payload_sha256,
                tentativas=0,
            )

        url, cabecalhos, chave = self._configuracao_real()
        transporte = self._criar_transporte()

        try:
            try:
                resposta = transporte.postar(
                    url=url,
                    payload=payload,
                    cabecalhos=cabecalhos,
                    timeout_seconds=self._settings.request_timeout_seconds,
                )
            except FalhaTransporteEmpregos as erro:
                causa = erro.__cause__ or erro
                raise ResultadoEmpregosIndeterminado(
                    "Não foi possível determinar se o Empregos recebeu a publicação.",
                    tentativas=1,
                ) from causa
        finally:
            # Uma falha ao fechar a sessão não muda a resposta da API.
            with suppress(Exception):
                transporte.fechar()

        corpo = _sanitizar_resposta(resposta.corpo, segredo=chave)
        request_id = _extrair_request_id(resposta.cabecalhos)
        status = resposta.status_http

        if 200 <= status < 300:
            return ResultadoEnvioEmpregos(
                situacao=SituacaoEnvioEmpregos.SUCESSO,
                external_job_posting_id=external_id,
                operation_type=operation_type,
                payload_sha256=payload_sha256,
                tentativas=1,
                status_http=status,
                request_id=request_id,
                corpo_resposta=corpo,
            )

        if status in {401, 403}:
            raise AutenticacaoEmpregosRecusada(
                "A API do Empregos recusou a autenticação.",
                status_http=status,
                tentativas=1,
            )

        # 408 e 429 são respostas temporárias. Mesmo assim, o cliente
        # não repete o POST até existir controle idempotente persistido.
        if status in {408, 429}:
            raise ResultadoEmpregosIndeterminado(
                "A API do Empregos não confirmou a publicação.",
                status_http=status,
                tentativas=1,
            )

        if 400 <= status < 500:
            raise RequisicaoEmpregosRecusada(
                "A API do Empregos recusou os dados da publicação.",
                status_http=status,
                tentativas=1,
            )

        raise ResultadoEmpregosIndeterminado(
            "A API do Empregos não confirmou a publicação.",
            status_http=status,
            tentativas=1,
        )
