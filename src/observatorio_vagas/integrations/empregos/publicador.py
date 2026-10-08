"""Coordenação segura entre o cliente HTTP e o histórico MongoDB."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from observatorio_vagas.domain.assinatura_vaga import identidade_de_payload
from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import (
    OperacaoPublicacaoEmpregos,
    encontrar_publicacao_da_mesma_vaga,
)
from observatorio_vagas.integrations.empregos.client import (
    AutenticacaoEmpregosRecusada,
    RequisicaoEmpregosRecusada,
    ResultadoEmpregosIndeterminado,
    ResultadoEnvioEmpregos,
    SituacaoEnvioEmpregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
)
from observatorio_vagas.storage.contracts import (
    RepositorioPublicacoesEmpregos,
)


class ClientePublicacaoEmpregos(Protocol):
    """Parte do cliente necessária ao coordenador persistente."""

    def validar_configuracao_publicacao(self) -> None:
        """Valida endpoint, ambiente, kill switch e credencial."""

        ...

    def publicar(
        self,
        preparacao: ResultadoPreparacaoEmpregos,
        *,
        confirmar_publicacao: bool = False,
    ) -> ResultadoEnvioEmpregos:
        """Simula ou realiza exatamente uma requisição."""

        ...


class OperacaoPublicacaoEmpregosBloqueada(RuntimeError):
    """Impede repetição automática de uma operação já processada."""

    def __init__(self, operacao: OperacaoPublicacaoEmpregos) -> None:
        self.operacao = operacao
        super().__init__(
            "a operação de publicação já está "
            f"no estado {operacao.situacao.value} e exige reconciliação"
        )


class VagaJaPublicadaPorOutraFonte(RuntimeError):
    """A mesma vaga já está no ar no Empregos, publicada a partir de outro anúncio."""

    def __init__(self, publicada: OperacaoPublicacaoEmpregos) -> None:
        self.publicada = publicada
        super().__init__(
            "a mesma vaga já foi publicada a partir de outro anúncio "
            f"({publicada.anuncio_id}, estado {publicada.situacao.value}); nada foi enviado"
        )


@dataclass(frozen=True, slots=True)
class ResultadoPublicacaoEmpregosPersistida:
    """Reúne o resultado HTTP e o registro auditável correspondente."""

    envio: ResultadoEnvioEmpregos
    operacao: OperacaoPublicacaoEmpregos | None
    reutilizada: bool = False

    @property
    def simulada(self) -> bool:
        """Indica que nada foi enviado nem gravado."""

        return self.operacao is None


class PublicadorEmpregos:
    """Garante que o histórico seja persistido antes e depois do POST."""

    def __init__(
        self,
        cliente: ClientePublicacaoEmpregos,
        repositorio: RepositorioPublicacoesEmpregos,
    ) -> None:
        self._cliente = cliente
        self._repositorio = repositorio

    def publicar(
        self,
        preparacao: ResultadoPreparacaoEmpregos,
        *,
        vaga_id: UUID,
        anuncio_id: UUID,
        confirmar_publicacao: bool = False,
    ) -> ResultadoPublicacaoEmpregosPersistida:
        """Simula por padrão ou executa uma publicação persistida."""

        simulacao = self._cliente.publicar(
            preparacao,
            confirmar_publicacao=False,
        )

        if not confirmar_publicacao:
            return ResultadoPublicacaoEmpregosPersistida(
                envio=simulacao,
                operacao=None,
            )

        # Configurações são verificadas antes de registrar que o HTTP começou.
        self._cliente.validar_configuracao_publicacao()

        identidade = identidade_de_payload(
            preparacao.payload,
            dominio=(
                preparacao.proveniencia_fonte.dominio
                if preparacao.proveniencia_fonte is not None
                else None
            ),
        )
        preparada = OperacaoPublicacaoEmpregos(
            vaga_id=vaga_id,
            anuncio_id=anuncio_id,
            external_job_posting_id=simulacao.external_job_posting_id,
            operation_type=simulacao.operation_type,
            payload_sha256=simulacao.payload_sha256,
            assinatura_conteudo=identidade.assinatura if identidade else None,
            assinatura_descricao_completa=identidade.assinatura_completa if identidade else None,
            dominio_origem=identidade.dominio if identidade else None,
        )
        # Conferir e reservar sob a trava: duas execuções simultâneas não
        # conseguem, cada uma, não ver a outra e publicar a mesma vaga.
        with self._repositorio.trava_publicacao():
            # Última barreira contra a mesma vaga vinda de outro site: vale também
            # para quem publica sem passar pela fila (scripts/publicar_vaga_empregos.py).
            if identidade is not None:
                publicada = encontrar_publicacao_da_mesma_vaga(
                    self._repositorio.listar_ativas_por_assinatura(identidade.assinatura),
                    identidade=identidade,
                    external_job_posting_id=simulacao.external_job_posting_id,
                )
                if publicada is not None:
                    raise VagaJaPublicadaPorOutraFonte(publicada)
            reserva = self._repositorio.reservar(preparada)
        operacao = reserva.operacao

        if operacao.situacao is SituacaoPublicacaoEmpregos.SUCESSO:
            return ResultadoPublicacaoEmpregosPersistida(
                envio=self._resultado_do_historico(operacao),
                operacao=operacao,
                reutilizada=True,
            )

        if operacao.situacao is not SituacaoPublicacaoEmpregos.PREPARADA:
            raise OperacaoPublicacaoEmpregosBloqueada(operacao)

        enviando = operacao.iniciar_envio()
        enviando = self._repositorio.salvar_transicao(
            enviando,
            situacao_anterior=SituacaoPublicacaoEmpregos.PREPARADA,
        )

        try:
            resultado = self._cliente.publicar(
                preparacao,
                confirmar_publicacao=True,
            )
        except (AutenticacaoEmpregosRecusada, RequisicaoEmpregosRecusada) as erro:
            rejeitada = enviando.concluir(
                situacao=SituacaoPublicacaoEmpregos.REJEITADA,
                status_http=erro.status_http,
                erro_resumido=str(erro),
            )
            self._repositorio.salvar_transicao(
                rejeitada,
                situacao_anterior=SituacaoPublicacaoEmpregos.ENVIANDO,
            )
            raise
        except ResultadoEmpregosIndeterminado as erro:
            indeterminada = enviando.concluir(
                situacao=SituacaoPublicacaoEmpregos.INDETERMINADA,
                status_http=erro.status_http,
                erro_resumido=str(erro),
            )
            self._repositorio.salvar_transicao(
                indeterminada,
                situacao_anterior=SituacaoPublicacaoEmpregos.ENVIANDO,
            )
            raise
        except Exception:
            indeterminada = enviando.concluir(
                situacao=SituacaoPublicacaoEmpregos.INDETERMINADA,
                erro_resumido="Falha inesperada durante o envio.",
            )
            self._repositorio.salvar_transicao(
                indeterminada,
                situacao_anterior=SituacaoPublicacaoEmpregos.ENVIANDO,
            )
            raise

        sucesso = enviando.concluir(
            situacao=SituacaoPublicacaoEmpregos.SUCESSO,
            status_http=resultado.status_http,
            request_id=resultado.request_id,
        )
        sucesso = self._repositorio.salvar_transicao(
            sucesso,
            situacao_anterior=SituacaoPublicacaoEmpregos.ENVIANDO,
        )

        return ResultadoPublicacaoEmpregosPersistida(
            envio=resultado,
            operacao=sucesso,
        )

    @staticmethod
    def _resultado_do_historico(
        operacao: OperacaoPublicacaoEmpregos,
    ) -> ResultadoEnvioEmpregos:
        """Reconstrói um resultado mínimo sem repetir o POST."""

        if operacao.status_http is None:
            raise ValueError("uma operação com sucesso não possui status HTTP")

        return ResultadoEnvioEmpregos(
            situacao=SituacaoEnvioEmpregos.SUCESSO,
            external_job_posting_id=operacao.external_job_posting_id,
            operation_type=operacao.operation_type,
            payload_sha256=operacao.payload_sha256,
            tentativas=operacao.tentativas,
            status_http=operacao.status_http,
            request_id=operacao.request_id,
        )
