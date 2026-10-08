"""Testes da coordenação persistente de publicações no Empregos."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from observatorio_vagas.domain.assinatura_vaga import identidade_de_payload
from observatorio_vagas.domain.elegibilidade import (
    ResultadoElegibilidadePublicacao,
)
from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.prontidao import RelatorioProntidao
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.integrations.empregos.client import (
    ConfiguracaoClienteEmpregosInvalida,
    RequisicaoEmpregosRecusada,
    ResultadoEmpregosIndeterminado,
    ResultadoEnvioEmpregos,
    SituacaoEnvioEmpregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
)
from observatorio_vagas.integrations.empregos.publicador import (
    OperacaoPublicacaoEmpregosBloqueada,
    PublicadorEmpregos,
    VagaJaPublicadaPorOutraFonte,
)
from observatorio_vagas.storage.contracts import ResultadoReservaPublicacao

VAGA_ID = UUID("a1000000-0000-4000-8000-0000000000a1")
ANUNCIO_ID = UUID("a2000000-0000-4000-8000-0000000000a2")
PAYLOAD = {
    "company": {
        "applyUrl": "https://candidaturas.example.com/vaga-001",
        "name": "Empresa Exemplo",
        "description": "Descrição institucional.",
        "nationalRegister": "12.345.678/0001-90",
    },
    "externalJobPostingId": "fonte:alvo:VAGA-001",
    "jobPostingOperationType": "CREATE",
    "title": "Pessoa Desenvolvedora Python",
    "description": "Desenvolvimento de aplicações.",
    "location": {"address": "São Paulo, SP"},
}
HASH_PAYLOAD = "a" * 64


def criar_preparacao() -> ResultadoPreparacaoEmpregos:
    """Cria uma preparação já aprovada pelas camadas anteriores."""

    relatorio = cast(
        RelatorioProntidao,
        SimpleNamespace(pronto_para_envio=True),
    )
    return ResultadoPreparacaoEmpregos(
        relatorio=relatorio,
        elegibilidade=ResultadoElegibilidadePublicacao(),
        payload=PAYLOAD,
    )


def criar_resultado(
    situacao: SituacaoEnvioEmpregos,
    *,
    status_http: int | None = None,
) -> ResultadoEnvioEmpregos:
    """Cria um resultado mínimo compatível com o cliente real."""

    return ResultadoEnvioEmpregos(
        situacao=situacao,
        external_job_posting_id="fonte:alvo:VAGA-001",
        operation_type="CREATE",
        payload_sha256=HASH_PAYLOAD,
        tentativas=0 if situacao is SituacaoEnvioEmpregos.SIMULADO else 1,
        status_http=status_http,
        request_id="req-001" if status_http is not None else None,
    )


@dataclass
class ClienteFalso:
    """Simula o cliente sem qualquer acesso à internet."""

    eventos: list[str]
    resultado_real: ResultadoEnvioEmpregos | None = None
    erro_real: Exception | None = None
    erro_configuracao: Exception | None = None
    envios_reais: int = 0

    def validar_configuracao_publicacao(self) -> None:
        self.eventos.append("validar")
        if self.erro_configuracao is not None:
            raise self.erro_configuracao

    def publicar(
        self,
        preparacao: ResultadoPreparacaoEmpregos,
        *,
        confirmar_publicacao: bool = False,
    ) -> ResultadoEnvioEmpregos:
        assert preparacao.payload == PAYLOAD

        if not confirmar_publicacao:
            self.eventos.append("simular")
            return criar_resultado(SituacaoEnvioEmpregos.SIMULADO)

        self.eventos.append("enviar")
        self.envios_reais += 1

        if self.erro_real is not None:
            raise self.erro_real

        assert self.resultado_real is not None
        return self.resultado_real


@dataclass
class RepositorioFalso:
    """Mantém uma única operação em memória e valida as transições."""

    eventos: list[str]
    operacao: OperacaoPublicacaoEmpregos | None = None
    # Publicações de outros anúncios já no ar (mesma vaga vinda de outro site).
    outras_publicadas: tuple[OperacaoPublicacaoEmpregos, ...] = ()
    travas_obtidas: int = 0
    dentro_da_trava: bool = False

    @contextmanager
    def trava_publicacao(self) -> Iterator[None]:
        self.travas_obtidas += 1
        self.dentro_da_trava = True
        try:
            yield
        finally:
            self.dentro_da_trava = False

    def reservar(
        self,
        operacao: OperacaoPublicacaoEmpregos,
    ) -> ResultadoReservaPublicacao:
        # Conferir e reservar precisam acontecer sob a trava.
        assert self.dentro_da_trava
        self.eventos.append("reservar")

        if self.operacao is None:
            self.operacao = operacao
            return ResultadoReservaPublicacao(operacao=operacao, criada=True)

        return ResultadoReservaPublicacao(operacao=self.operacao, criada=False)

    def salvar_transicao(
        self,
        operacao: OperacaoPublicacaoEmpregos,
        *,
        situacao_anterior: SituacaoPublicacaoEmpregos,
    ) -> OperacaoPublicacaoEmpregos:
        assert self.operacao is not None
        assert self.operacao.situacao is situacao_anterior
        self.eventos.append(f"{situacao_anterior.value}->{operacao.situacao.value}")
        self.operacao = operacao
        return operacao

    def buscar_por_id(self, operacao_id: UUID) -> OperacaoPublicacaoEmpregos | None:
        if self.operacao is not None and self.operacao.id == operacao_id:
            return self.operacao
        return None

    def buscar_por_chave(self, chave_idempotencia: str) -> OperacaoPublicacaoEmpregos | None:
        if self.operacao is not None and self.operacao.chave_idempotencia == chave_idempotencia:
            return self.operacao
        return None

    def listar_ativas_por_assinatura(
        self, assinatura_conteudo: str
    ) -> list[OperacaoPublicacaoEmpregos]:
        return [
            operacao
            for operacao in (self.operacao, *self.outras_publicadas)
            if operacao is not None and operacao.assinatura_conteudo == assinatura_conteudo
        ]

    def listar_por_vaga(
        self,
        vaga_id: UUID,
        limite: int = 100,
    ) -> list[OperacaoPublicacaoEmpregos]:
        if self.operacao is not None and self.operacao.vaga_id == vaga_id and limite > 0:
            return [self.operacao]
        return []


def criar_publicador(
    *,
    resultado_real: ResultadoEnvioEmpregos | None = None,
    erro_real: Exception | None = None,
    erro_configuracao: Exception | None = None,
) -> tuple[PublicadorEmpregos, ClienteFalso, RepositorioFalso, list[str]]:
    """Monta o coordenador e suas dependências observáveis."""

    eventos: list[str] = []
    cliente = ClienteFalso(
        eventos,
        resultado_real=resultado_real,
        erro_real=erro_real,
        erro_configuracao=erro_configuracao,
    )
    repositorio = RepositorioFalso(eventos)
    return PublicadorEmpregos(cliente, repositorio), cliente, repositorio, eventos


def test_publicacao_grava_a_assinatura_de_conteudo() -> None:
    """Sem a assinatura gravada, a cópia de outro site não seria reconhecida depois."""

    publicador, _, repositorio, _ = criar_publicador(
        resultado_real=criar_resultado(SituacaoEnvioEmpregos.SUCESSO, status_http=201)
    )
    publicador.publicar(
        criar_preparacao(), vaga_id=VAGA_ID, anuncio_id=ANUNCIO_ID, confirmar_publicacao=True
    )

    identidade = identidade_de_payload(PAYLOAD, dominio=None)
    assert repositorio.operacao is not None
    assert repositorio.operacao.assinatura_conteudo == identidade.assinatura
    assert repositorio.operacao.assinatura_descricao_completa == identidade.assinatura_completa


def test_mesma_vaga_ja_publicada_por_outro_anuncio_nao_e_enviada() -> None:
    """Última barreira: vale também para quem publica sem passar pela fila."""

    publicador, cliente, repositorio, eventos = criar_publicador(
        resultado_real=criar_resultado(SituacaoEnvioEmpregos.SUCESSO, status_http=201)
    )
    identidade = identidade_de_payload(PAYLOAD, dominio=None)
    repositorio.outras_publicadas = (
        OperacaoPublicacaoEmpregos(
            vaga_id=UUID("b1000000-0000-4000-8000-0000000000b1"),
            anuncio_id=UUID("b2000000-0000-4000-8000-0000000000b2"),
            external_job_posting_id="outro-site:VAGA-9",
            operation_type="CREATE",
            payload_sha256="b" * 64,
            assinatura_conteudo=identidade.assinatura,
        ),
    )

    with pytest.raises(VagaJaPublicadaPorOutraFonte):
        publicador.publicar(
            criar_preparacao(), vaga_id=VAGA_ID, anuncio_id=ANUNCIO_ID, confirmar_publicacao=True
        )

    assert cliente.envios_reais == 0
    assert "reservar" not in eventos
    # A conferência aconteceu sob a trava, e ela foi liberada depois do erro.
    assert repositorio.travas_obtidas == 1
    assert repositorio.dentro_da_trava is False


def test_simulacao_nao_grava_historico() -> None:
    """O comportamento padrão continua sem rede e sem MongoDB."""

    publicador, cliente, repositorio, eventos = criar_publicador()
    resultado = publicador.publicar(
        criar_preparacao(),
        vaga_id=VAGA_ID,
        anuncio_id=ANUNCIO_ID,
    )

    assert resultado.simulada is True
    assert cliente.envios_reais == 0
    assert repositorio.operacao is None
    assert repositorio.travas_obtidas == 0
    assert eventos == ["simular"]


def test_publicacao_persiste_antes_e_depois_do_post() -> None:
    """O estado enviando precisa existir antes da chamada externa."""

    publicador, cliente, repositorio, eventos = criar_publicador(
        resultado_real=criar_resultado(SituacaoEnvioEmpregos.SUCESSO, status_http=201)
    )
    resultado = publicador.publicar(
        criar_preparacao(),
        vaga_id=VAGA_ID,
        anuncio_id=ANUNCIO_ID,
        confirmar_publicacao=True,
    )

    assert resultado.operacao is not None
    assert resultado.operacao.situacao is SituacaoPublicacaoEmpregos.SUCESSO
    assert cliente.envios_reais == 1
    assert repositorio.operacao == resultado.operacao
    assert eventos == [
        "simular",
        "validar",
        "reservar",
        "preparada->enviando",
        "enviar",
        "enviando->sucesso",
    ]


def test_sucesso_existente_nao_repete_post() -> None:
    """Uma segunda execução devolve o histórico de sucesso."""

    publicador, cliente, _, _ = criar_publicador(
        resultado_real=criar_resultado(SituacaoEnvioEmpregos.SUCESSO, status_http=200)
    )
    primeira = publicador.publicar(
        criar_preparacao(),
        vaga_id=VAGA_ID,
        anuncio_id=ANUNCIO_ID,
        confirmar_publicacao=True,
    )
    segunda = publicador.publicar(
        criar_preparacao(),
        vaga_id=VAGA_ID,
        anuncio_id=ANUNCIO_ID,
        confirmar_publicacao=True,
    )

    assert primeira.reutilizada is False
    assert segunda.reutilizada is True
    assert segunda.envio.sucesso is True
    assert cliente.envios_reais == 1


def test_rejeicao_e_persistida_e_propaga_erro() -> None:
    """Uma resposta permanente 4xx termina como rejeitada."""

    erro = RequisicaoEmpregosRecusada(
        "dados recusados",
        status_http=422,
    )
    publicador, _, repositorio, _ = criar_publicador(erro_real=erro)

    with pytest.raises(RequisicaoEmpregosRecusada):
        publicador.publicar(
            criar_preparacao(),
            vaga_id=VAGA_ID,
            anuncio_id=ANUNCIO_ID,
            confirmar_publicacao=True,
        )

    assert repositorio.operacao is not None
    assert repositorio.operacao.situacao is SituacaoPublicacaoEmpregos.REJEITADA
    assert repositorio.operacao.status_http == 422


def test_resultado_indeterminado_bloqueia_repeticao() -> None:
    """Um timeout persistido nunca gera outro POST automático."""

    erro = ResultadoEmpregosIndeterminado(
        "resultado desconhecido",
        status_http=504,
    )
    publicador, cliente, repositorio, _ = criar_publicador(erro_real=erro)

    with pytest.raises(ResultadoEmpregosIndeterminado):
        publicador.publicar(
            criar_preparacao(),
            vaga_id=VAGA_ID,
            anuncio_id=ANUNCIO_ID,
            confirmar_publicacao=True,
        )

    assert repositorio.operacao is not None
    assert repositorio.operacao.situacao is SituacaoPublicacaoEmpregos.INDETERMINADA

    with pytest.raises(OperacaoPublicacaoEmpregosBloqueada, match="reconciliação"):
        publicador.publicar(
            criar_preparacao(),
            vaga_id=VAGA_ID,
            anuncio_id=ANUNCIO_ID,
            confirmar_publicacao=True,
        )

    assert cliente.envios_reais == 1


def test_configuracao_invalida_falha_antes_da_reserva() -> None:
    """Uma trava fechada não pode deixar operação falsa em envio."""

    publicador, _, repositorio, eventos = criar_publicador(
        erro_configuracao=ConfiguracaoClienteEmpregosInvalida("kill switch fechado")
    )

    with pytest.raises(ConfiguracaoClienteEmpregosInvalida):
        publicador.publicar(
            criar_preparacao(),
            vaga_id=VAGA_ID,
            anuncio_id=ANUNCIO_ID,
            confirmar_publicacao=True,
        )

    assert repositorio.operacao is None
    assert eventos == ["simular", "validar"]
