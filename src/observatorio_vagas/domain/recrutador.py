"""Entidade responsável pelos recrutadores das empresas."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    TextoObrigatorio,
    agora_utc,
)


class Recrutador(ModeloDominio):
    """Pessoa responsável pela publicação e gestão das vagas.

    Uma empresa pode possuir vários recrutadores.

    O recrutador fica separado de Empresa para não precisarmos
    repetir todos os dados da empresa para cada pessoa.
    """

    # Identificador interno criado pelo nosso sistema.
    #
    # Não dependemos do identificador do Empregos ou de outra fonte.
    id: UUID = Field(default_factory=uuid4)

    # Identificador da empresa à qual o recrutador pertence.
    empresa_id: UUID

    # Identificador utilizado por algum sistema externo.
    #
    # Ele é opcional porque podemos encontrar nome e e-mail antes
    # de receber o identificador oficial do parceiro.
    #
    # O limite de 50 caracteres acompanha a documentação da API.
    id_externo: str | None = Field(
        default=None,
        max_length=50,
    )

    # Nome da pessoa responsável.
    #
    # O limite acompanha o contrato de provisionamento da API.
    nome: TextoObrigatorio = Field(max_length=200)

    # Endereço de e-mail profissional.
    #
    # A API aceita até 255 caracteres.
    email: TextoObrigatorio = Field(max_length=255)

    # Um recrutador desativado permanece no histórico,
    # mas não deve ser usado em novas publicações.
    ativo: bool = True

    # Datas utilizadas para auditoria.
    criado_em: DataHora = Field(default_factory=agora_utc)
    atualizado_em: DataHora = Field(default_factory=agora_utc)

    @field_validator("id_externo", mode="before")
    @classmethod
    def limpar_id_externo(
        cls,
        valor: object,
    ) -> str | None:
        """Transforma identificadores vazios em ausência de valor."""

        # None significa que ainda não conhecemos o identificador.
        if valor is None:
            return None

        # Convertemos para texto e removemos espaços.
        texto = str(valor).strip()

        # Uma string vazia não é um identificador válido.
        #
        # Guardamos None para representar a informação ausente.
        return texto or None

    @field_validator("email", mode="before")
    @classmethod
    def normalizar_email(
        cls,
        valor: object,
    ) -> str:
        """Normaliza o e-mail e verifica sua estrutura básica."""

        # Retiramos espaços e convertemos para minúsculas.
        email = str(valor).strip().lower()

        # Um e-mail precisa possuir exatamente um símbolo @.
        if email.count("@") != 1:
            raise ValueError("e-mail inválido")

        # Separamos o texto antes e depois do @.
        usuario, dominio = email.split("@")

        # As duas partes precisam estar preenchidas.
        if not usuario or not dominio:
            raise ValueError("e-mail inválido")

        # E-mails corporativos normalmente precisam possuir
        # um domínio como empresa.com ou empresa.com.br.
        if "." not in dominio:
            raise ValueError("e-mail inválido")

        # O ponto não pode ser o primeiro nem o último
        # caractere do domínio.
        if dominio.startswith(".") or dominio.endswith("."):
            raise ValueError("e-mail inválido")

        return email

    @model_validator(mode="after")
    def validar_datas(self) -> Recrutador:
        """Impede uma sequência de datas impossível."""

        if self.atualizado_em < self.criado_em:
            raise ValueError("atualizado_em não pode ser anterior a criado_em")

        return self
