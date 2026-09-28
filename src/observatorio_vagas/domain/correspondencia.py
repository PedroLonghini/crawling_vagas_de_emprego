"""Correspondência explicável entre anúncios de fontes diferentes."""

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
from observatorio_vagas.domain.enums import ClassificacaoCorrespondencia


class SinalCorrespondencia(ModeloDominio):
    """Uma evidência usada para decidir se dois anúncios são a mesma vaga."""

    # Exemplo de nome: titulo, empresa, localidade ou descricao.
    nome: TextoObrigatorio

    # Similaridade mede o quanto os valores combinam; peso mede a importância.
    similaridade: Confianca
    peso: Confianca

    # Valores são guardados para tornar a decisão explicável.
    valor_origem: Any = None
    valor_destino: Any = None

    @property
    def contribuicao(self) -> float:
        # Um sinal perfeito com peso 0.5 contribui 0.5 para a decisão.
        return self.similaridade * self.peso


class CorrespondenciaAnuncios(ModeloDominio):
    """Decisão versionada sobre dois anúncios potencialmente equivalentes."""

    # IDs dos dois anúncios comparados.
    id: UUID = Field(default_factory=uuid4)
    anuncio_origem_id: UUID
    anuncio_destino_id: UUID
    # Resultado final entre zero e um e sua classificação de negócio.
    pontuacao: Confianca
    classificacao: ClassificacaoCorrespondencia
    sinais: tuple[SinalCorrespondencia, ...]
    # A versão é essencial: mudanças futuras não podem apagar como uma decisão
    # antiga foi produzida.
    versao_algoritmo: TextoObrigatorio
    criado_em: DataHora = Field(default_factory=agora_utc)
    # Revisão humana é opcional, mas responsável e data precisam andar juntos.
    revisado_por: str | None = None
    revisado_em: DataHora | None = None
    observacao_revisao: str | None = None

    @model_validator(mode="after")
    def validar_correspondencia(self) -> CorrespondenciaAnuncios:
        # Comparar um registro com ele mesmo produziria uma falsa confirmação.
        if self.anuncio_origem_id == self.anuncio_destino_id:
            raise ValueError("um anúncio não pode ser comparado com ele mesmo")
        # Sem sinais, a pontuação não pode ser explicada ou auditada.
        if not self.sinais:
            raise ValueError("correspondência exige pelo menos um sinal")
        # Impede uma revisão sem autor ou sem momento registrado.
        if bool(self.revisado_por) != bool(self.revisado_em):
            raise ValueError("revisado_por e revisado_em devem ser informados juntos")
        return self
