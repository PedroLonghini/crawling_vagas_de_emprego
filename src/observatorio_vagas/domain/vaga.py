"""Vaga canônica e valores normalizados para análise."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)


class SalarioNormalizado(ModeloDominio):
    """Salário classificado sem eliminar o valor original do anúncio."""

    # Natureza impede misturar valor publicado com estimativa.
    natureza: NaturezaSalario

    # Moeda usa código de três letras, como BRL ou USD.
    moeda: str = "BRL"

    # Período é essencial para não comparar R$ 50/hora com R$ 50/mês.
    periodo: PeriodoSalario = PeriodoSalario.NAO_INFORMADO

    # Faixa exatamente interpretada da publicação.
    minimo: Decimal | None = Field(default=None, ge=0)
    maximo: Decimal | None = Field(default=None, ge=0)

    # Referência mensal fica separada dos valores originais.
    mensal_minimo: Decimal | None = Field(default=None, ge=0)
    mensal_maximo: Decimal | None = Field(default=None, ge=0)

    # Explica como a conversão foi feita, por exemplo "valor anual / 12".
    regra_normalizacao: str | None = None

    @field_validator("moeda", mode="before")
    @classmethod
    def normalizar_moeda(cls, valor: str) -> str:
        # Guardar em maiúsculas evita BRL, brl e Brl como moedas diferentes.
        moeda = str(valor).strip().upper()
        if len(moeda) != 3 or not moeda.isalpha():
            raise ValueError("moeda deve usar código de três letras")
        return moeda

    @model_validator(mode="after")
    def validar_faixas(self) -> SalarioNormalizado:
        # Um objeto de salário sem nenhum valor não possui utilidade.
        if self.minimo is None and self.maximo is None:
            raise ValueError("salário exige pelo menos um valor")
        # Impede faixas matematicamente invertidas.
        if self.minimo is not None and self.maximo is not None and self.minimo > self.maximo:
            raise ValueError("salário mínimo não pode ser maior que o máximo")
        if (
            self.mensal_minimo is not None
            and self.mensal_maximo is not None
            and self.mensal_minimo > self.mensal_maximo
        ):
            raise ValueError("salário mensal mínimo não pode ser maior que o máximo")
        # Qualquer valor convertido precisa explicar a regra usada.
        if (self.mensal_minimo is not None or self.mensal_maximo is not None) and not (
            self.regra_normalizacao
        ):
            raise ValueError("valor mensal exige regra_normalizacao")
        return self


class VagaCanonica(ModeloDominio):
    """Oportunidade consolidada que pode reunir anúncios de várias fontes."""

    # A vaga canônica tem identidade própria e pertence a uma empresa canônica.
    id: UUID = Field(default_factory=uuid4)
    empresa_id: UUID

    # Classificações usadas nas análises e filtros.
    # Título padronizado usado em filtros e comparações.
    titulo_normalizado: TextoObrigatorio

    # Descrição limpa e preparada a partir do anúncio original.
    #
    # Ela continua opcional porque podemos receber anúncios
    # incompletos. O relatório de prontidão verificará isso.
    descricao_normalizada: str | None = None

    # Classificações profissionais.
    familia_cargo: str | None = None
    area: str | None = None
    senioridade: Senioridade = Senioridade.NAO_INFORMADA
    # Localização normalizada.
    cidade: str | None = None
    estado: str | None = None
    pais: str = "BR"

    # Endereço e CEP preparados para consulta e publicação.
    endereco: str | None = None
    cep: str | None = None

    # Coordenadas geográficas.
    #
    # Latitude varia entre -90 e 90.
    # Longitude varia entre -180 e 180.
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
    )
    modalidade: ModalidadeTrabalho = ModalidadeTrabalho.NAO_INFORMADO
    regime: RegimeContratacao = RegimeContratacao.NAO_INFORMADO

    # Salário pode ser ausente porque muitas vagas não divulgam valor.
    salario: SalarioNormalizado | None = None

    # Quantidade de pessoas que a empresa pretende contratar.
    #
    # Quando informada, precisa ser pelo menos 1.
    quantidade_vagas: int | None = Field(
        default=None,
        ge=1,
    )

    # Tuplas evitam alterações acidentais.
    #
    # Elas também permitem remover informações duplicadas
    # durante a criação do objeto.
    tecnologias: tuple[str, ...] = ()
    requisitos: tuple[str, ...] = ()
    responsabilidades: tuple[str, ...] = ()
    beneficios: tuple[str, ...] = ()
    criado_em: DataHora = Field(default_factory=agora_utc)
    atualizado_em: DataHora = Field(default_factory=agora_utc)

    @field_validator(
        "tecnologias",
        "requisitos",
        "responsabilidades",
        "beneficios",
        mode="before",
    )
    @classmethod
    def limpar_lista_textual(cls, valores: object) -> tuple[str, ...]:
        # Fontes podem entregar uma string, lista ou valor ausente.
        if valores is None:
            return ()
        if isinstance(valores, str):
            valores = (valores,)

        # Remove duplicados ignorando caixa, mas preserva a primeira grafia.
        unicos: list[str] = []
        encontrados: set[str] = set()
        for valor in valores:  # type: ignore[union-attr]
            item = str(valor).strip()
            chave = item.casefold()
            if item and chave not in encontrados:
                unicos.append(item)
                encontrados.add(chave)
        return tuple(unicos)

    @model_validator(mode="after")
    def validar_consistencia(self) -> VagaCanonica:
        """Valida datas e coordenadas da vaga."""

        # Mantém a cronologia interna coerente.
        if self.atualizado_em < self.criado_em:
            raise ValueError("atualizado_em não pode ser anterior a criado_em")

        # Latitude e longitude precisam aparecer juntas.
        #
        # Uma coordenada sem a outra não permite localizar
        # corretamente a vaga em um mapa.
        latitude_ausente = self.latitude is None
        longitude_ausente = self.longitude is None

        if latitude_ausente != longitude_ausente:
            raise ValueError("latitude e longitude devem ser informadas juntas")

        return self
