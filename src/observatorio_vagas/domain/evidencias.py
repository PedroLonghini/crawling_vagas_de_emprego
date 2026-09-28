"""Proveniência das informações extraídas ou inferidas das vagas."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from observatorio_vagas.domain.common import (
    Confianca,
    DataHora,
    ModeloDominio,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import MetodoExtracao

_METODOS_INFERIDOS = {
    # Estes métodos interpretam texto; por isso precisam guardar o trecho que
    # sustentou a conclusão.
    MetodoExtracao.REGRA,
    MetodoExtracao.CLASSIFICADOR,
    MetodoExtracao.MODELO_LINGUAGEM,
}


class EvidenciaExtracao(ModeloDominio):
    """Explica de onde veio um campo estruturado ou normalizado."""

    # Liga a evidência ao anúncio original.
    id: UUID = Field(default_factory=uuid4)
    anuncio_id: UUID

    # Exemplo: campo="modalidade", valor_extraido="hibrido".
    campo: TextoObrigatorio
    valor_extraido: Any

    # Método, confiança e versão tornam a extração auditável.
    metodo: MetodoExtracao
    confianca: Confianca
    # Trecho da descrição que comprova ou sustenta a conclusão.
    trecho_evidencia: str | None = None
    versao_extrator: TextoObrigatorio
    versao_taxonomia: str | None = None
    criado_em: DataHora = Field(default_factory=agora_utc)
    # Uma pessoa pode confirmar ou corrigir a extração posteriormente.
    revisado_por: str | None = None
    revisado_em: DataHora | None = None

    @model_validator(mode="after")
    def validar_proveniencia(self) -> EvidenciaExtracao:
        # Inferência sem trecho seria uma conclusão que ninguém consegue
        # conferir depois.
        if self.metodo in _METODOS_INFERIDOS and not self.trecho_evidencia:
            raise ValueError("métodos inferidos exigem trecho_evidencia")
        # Mantém autoria e momento da revisão sempre completos.
        if bool(self.revisado_por) != bool(self.revisado_em):
            raise ValueError("revisado_por e revisado_em devem ser informados juntos")
        return self
