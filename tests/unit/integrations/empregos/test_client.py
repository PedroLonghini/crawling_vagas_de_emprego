"""Testes offline do cliente seguro da API do Empregos."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
import requests
from pydantic import SecretStr

from observatorio_vagas.config import Settings
from observatorio_vagas.domain.elegibilidade import (
    BloqueioPublicacao,
    CodigoBloqueioPublicacao,
    ResultadoElegibilidadePublicacao,
)
from observatorio_vagas.domain.prontidao import RelatorioProntidao
from observatorio_vagas.integrations.empregos import (
    AutenticacaoEmpregosRecusada,
    ClienteEmpregos,
    ConfiguracaoClienteEmpregosInvalida,
    FalhaTransporteEmpregos,
    PayloadEmpregosInvalido,
    PublicacaoEmpregosBloqueada,
    RequisicaoEmpregosRecusada,
    RespostaTransporteEmpregos,
    ResultadoEmpregosIndeterminado,
    ResultadoPreparacaoEmpregos,
    SituacaoEnvioEmpregos,
    TransporteRequestsEmpregos,
)

CHAVE_TESTE = "token-ultrassecreto-empregos"

PAYLOAD_TESTE = {
    "company": {
        "applyUrl": "https://candidaturas.example.com/vaga-001",
        "name": "Tecnologia Atlas",
        "description": "Empresa fictícia.",
        "nationalRegister": "12.345.678/0001-90",
    },
    "externalJobPostingId": "fonte:alvo:VAGA-001",
    "jobPostingOperationType": "CREATE",
    "title": "Pessoa Desenvolvedora Python",
    "description": "Desenvolvimento de aplicações Python.",
    "location": {"address": "São Paulo, SP"},
}


def criar_settings_real(**alteracoes: Any) -> Settings:
    """Cria uma configuração segura e previsível para testes."""

    valores: dict[str, Any] = {
        "environment": "staging",
        "empregos_api_base_url": "https://parceiro.example.com/api",
        "empregos_api_publication_path": "/v1/job-postings",
        "empregos_api_auth_header": "X-API-Key",
        "empregos_api_auth_prefix": "",
        "empregos_api_key": SecretStr(CHAVE_TESTE),
        "empregos_publicacao_habilitada": True,
        "request_timeout_seconds": 7.5,
    }
    valores.update(alteracoes)
    return Settings(**valores)


def criar_preparacao_aprovada(
    payload: dict[str, Any] | None = None,
) -> ResultadoPreparacaoEmpregos:
    """Cria o resultado mínimo já aprovado pelas camadas anteriores."""

    relatorio = cast(
        RelatorioProntidao,
        SimpleNamespace(pronto_para_envio=True),
    )

    return ResultadoPreparacaoEmpregos(
        relatorio=relatorio,
        elegibilidade=ResultadoElegibilidadePublicacao(),
        payload=payload or PAYLOAD_TESTE,
    )


def criar_preparacao_bloqueada() -> ResultadoPreparacaoEmpregos:
    """Cria uma preparação cuja fonte não permite republicação."""

    relatorio = cast(
        RelatorioProntidao,
        SimpleNamespace(pronto_para_envio=True),
    )
    bloqueio = BloqueioPublicacao(
        codigo=CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO,
        mensagem="A fonte permite somente coleta.",
    )

    return ResultadoPreparacaoEmpregos(
        relatorio=relatorio,
        elegibilidade=ResultadoElegibilidadePublicacao(
            bloqueios=(bloqueio,),
        ),
        payload=None,
    )


@dataclass
class TransporteFalso:
    """Transporte em memória que nunca acessa a internet."""

    resposta: RespostaTransporteEmpregos | None = None
    erro: Exception | None = None
    chamadas: list[dict[str, Any]] = field(default_factory=list)
    fechado: bool = False

    def postar(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        cabecalhos: dict[str, str],
        timeout_seconds: float,
    ) -> RespostaTransporteEmpregos:
        """Registra a chamada e devolve o resultado configurado."""

        self.chamadas.append(
            {
                "url": url,
                "payload": payload,
                "cabecalhos": cabecalhos,
                "timeout_seconds": timeout_seconds,
            }
        )

        if self.erro is not None:
            raise self.erro

        assert self.resposta is not None
        return self.resposta

    def fechar(self) -> None:
        """Registra o encerramento do transporte."""

        self.fechado = True


def test_simulacao_padrao_nao_cria_transporte() -> None:
    """A operação padrão deve realizar exatamente zero chamadas externas."""

    def falhar_se_criar_transporte() -> TransporteFalso:
        raise AssertionError("o transporte não deveria ser criado")

    cliente = ClienteEmpregos(
        Settings(),
        criar_transporte=falhar_se_criar_transporte,
    )

    resultado = cliente.publicar(criar_preparacao_aprovada())

    assert resultado.situacao is SituacaoEnvioEmpregos.SIMULADO
    assert resultado.simulado is True
    assert resultado.sucesso is False
    assert resultado.tentativas == 0
    assert resultado.status_http is None
    assert len(resultado.payload_sha256) == 64


def test_preparacao_bloqueada_nao_cria_transporte() -> None:
    """Uma política proibida deve interromper antes da camada HTTP."""

    def falhar_se_criar_transporte() -> TransporteFalso:
        raise AssertionError("o transporte não deveria ser criado")

    cliente = ClienteEmpregos(
        criar_settings_real(),
        criar_transporte=falhar_se_criar_transporte,
    )

    with pytest.raises(PublicacaoEmpregosBloqueada):
        cliente.publicar(
            criar_preparacao_bloqueada(),
            confirmar_publicacao=True,
        )


def test_envio_real_exige_kill_switch() -> None:
    """Confirmação isolada não deve abrir a publicação real."""

    cliente = ClienteEmpregos(
        criar_settings_real(empregos_publicacao_habilitada=False),
    )

    with pytest.raises(ConfiguracaoClienteEmpregosInvalida):
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )


def test_envio_real_nao_e_permitido_em_development() -> None:
    """O ambiente local não pode publicar mesmo com as demais opções."""

    cliente = ClienteEmpregos(
        criar_settings_real(environment="development"),
    )

    with pytest.raises(ConfiguracaoClienteEmpregosInvalida):
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )


@pytest.mark.parametrize(
    "alteracoes",
    [
        {"empregos_api_base_url": None},
        {"empregos_api_publication_path": None},
        {"empregos_api_auth_header": None},
        {"empregos_api_key": None},
    ],
)
def test_envio_real_exige_configuracao_completa(
    alteracoes: dict[str, Any],
) -> None:
    """Nenhuma configuração externa obrigatória pode ser presumida."""

    cliente = ClienteEmpregos(criar_settings_real(**alteracoes))

    with pytest.raises(ConfiguracaoClienteEmpregosInvalida):
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )


def test_publica_com_url_autenticacao_payload_e_timeout_configurados() -> None:
    """O transporte deve receber somente os valores explicitamente configurados."""

    transporte = TransporteFalso(
        resposta=RespostaTransporteEmpregos(
            status_http=201,
            corpo={"id": "EMPREGOS-123"},
            cabecalhos={"X-Request-ID": "REQ-123"},
        )
    )
    cliente = ClienteEmpregos(
        criar_settings_real(),
        criar_transporte=lambda: transporte,
    )

    resultado = cliente.publicar(
        criar_preparacao_aprovada(),
        confirmar_publicacao=True,
    )

    assert resultado.sucesso is True
    assert resultado.status_http == 201
    assert resultado.request_id == "REQ-123"
    assert resultado.corpo_resposta == {"id": "EMPREGOS-123"}
    assert resultado.tentativas == 1
    assert transporte.fechado is True
    assert len(transporte.chamadas) == 1

    chamada = transporte.chamadas[0]
    assert chamada["url"] == "https://parceiro.example.com/api/v1/job-postings"
    assert chamada["payload"] == PAYLOAD_TESTE
    assert chamada["cabecalhos"]["X-API-Key"] == CHAVE_TESTE
    assert chamada["cabecalhos"]["Content-Type"] == "application/json"
    assert chamada["timeout_seconds"] == 7.5


def test_chave_nao_aparece_em_repr_erro_ou_resposta() -> None:
    """A credencial não pode vazar em nenhuma saída retornada ao chamador."""

    transporte = TransporteFalso(
        resposta=RespostaTransporteEmpregos(
            status_http=200,
            corpo={"eco": CHAVE_TESTE},
        )
    )
    cliente = ClienteEmpregos(
        criar_settings_real(),
        criar_transporte=lambda: transporte,
    )

    resultado = cliente.publicar(
        criar_preparacao_aprovada(),
        confirmar_publicacao=True,
    )

    assert CHAVE_TESTE not in repr(cliente)
    assert CHAVE_TESTE not in repr(resultado)
    assert resultado.corpo_resposta == {"eco": "[REDACTED]"}


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_erro_permanente_nao_repete_post(status: int) -> None:
    """Uma rejeição de dados deve realizar somente uma tentativa."""

    transporte = TransporteFalso(
        resposta=RespostaTransporteEmpregos(status_http=status),
    )
    cliente = ClienteEmpregos(
        criar_settings_real(max_retries=20),
        criar_transporte=lambda: transporte,
    )

    with pytest.raises(RequisicaoEmpregosRecusada) as captura:
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )

    assert captura.value.status_http == status
    assert captura.value.tentativas == 1
    assert len(transporte.chamadas) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_rejeicao_de_autenticacao_e_especifica(status: int) -> None:
    """Falhas de credencial devem ser distinguíveis de payload inválido."""

    transporte = TransporteFalso(
        resposta=RespostaTransporteEmpregos(status_http=status),
    )
    cliente = ClienteEmpregos(
        criar_settings_real(),
        criar_transporte=lambda: transporte,
    )

    with pytest.raises(AutenticacaoEmpregosRecusada) as captura:
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )

    assert captura.value.status_http == status
    assert CHAVE_TESTE not in str(captura.value)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_status_transitorio_fica_indeterminado_sem_retry(status: int) -> None:
    """Sem idempotência persistida, um POST nunca é repetido automaticamente."""

    transporte = TransporteFalso(
        resposta=RespostaTransporteEmpregos(status_http=status),
    )
    cliente = ClienteEmpregos(
        criar_settings_real(max_retries=20),
        criar_transporte=lambda: transporte,
    )

    with pytest.raises(ResultadoEmpregosIndeterminado) as captura:
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )

    assert captura.value.status_http == status
    assert captura.value.tentativas == 1
    assert len(transporte.chamadas) == 1


def test_falha_de_rede_fica_indeterminada_e_preserva_causa() -> None:
    """Um timeout pode ocorrer depois do aceite e não pode causar duplicação."""

    timeout = requests.ReadTimeout("timeout sem conteúdo sensível")

    try:
        raise FalhaTransporteEmpregos("falha segura") from timeout
    except FalhaTransporteEmpregos as erro_transporte:
        transporte = TransporteFalso(erro=erro_transporte)

    cliente = ClienteEmpregos(
        criar_settings_real(max_retries=20),
        criar_transporte=lambda: transporte,
    )

    with pytest.raises(ResultadoEmpregosIndeterminado) as captura:
        cliente.publicar(
            criar_preparacao_aprovada(),
            confirmar_publicacao=True,
        )

    assert captura.value.tentativas == 1
    assert captura.value.__cause__ is timeout
    assert len(transporte.chamadas) == 1


def test_payload_sem_identificador_externo_e_rejeitado_antes_do_http() -> None:
    """A identidade da vaga é obrigatória para controlar a publicação."""

    payload = dict(PAYLOAD_TESTE)
    payload.pop("externalJobPostingId")

    with pytest.raises(PayloadEmpregosInvalido) as captura:
        ClienteEmpregos(Settings()).publicar(
            criar_preparacao_aprovada(payload),
        )

    assert "externalJobPostingId" in captura.value.campos


class RespostaRequestsFalsa:
    """Resposta mínima para verificar os argumentos enviados ao requests."""

    status_code = 200
    content = b'{"ok": true}'
    text = '{"ok": true}'
    headers = {"X-Request-ID": "REQ-TRANSPORTE"}

    @staticmethod
    def json() -> dict[str, bool]:
        """Devolve um corpo JSON previsível."""

        return {"ok": True}


class SessaoRequestsFalsa:
    """Sessão falsa usada para provar que redirects ficam desabilitados."""

    def __init__(self) -> None:
        self.chamada: dict[str, Any] | None = None
        self.fechada = False

    def post(self, url: str, **argumentos: Any) -> RespostaRequestsFalsa:
        """Registra o POST sem abrir uma conexão."""

        self.chamada = {"url": url, **argumentos}
        return RespostaRequestsFalsa()

    def close(self) -> None:
        """Registra o fechamento da sessão."""

        self.fechada = True


def test_transporte_requests_nao_segue_redirecionamentos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credencial e payload não podem ser encaminhados a outro host."""

    sessao = SessaoRequestsFalsa()
    monkeypatch.setattr(requests, "Session", lambda: sessao)
    transporte = TransporteRequestsEmpregos()

    resposta = transporte.postar(
        url="https://parceiro.example.com/job-postings",
        payload=PAYLOAD_TESTE,
        cabecalhos={"X-API-Key": CHAVE_TESTE},
        timeout_seconds=5,
    )
    transporte.fechar()

    assert resposta.status_http == 200
    assert sessao.chamada is not None
    assert sessao.chamada["allow_redirects"] is False
    assert sessao.chamada["timeout"] == 5
    assert sessao.fechada is True
