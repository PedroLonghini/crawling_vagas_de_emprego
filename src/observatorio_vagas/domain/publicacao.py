"""Histórico idempotente das publicações destinadas ao Empregos."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from observatorio_vagas.domain.assinatura_vaga import IdentidadeConteudo, mesma_vaga
from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos


def calcular_chave_idempotencia_empregos(
    *,
    external_job_posting_id: str,
    operation_type: str,
) -> str:
    """Identifica uma operação lógica sem depender do conteúdo do payload."""

    partes = (
        "empregos",
        external_job_posting_id.strip(),
        operation_type.strip().upper(),
    )
    texto = "\x1f".join(partes)
    return hashlib.sha256(texto.encode()).hexdigest()


def encontrar_publicacao_da_mesma_vaga(
    publicadas: Iterable[OperacaoPublicacaoEmpregos],
    *,
    identidade: IdentidadeConteudo,
    external_job_posting_id: str,
) -> OperacaoPublicacaoEmpregos | None:
    """Publicação no ar (de outro id) que é a mesma vaga, ou None.

    ``publicadas`` já vem filtrada pela assinatura (começo da descrição); aqui
    se aplica a regra do mesmo site (descrição inteira igual).
    """

    for publicada in publicadas:
        if publicada.external_job_posting_id == external_job_posting_id.strip():
            continue
        if mesma_vaga(
            dominio_a=publicada.dominio_origem,
            assinatura_completa_a=publicada.assinatura_descricao_completa,
            dominio_b=identidade.dominio,
            assinatura_completa_b=identidade.assinatura_completa,
        ):
            return publicada
    return None


class OperacaoPublicacaoEmpregos(ModeloDominio):
    """Registro auditável de uma única operação lógica de publicação."""

    id: UUID = Field(default_factory=uuid4)
    vaga_id: UUID
    anuncio_id: UUID
    external_job_posting_id: TextoObrigatorio
    operation_type: TextoObrigatorio
    payload_sha256: str
    chave_idempotencia: str | None = None
    # Mesma vaga vinda de outro site tem outro external_job_posting_id; a
    # assinatura de conteúdo (domain/assinatura_vaga.py) a reconhece. A
    # completa e o domínio separam "outro site" de "mesmo site".
    assinatura_conteudo: str | None = None
    assinatura_descricao_completa: str | None = None
    dominio_origem: str | None = None

    situacao: SituacaoPublicacaoEmpregos = SituacaoPublicacaoEmpregos.PREPARADA
    tentativas: int = Field(default=0, ge=0)
    status_http: int | None = Field(default=None, ge=100, le=599)
    request_id: str | None = None
    erro_resumido: str | None = None

    criado_em: DataHora = Field(default_factory=agora_utc)
    atualizado_em: DataHora = Field(default_factory=agora_utc)
    enviado_em: DataHora | None = None
    finalizado_em: DataHora | None = None

    @field_validator("operation_type", mode="before")
    @classmethod
    def normalizar_tipo_operacao(cls, valor: Any) -> str:
        """Mantém o tipo no mesmo formato utilizado pelo payload."""

        return str(valor).strip().upper()

    @field_validator(
        "payload_sha256",
        "chave_idempotencia",
        "assinatura_conteudo",
        "assinatura_descricao_completa",
        mode="before",
    )
    @classmethod
    def validar_hash(cls, valor: Any) -> str | None:
        """Aceita somente hashes SHA-256 hexadecimais."""

        if valor is None:
            return None

        normalizado = str(valor).strip().casefold()

        if re.fullmatch(r"[0-9a-f]{64}", normalizado) is None:
            raise ValueError("o valor deve ser um SHA-256 hexadecimal")

        return normalizado

    @model_validator(mode="after")
    def validar_identidade_e_estado(self) -> OperacaoPublicacaoEmpregos:
        """Calcula a chave e impede estados temporais contraditórios."""

        chave_esperada = calcular_chave_idempotencia_empregos(
            external_job_posting_id=self.external_job_posting_id,
            operation_type=self.operation_type,
        )

        if self.chave_idempotencia is None:
            object.__setattr__(self, "chave_idempotencia", chave_esperada)
        elif self.chave_idempotencia != chave_esperada:
            raise ValueError("chave_idempotencia não corresponde à operação")

        if self.atualizado_em < self.criado_em:
            raise ValueError("atualizado_em não pode ser anterior a criado_em")

        for nome, momento in (
            ("enviado_em", self.enviado_em),
            ("finalizado_em", self.finalizado_em),
        ):
            if momento is not None and momento < self.criado_em:
                raise ValueError(f"{nome} não pode ser anterior a criado_em")

        if (
            self.enviado_em is not None
            and self.finalizado_em is not None
            and self.finalizado_em < self.enviado_em
        ):
            raise ValueError("finalizado_em não pode ser anterior a enviado_em")

        if self.situacao is SituacaoPublicacaoEmpregos.PREPARADA:
            if (
                self.tentativas != 0
                or self.enviado_em is not None
                or self.finalizado_em is not None
            ):
                raise ValueError("uma operação preparada ainda não pode possuir tentativa")

            if self.status_http is not None:
                raise ValueError("uma operação preparada não pode possuir status HTTP")

        elif self.situacao is SituacaoPublicacaoEmpregos.ENVIANDO:
            if self.tentativas < 1 or self.enviado_em is None:
                raise ValueError("uma operação em envio exige tentativa e enviado_em")

            if self.finalizado_em is not None:
                raise ValueError("uma operação em envio ainda não pode estar finalizada")

        else:
            if self.tentativas < 1 or self.enviado_em is None or self.finalizado_em is None:
                raise ValueError("uma operação concluída exige tentativa e datas operacionais")

        if self.situacao is SituacaoPublicacaoEmpregos.SUCESSO and (
            self.status_http is None or not 200 <= self.status_http < 300
        ):
            raise ValueError("uma publicação com sucesso exige status HTTP 2xx")

        return self

    def iniciar_envio(
        self,
        *,
        momento: DataHora | None = None,
    ) -> OperacaoPublicacaoEmpregos:
        """Avança uma operação preparada para o estado de envio."""

        if self.situacao is not SituacaoPublicacaoEmpregos.PREPARADA:
            raise ValueError("somente uma operação preparada pode iniciar o envio")

        agora = momento or agora_utc()
        return self._substituir(
            situacao=SituacaoPublicacaoEmpregos.ENVIANDO,
            tentativas=1,
            enviado_em=agora,
            atualizado_em=agora,
        )

    def concluir(
        self,
        *,
        situacao: SituacaoPublicacaoEmpregos,
        momento: DataHora | None = None,
        status_http: int | None = None,
        request_id: str | None = None,
        erro_resumido: str | None = None,
    ) -> OperacaoPublicacaoEmpregos:
        """Finaliza um envio com um resultado conhecido ou indeterminado."""

        if self.situacao is not SituacaoPublicacaoEmpregos.ENVIANDO:
            raise ValueError("somente uma operação em envio pode ser concluída")

        if situacao not in {
            SituacaoPublicacaoEmpregos.SUCESSO,
            SituacaoPublicacaoEmpregos.REJEITADA,
            SituacaoPublicacaoEmpregos.INDETERMINADA,
        }:
            raise ValueError("situação final inválida")

        agora = momento or agora_utc()
        return self._substituir(
            situacao=situacao,
            status_http=status_http,
            request_id=request_id,
            erro_resumido=erro_resumido,
            finalizado_em=agora,
            atualizado_em=agora,
        )

    def _substituir(self, **alteracoes: Any) -> OperacaoPublicacaoEmpregos:
        """Cria uma cópia revalidada depois de uma transição."""

        dados = self.model_dump(mode="python")
        dados.update(alteracoes)
        return type(self).model_validate(dados)
