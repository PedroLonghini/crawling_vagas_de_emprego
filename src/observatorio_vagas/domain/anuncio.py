"""Representação do anúncio exatamente como observado em uma fonte."""

from __future__ import annotations

import re
from datetime import date
from uuid import UUID, uuid4

from pydantic import Field, HttpUrl, field_validator, model_validator

from observatorio_vagas.domain.common import (
    DataHora,
    ModeloDominio,
    ObjetoJson,
    TextoObrigatorio,
    agora_utc,
)
from observatorio_vagas.domain.enums import Fonte, StatusAnuncio


class AnuncioVaga(ModeloDominio):
    """Publicação de uma vaga em uma fonte específica."""

    # ID interno do anúncio. O ID externo permanece separado logo abaixo.
    id: UUID = Field(default_factory=uuid4)

    # Indica qual linha do catálogo originou este anúncio.
    #
    # Exemplo:
    # govuk_teaching_vacancies_piloto
    #
    # Este campo é muito importante porque "fonte=outra" não informa
    # exatamente qual site foi acessado.
    #
    # Com o alvo_id podemos voltar ao catálogo e descobrir:
    # - qual domínio estava autorizado;
    # - se a coleta estava permitida;
    # - se a republicação estava permitida;
    # - se a fonte foi bloqueada depois da coleta.
    alvo_id: TextoObrigatorio | None = None

    # Tipo geral da fonte.
    #
    # Exemplos:
    # - outra;
    # - gupy;
    # - pagina_carreiras.
    fonte: Fonte

    # Identificador da vaga dentro do site de origem.
    id_externo: TextoObrigatorio
    url: HttpUrl
    # URL para a qual o candidato será enviado para se candidatar.
    #
    # Ela pode ser diferente da página em que encontramos a vaga.
    #
    # Exemplo:
    # Página encontrada: empresa.com/carreiras/vaga-123
    # Candidatura: sistema-ats.com/apply/vaga-123
    url_candidatura: HttpUrl | None = None

    # empresa_id pode ficar vazio até a resolução da empresa terminar.
    empresa_id: UUID | None = None
    id_empresa_na_fonte: str | None = None

    # Campos originais nunca são substituídos pelos valores normalizados.
    # Isso permite auditoria e reprocessamento com regras futuras.
    # Campos originais nunca são substituídos pelos normalizados.
    #
    # Eles guardam exatamente o que foi encontrado na fonte.
    # Isso permite corrigir as regras e reprocessar os dados no futuro.
    titulo_original: TextoObrigatorio
    descricao_original: str = ""

    # Nome da empresa exatamente como apareceu no anúncio.
    empresa_original: str | None = None

    # Localização completa exatamente como foi publicada.
    localidade_original: str | None = None

    # Partes mais específicas da localização, quando disponíveis.
    endereco_original: str | None = None
    cep_original: str | None = None

    # Coordenadas exatamente como foram encontradas.
    #
    # Exemplo:
    # "-23.5440722,-46.6450811"
    geolocalizacao_original: str | None = None

    # Salário exatamente como apareceu na página.
    #
    # Exemplo:
    # "R$ 5.000 a R$ 7.000 por mês"
    salario_original: str | None = None

    # Modalidade e contratação ainda sem padronização.
    modalidade_original: str | None = None
    regime_original: str | None = None
    senioridade_original: str | None = None

    # Textos específicos que podem aparecer separados na página.
    responsabilidades_original: str | None = None
    requisitos_original: str | None = None
    beneficios_original: str | None = None

    # Quantidade de profissionais que a empresa deseja contratar.
    numero_vagas_original: str | int | None = None

    # Datas informadas pela fonte.
    publicado_em: date | DataHora | None = None
    expira_em: date | DataHora | None = None

    # O status representa o que observamos na fonte, não uma conclusão eterna.
    status: StatusAnuncio = StatusAnuncio.DESCONHECIDO

    # SHA-256 detecta mudança de conteúdo sem comparar cada texto manualmente.
    hash_conteudo: str

    # Caminho ou chave para recuperar o JSON/HTML bruto.
    referencia_bruta: TextoObrigatorio

    # Campos adicionais da fonte são preservados para não perder informação.
    campos_estruturados: ObjetoJson = Field(default_factory=dict)

    # Intervalo durante o qual observamos esse anúncio.
    primeira_observacao_em: DataHora = Field(default_factory=agora_utc)
    ultima_observacao_em: DataHora = Field(default_factory=agora_utc)

    @field_validator("hash_conteudo", mode="before")
    @classmethod
    def validar_hash(cls, valor: str) -> str:
        # Normaliza letras hexadecimais antes da validação.
        normalizado = str(valor).strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", normalizado):
            raise ValueError("hash_conteudo deve ser um SHA-256 hexadecimal")
        return normalizado

    @model_validator(mode="after")
    def validar_periodo_observado(self) -> AnuncioVaga:
        # Impede uma sequência temporal impossível.
        if self.ultima_observacao_em < self.primeira_observacao_em:
            raise ValueError("última observação não pode ser anterior à primeira")
        return self

    @property
    def chave_fonte(self) -> tuple[Fonte, str]:
        # Esta chave será usada para idempotência e busca rápida no repositório.
        return self.fonte, self.id_externo
