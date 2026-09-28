"""Observações imutáveis e alterações detectadas nos anúncios."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    ObjetoJson,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import StatusAnuncio


class AlteracaoCampo(ModeloDominio):
    """Diferença observada em um único campo do anúncio."""

    campo: TextoObrigatorio

    # Any é usado porque um campo pode ser texto, número, lista ou objeto.
    valor_anterior: Any = None
    valor_atual: Any = None

    @model_validator(mode="after")
    def validar_mudanca(self) -> AlteracaoCampo:
        # Valores iguais não representam uma alteração real.
        if self.valor_anterior == self.valor_atual:
            raise ValueError("alteração exige valores diferentes")
        return self


class ObservacaoAnuncio(ModeloDominio):
    """Snapshot de um anúncio em uma execução específica."""

    # Liga a observação ao anúncio e à execução que a produziu.
    id: UUID = Field(default_factory=uuid4)
    anuncio_id: UUID
    execucao_coleta_id: UUID
    observado_em: DataHora = Field(default_factory=agora_utc)
    # Estado observado naquele momento, não apenas o estado atual.
    status: StatusAnuncio

    # Hash permite saber rapidamente se o conteúdo mudou.
    hash_conteudo: str

    # Snapshot preserva os valores daquele momento para reconstruir o passado.
    snapshot: ObjetoJson

    # Alterações explicam exatamente o que mudou desde a observação anterior.
    alteracoes: tuple[AlteracaoCampo, ...] = ()

    # Referência permite recuperar a resposta original usada nessa decisão.
    referencia_bruta: TextoObrigatorio

    @field_validator("hash_conteudo", mode="before")
    @classmethod
    def validar_hash(cls, valor: str) -> str:
        normalizado = str(valor).strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", normalizado):
            raise ValueError("hash_conteudo deve ser um SHA-256 hexadecimal")
        return normalizado

    @model_validator(mode="after")
    def validar_campos_unicos(self) -> ObservacaoAnuncio:
        # Repetir o mesmo campo geraria um histórico contraditório.
        campos = [alteracao.campo for alteracao in self.alteracoes]
        if len(campos) != len(set(campos)):
            raise ValueError("uma observação não pode repetir o mesmo campo alterado")
        return self

    @property
    def houve_alteracao(self) -> bool:
        # Propriedade de conveniência para consultas e indicadores.
        return bool(self.alteracoes)
