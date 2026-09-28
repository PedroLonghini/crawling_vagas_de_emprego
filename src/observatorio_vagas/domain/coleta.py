"""Entidades de acompanhamento das execuções de coleta."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    ObjetoJson,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import Fonte, StatusColeta


class MetricasColeta(ModeloDominio):
    """Contadores que explicam o resultado de uma execução."""

    # Cada contador começa em zero e rejeita números negativos.
    requisicoes: int = Field(default=0, ge=0)
    recebidos: int = Field(default=0, ge=0)
    novos: int = Field(default=0, ge=0)
    alterados: int = Field(default=0, ge=0)
    inalterados: int = Field(default=0, ge=0)
    invalidos: int = Field(default=0, ge=0)
    erros: int = Field(default=0, ge=0)

    @property
    def processados(self) -> int:
        # Erros de requisição não entram aqui porque podem ocorrer antes de um
        # registro ser recebido.
        return self.novos + self.alterados + self.inalterados + self.invalidos

    @model_validator(mode="after")
    def validar_totais(self) -> MetricasColeta:
        # Não é possível processar mais registros do que a fonte entregou.
        if self.processados > self.recebidos:
            raise ValueError("registros processados não podem superar recebidos")
        return self


class ExecucaoColeta(ModeloDominio):
    """Execução rastreável de um conector."""

    # Cada execução recebe um UUID independente da fonte.
    id: UUID = Field(default_factory=uuid4)

    # Fonte e versão permitem descobrir qual código gerou os dados.
    fonte: Fonte
    versao_conector: TextoObrigatorio

    # Status e datas permitem monitorar execuções travadas ou incompletas.
    status: StatusColeta = StatusColeta.PENDENTE
    iniciado_em: DataHora = Field(default_factory=agora_utc)
    finalizado_em: DataHora | None = None
    # Checkpoint guarda paginação/cursor para retomar após uma falha.
    checkpoint: ObjetoJson = Field(default_factory=dict)

    # Métricas são armazenadas junto da execução para auditoria.
    metricas: MetricasColeta = Field(default_factory=MetricasColeta)

    # Mensagem resumida ajuda na operação; detalhes completos ficam nos logs.
    erro_resumido: str | None = None

    @model_validator(mode="after")
    def validar_periodo(self) -> ExecucaoColeta:
        # Uma execução não pode terminar antes de começar.
        if self.finalizado_em is not None and self.finalizado_em < self.iniciado_em:
            raise ValueError("finalizado_em não pode ser anterior a iniciado_em")
        return self
