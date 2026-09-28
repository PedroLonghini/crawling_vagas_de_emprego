"""Entidades relacionadas à identidade canônica das empresas."""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import Field, HttpUrl, field_validator, model_validator

from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    ObjetoJson,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import Fonte


def _normalizar_cnpj(valor: str | None) -> str | None:
    """Remove pontuação e valida a estrutura básica do CNPJ.

    O checksum completo ficará em um serviço específico. Aqui garantimos o
    formato necessário para que o identificador seja armazenado e comparado.
    """

    # Algumas fontes não informam CNPJ. None é diferente de CNPJ inválido.
    if valor is None:
        return None

    # CNPJ é texto, não número. Isso preserva zeros iniciais e aceita o novo
    # formato alfanumérico nas doze primeiras posições.
    normalizado = re.sub(r"[^A-Za-z0-9]", "", valor).upper()

    # Os dois últimos caracteres continuam sendo dígitos verificadores.
    if len(normalizado) != 14 or not normalizado[-2:].isdigit():
        raise ValueError("CNPJ deve possuir 14 posições e dois dígitos verificadores numéricos")
    return normalizado


class EvidenciaCadastralEmpresa(ModeloDominio):
    """Comprova a origem de um dado cadastral da empresa."""

    campo: TextoObrigatorio
    valor_extraido: TextoObrigatorio
    url_fonte: HttpUrl
    referencia_bruta: TextoObrigatorio
    hash_conteudo: TextoObrigatorio
    pagina: int | None = None
    trecho_evidencia: TextoObrigatorio
    coletado_em: DataHora

    @field_validator("hash_conteudo")
    @classmethod
    def validar_hash_conteudo(cls, valor: str) -> str:
        """Exige um SHA-256 hexadecimal completo."""

        normalizado = valor.strip().casefold()

        if re.fullmatch(r"[0-9a-f]{64}", normalizado) is None:
            raise ValueError("hash_conteudo deve ser um SHA-256 hexadecimal")

        return normalizado

    @field_validator("pagina")
    @classmethod
    def validar_pagina(
        cls,
        valor: int | None,
    ) -> int | None:
        """Aceita ausência ou um número de página positivo."""

        if valor is None:
            return None

        if isinstance(valor, bool) or valor < 1:
            raise ValueError("pagina precisa ser um inteiro positivo")

        return valor


class Empresa(ModeloDominio):
    """Empresa consolidada independentemente de como aparece nas fontes."""

    # UUID interno evita depender do ID de uma fonte externa.
    id: UUID = Field(default_factory=uuid4)

    # Razão social é obrigatória; nome fantasia é opcional.
    razao_social: TextoObrigatorio
    nome_fantasia: str | None = None

    # Nomes alternativos ajudam a resolver "WEG", "WEG S.A." etc.
    nomes_alternativos: tuple[str, ...] = ()

    # CNPJ e domínio são importantes para deduplicar empresas.
    cnpj: str | None = None
    dominio: str | None = None

    # Endereço da página utilizada para encontrar oportunidades.
    pagina_carreiras: HttpUrl | None = None

    # Endereço do site institucional.
    site: HttpUrl | None = None

    # Endereço público do logotipo.
    logo_url: HttpUrl | None = None

    # Descrição institucional da empresa.
    descricao: str | None = None

    # Informações cadastrais e analíticas.
    setor: str | None = None
    cnae: str | None = None
    porte: str | None = None
    cidade: str | None = None
    estado: str | None = None
    pais: str = "BR"
    # Evidências cadastrais preservam a origem de CNPJ,
    # CNAE, setor e outros dados enriquecidos.
    evidencias_cadastrais: tuple[
        EvidenciaCadastralEmpresa,
        ...,
    ] = ()

    # Datas permitem auditar quando o cadastro surgiu e foi atualizado.
    criado_em: DataHora = Field(default_factory=agora_utc)
    atualizado_em: DataHora = Field(default_factory=agora_utc)

    @field_validator("cnpj", mode="before")
    @classmethod
    def normalizar_cnpj(cls, valor: str | None) -> str | None:
        return _normalizar_cnpj(valor)

    @field_validator("dominio", mode="before")
    @classmethod
    def normalizar_dominio(cls, valor: str | None) -> str | None:
        # Domínio vazio significa informação ausente, não erro.
        if valor is None or not str(valor).strip():
            return None

        # Aceitamos entrada com ou sem https://, mas guardamos apenas o domínio.
        texto = str(valor).strip().lower()
        parsed = urlsplit(texto if "://" in texto else f"https://{texto}")
        if not parsed.hostname:
            raise ValueError("Domínio inválido")

        # Um domínio canônico não pode conter /vagas, parâmetros ou fragmentos.
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Domínio não deve conter caminho, consulta ou fragmento")

        # www.exemplo.com e exemplo.com devem apontar para a mesma empresa.
        hostname = parsed.hostname.removeprefix("www.")
        return hostname

    @field_validator("nomes_alternativos", mode="before")
    @classmethod
    def limpar_nomes_alternativos(cls, valores: object) -> tuple[str, ...]:
        # Transforma ausência em tupla vazia para evitar verificações repetidas.
        if valores is None:
            return ()

        # Uma única string também é aceita por conveniência.
        if isinstance(valores, str):
            valores = (valores,)

        # Remove vazios e duplicados sem perder a grafia da primeira ocorrência.
        unicos: list[str] = []
        encontrados: set[str] = set()
        for valor in valores:  # type: ignore[union-attr]
            nome = str(valor).strip()
            chave = nome.casefold()
            if nome and chave not in encontrados:
                unicos.append(nome)
                encontrados.add(chave)
        return tuple(unicos)

    @model_validator(mode="after")
    def validar_datas(self) -> Empresa:
        # Uma atualização não pode acontecer antes da criação do cadastro.
        if self.atualizado_em < self.criado_em:
            raise ValueError("atualizado_em não pode ser anterior a criado_em")
        return self

    @property
    def nome_exibicao(self) -> str:
        # O usuário prefere nome fantasia; razão social funciona como fallback.
        return self.nome_fantasia or self.razao_social


class EmpresaFonte(ModeloDominio):
    """Identidade usada por uma empresa em uma fonte específica."""

    # ID desta associação e ID da empresa canônica.
    id: UUID = Field(default_factory=uuid4)
    empresa_id: UUID

    # Dados que identificam a mesma empresa dentro da fonte externa.
    fonte: Fonte
    id_externo: str | None = None
    nome_na_fonte: str | None = None
    url_na_fonte: HttpUrl | None = None

    # Desativar uma identidade preserva histórico sem continuar coletando-a.
    ativa: bool = True

    # Metadados preservam informações específicas que ainda não têm coluna.
    metadados: ObjetoJson = Field(default_factory=dict)
    criado_em: DataHora = Field(default_factory=agora_utc)
    atualizado_em: DataHora = Field(default_factory=agora_utc)

    @model_validator(mode="after")
    def validar_identidade(self) -> EmpresaFonte:
        # Sem ID e sem URL, não existe maneira de reencontrar essa identidade.
        if not self.id_externo and not self.url_na_fonte:
            raise ValueError("EmpresaFonte exige id_externo ou url_na_fonte")
        if self.atualizado_em < self.criado_em:
            raise ValueError("atualizado_em não pode ser anterior a criado_em")
        return self
