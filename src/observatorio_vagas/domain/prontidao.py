"""Avaliação dos dados necessários para uma futura publicação."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.common import ModeloDominio, TextoObrigatorio
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.domain.recrutador import Recrutador
from observatorio_vagas.domain.vaga import VagaCanonica


class SeveridadeProntidao(StrEnum):
    """Indica se um problema bloqueia ou apenas alerta."""

    ERRO = "erro"
    ALERTA = "alerta"


class SituacaoCampoProntidao(StrEnum):
    """Explica como o valor de um campo da API foi obtido."""

    EXTRAIDO = "extraido"
    INFERIDO = "inferido"
    ENRIQUECIDO = "enriquecido"
    GERADO = "gerado"
    AUSENTE = "ausente"
    REVISAO_NECESSARIA = "revisao_necessaria"


# Lista dos 24 valores simples existentes no JobRequest da API.
#
# O segundo valor de cada item indica se o campo é obrigatório.
CAMPOS_API_EMPREGOS: tuple[tuple[str, bool], ...] = (
    # A URL da vaga é obrigatória como referência da fonte. Quando houver uma
    # URL direta de candidatura, ela tem preferência. Isso substitui a
    # exigência de CNPJ para publicação.
    ("company.applyUrl", True),
    ("company.name", True),
    ("company.logoUrl", False),
    # O Empregos exibe o bloco da empresa mesmo sem texto institucional.
    ("company.description", False),
    ("company.industries", False),
    ("company.companyId", False),
    ("company.recruiterId", False),
    ("company.recruiterName", False),
    ("company.recruiterEmail", False),
    # CNPJ é útil para identificação, mas pode não ser divulgado pela fonte.
    ("company.nationalRegister", False),
    ("externalJobPostingId", True),
    ("jobPostingOperationType", False),
    ("title", True),
    ("description", True),
    ("location.address", True),
    ("location.postalCode", False),
    ("location.geolocation", False),
    ("salary.min", False),
    ("salary.max", False),
    ("workplaceTypes", False),
    ("employmentStatus", False),
    ("experienceLevel", False),
    ("trackingPixelUrl", False),
    ("expireAt", False),
)


# Tradução da modalidade interna para o texto aceito pelo Empregos.
_MODALIDADE_EMPREGOS = {
    ModalidadeTrabalho.PRESENCIAL: "On-site",
    ModalidadeTrabalho.HIBRIDO: "Hybrid",
    ModalidadeTrabalho.REMOTO: "Remote",
}


# Tradução dos regimes usados internamente.
_REGIME_EMPREGOS = {
    RegimeContratacao.CLT: "FULL_TIME",
    RegimeContratacao.PJ: "CONTRACT",
    RegimeContratacao.ESTAGIO: "INTERNSHIP",
    RegimeContratacao.TEMPORARIO: "TEMPORARY",
    RegimeContratacao.FREELANCER: "CONTRACT",
}
# Valores oficiais aceitos pelo campo employmentStatus
# da API do Empregos.
#
# Usamos frozenset porque essa coleção não deve ser modificada
# durante a execução do programa.
_STATUS_EMPREGOS_ACEITOS = frozenset(
    {
        "FULL_TIME",
        "PART_TIME",
        "CONTRACT",
        "INTERNSHIP",
        "TEMPORARY",
        "VOLUNTEER",
    }
)

# Tradução das senioridades internas.
_SENIORIDADE_EMPREGOS = {
    Senioridade.ESTAGIO: "INTERNSHIP",
    Senioridade.APRENDIZ: "ENTRY_LEVEL",
    Senioridade.JUNIOR: "ENTRY_LEVEL",
    Senioridade.PLENO: "MID_SENIOR_LEVEL",
    Senioridade.SENIOR: "MID_SENIOR_LEVEL",
    Senioridade.ESPECIALISTA: "MID_SENIOR_LEVEL",
    Senioridade.LIDERANCA: "DIRECTOR",
}


class CampoProntidao(ModeloDominio):
    """Representa um campo que poderá ser enviado para a API."""

    # Caminho do campo dentro do futuro JSON.
    #
    # Exemplos:
    # title
    # company.name
    # location.address
    campo: TextoObrigatorio

    # Valor que poderá ser colocado no JSON.
    valor: Any | None = None

    # Define se a ausência do campo bloqueia a publicação.
    obrigatorio: bool

    # Explica de onde veio o valor.
    situacao: SituacaoCampoProntidao

    # Informação adicional para o dashboard.
    mensagem: str | None = None

    @property
    def preenchido(self) -> bool:
        """Indica se o campo possui um valor utilizável."""

        return self.situacao in {
            SituacaoCampoProntidao.EXTRAIDO,
            SituacaoCampoProntidao.INFERIDO,
            SituacaoCampoProntidao.ENRIQUECIDO,
            SituacaoCampoProntidao.GERADO,
        }


class ProblemaProntidao(ModeloDominio):
    """Representa um campo ausente ou inadequado."""

    campo: TextoObrigatorio
    mensagem: TextoObrigatorio
    severidade: SeveridadeProntidao


class RelatorioProntidao(ModeloDominio):
    """Resultado completo da avaliação de uma vaga."""

    campos: tuple[CampoProntidao, ...] = ()
    problemas: tuple[ProblemaProntidao, ...] = ()

    @property
    def erros(self) -> tuple[ProblemaProntidao, ...]:
        """Devolve somente os problemas que bloqueiam o envio."""

        return tuple(
            problema
            for problema in self.problemas
            if problema.severidade == SeveridadeProntidao.ERRO
        )

    @property
    def alertas(self) -> tuple[ProblemaProntidao, ...]:
        """Devolve somente os alertas."""

        return tuple(
            problema
            for problema in self.problemas
            if problema.severidade == SeveridadeProntidao.ALERTA
        )

    @property
    def total_campos(self) -> int:
        """Retorna quantos campos estão sendo avaliados."""

        return len(self.campos)

    @property
    def campos_preenchidos(self) -> tuple[CampoProntidao, ...]:
        """Retorna os campos que possuem valores utilizáveis."""

        return tuple(campo for campo in self.campos if campo.preenchido)

    @property
    def campos_obrigatorios_ausentes(
        self,
    ) -> tuple[CampoProntidao, ...]:
        """Retorna campos obrigatórios ausentes ou em revisão."""

        return tuple(campo for campo in self.campos if campo.obrigatorio and not campo.preenchido)

    @property
    def percentual_preenchimento(self) -> float:
        """Calcula o percentual dos campos preenchidos."""

        if self.total_campos == 0:
            return 0.0

        return round(
            len(self.campos_preenchidos) / self.total_campos * 100,
            2,
        )

    @property
    def pronto_para_envio(self) -> bool:
        """Indica se a vaga poderá formar um payload válido."""

        return not self.erros and not self.campos_obrigatorios_ausentes

    def buscar_campo(
        self,
        nome: str,
    ) -> CampoProntidao | None:
        """Encontra um campo pelo caminho utilizado na API."""

        for campo in self.campos:
            if campo.campo == nome:
                return campo

        return None


def _texto_url(valor: object) -> str | None:
    """Transforma uma URL opcional em texto."""

    if valor is None:
        return None

    texto = str(valor).strip()

    return texto or None


def _endereco_api(
    vaga: VagaCanonica,
) -> tuple[str | None, SituacaoCampoProntidao]:
    """Obtém ou monta o endereço aceito pela API."""

    # Caso o extrator tenha encontrado um endereço completo,
    # utilizamos esse valor.
    if vaga.endereco:
        return vaga.endereco, SituacaoCampoProntidao.INFERIDO

    # Quando o endereço completo não existe, tentamos montar um
    # valor utilizando cidade, estado e país.
    partes = [
        parte
        for parte in (
            vaga.cidade,
            vaga.estado,
            vaga.pais,
        )
        if parte
    ]

    # Cidade e estado são o mínimo para gerar um endereço utilizável.
    if vaga.cidade and vaga.estado:
        return ", ".join(partes), SituacaoCampoProntidao.GERADO

    return None, SituacaoCampoProntidao.AUSENTE


def _geolocalizacao_api(
    vaga: VagaCanonica,
) -> str | None:
    """Monta a coordenada no formato latitude,longitude."""

    if vaga.latitude is None or vaga.longitude is None:
        return None

    return f"{vaga.latitude},{vaga.longitude}"


def _salario_api(
    valor: Decimal | None,
) -> tuple[int | str | None, SituacaoCampoProntidao]:
    """Prepara um salário para o tipo inteiro exigido pela API."""

    if valor is None:
        return None, SituacaoCampoProntidao.AUSENTE

    # A API espera um número inteiro de 32 bits.
    #
    # Caso o valor possua centavos ou ultrapasse o limite,
    # ele será marcado para revisão.
    if valor == valor.to_integral_value() and 0 <= valor <= 2_147_483_647:
        return int(valor), SituacaoCampoProntidao.INFERIDO

    return str(valor), SituacaoCampoProntidao.REVISAO_NECESSARIA


def _status_emprego_api(
    *,
    anuncio: AnuncioVaga,
    vaga: VagaCanonica,
) -> tuple[str | None, SituacaoCampoProntidao]:
    """Obtém o employmentStatus sem confundir FULL_TIME com CLT."""

    # regime_original preserva exatamente o que veio da fonte.
    #
    # Um anúncio pode conter:
    #
    # FULL_TIME
    #
    # ou vários valores separados por vírgula:
    #
    # FULL_TIME, TEMPORARY
    valores_originais = tuple(
        parte.strip().upper()
        for parte in (anuncio.regime_original or "").split(",")
        if parte.strip()
    )

    # Mantemos somente valores reconhecidos oficialmente
    # pela API do Empregos.
    valores_oficiais = tuple(
        valor for valor in valores_originais if valor in _STATUS_EMPREGOS_ACEITOS
    )

    # Se a fonte forneceu exatamente um valor oficial,
    # podemos utilizá-lo diretamente.
    #
    # Exemplo:
    #
    # FULL_TIME
    #
    # Ele será marcado como EXTRAIDO porque veio da própria fonte.
    if len(valores_oficiais) == 1:
        return (
            valores_oficiais[0],
            SituacaoCampoProntidao.EXTRAIDO,
        )

    # Se existem vários valores ou nenhum valor utilizável,
    # tentamos usar a classificação feita pela normalização interna.
    #
    # Exemplo:
    #
    # RegimeContratacao.TEMPORARIO vira TEMPORARY.
    regime_interno = _REGIME_EMPREGOS.get(vaga.regime)

    if regime_interno is not None:
        return (
            regime_interno,
            SituacaoCampoProntidao.INFERIDO,
        )

    # Se encontramos vários valores oficiais, mas não conseguimos
    # decidir com segurança qual deles deve ser enviado, deixamos
    # o campo para revisão.
    #
    # Se nenhum valor foi encontrado, marcamos como ausente.
    situacao = (
        SituacaoCampoProntidao.REVISAO_NECESSARIA
        if valores_oficiais
        else SituacaoCampoProntidao.AUSENTE
    )

    return None, situacao


def avaliar_prontidao_empregos(
    *,
    empresa: Empresa,
    recrutador: Recrutador | None,
    anuncio: AnuncioVaga,
    vaga: VagaCanonica,
    company_id_empregos: str | None = None,
    tracking_pixel_url: str | None = None,
    situacoes_campos: Mapping[
        str,
        SituacaoCampoProntidao,
    ]
    | None = None,
) -> RelatorioProntidao:
    """Prepara e verifica os 24 campos do futuro payload."""

    campos: list[CampoProntidao] = []
    problemas: list[ProblemaProntidao] = []

    # Converte o catálogo em dicionário para descobrir rapidamente
    # se um campo é obrigatório.
    obrigatoriedade = dict(CAMPOS_API_EMPREGOS)

    # Permite que a camada de extração informe com mais precisão
    # como um campo foi obtido.
    situacoes_personalizadas = situacoes_campos or {}

    def adicionar_campo(
        nome: str,
        valor: Any | None,
        situacao: SituacaoCampoProntidao,
        mensagem: str | None = None,
    ) -> None:
        """Adiciona um dos campos na ordem do catálogo."""

        valor_ausente = valor is None or (isinstance(valor, str) and not valor.strip())

        # Um valor vazio sempre será considerado ausente.
        if valor_ausente:
            situacao_final = SituacaoCampoProntidao.AUSENTE
        else:
            situacao_final = situacoes_personalizadas.get(
                nome,
                situacao,
            )

        campos.append(
            CampoProntidao(
                campo=nome,
                valor=valor,
                obrigatorio=obrigatoriedade[nome],
                situacao=situacao_final,
                mensagem=mensagem,
            )
        )

    def adicionar_problema(
        campo: str,
        mensagem: str,
        severidade: SeveridadeProntidao,
    ) -> None:
        """Registra um erro ou alerta de validação."""

        problemas.append(
            ProblemaProntidao(
                campo=campo,
                mensagem=mensagem,
                severidade=severidade,
            )
        )

    # --------------------------------------------------------------
    # RECRUTADOR
    # --------------------------------------------------------------

    # O recrutador é opcional.
    #
    # Entretanto, se existir, ele precisa pertencer à empresa e
    # estar ativo.
    recrutador_valido = recrutador is not None

    if recrutador is not None and recrutador.empresa_id != empresa.id:
        recrutador_valido = False

        adicionar_problema(
            "company.recruiter",
            "O recrutador pertence a outra empresa.",
            SeveridadeProntidao.ERRO,
        )

    if recrutador is not None and not recrutador.ativo:
        recrutador_valido = False

        adicionar_problema(
            "company.recruiter",
            "O recrutador selecionado está inativo.",
            SeveridadeProntidao.ERRO,
        )

    situacao_recrutador = (
        SituacaoCampoProntidao.ENRIQUECIDO
        if recrutador_valido
        else SituacaoCampoProntidao.REVISAO_NECESSARIA
    )

    # --------------------------------------------------------------
    # EMPRESA
    # --------------------------------------------------------------

    adicionar_campo(
        "company.applyUrl",
        _texto_url(anuncio.url_candidatura or anuncio.url),
        SituacaoCampoProntidao.EXTRAIDO,
        (
            "URL da fonte usada porque não há link direto de candidatura."
            if anuncio.url_candidatura is None
            else None
        ),
    )

    adicionar_campo(
        "company.name",
        empresa.nome_exibicao,
        SituacaoCampoProntidao.ENRIQUECIDO,
    )

    adicionar_campo(
        "company.logoUrl",
        _texto_url(empresa.logo_url),
        SituacaoCampoProntidao.ENRIQUECIDO,
    )

    adicionar_campo(
        "company.description",
        empresa.descricao,
        SituacaoCampoProntidao.ENRIQUECIDO,
        (
            "Descrição institucional não encontrada (campo opcional)."
            if not empresa.descricao
            else None
        ),
    )

    adicionar_campo(
        "company.industries",
        empresa.setor,
        SituacaoCampoProntidao.ENRIQUECIDO,
    )

    adicionar_campo(
        "company.companyId",
        company_id_empregos,
        SituacaoCampoProntidao.ENRIQUECIDO,
    )

    adicionar_campo(
        "company.recruiterId",
        recrutador.id_externo if recrutador is not None else None,
        situacao_recrutador,
    )

    adicionar_campo(
        "company.recruiterName",
        recrutador.nome if recrutador is not None else None,
        situacao_recrutador,
    )

    adicionar_campo(
        "company.recruiterEmail",
        recrutador.email if recrutador is not None else None,
        situacao_recrutador,
    )

    adicionar_campo(
        "company.nationalRegister",
        empresa.cnpj,
        SituacaoCampoProntidao.ENRIQUECIDO,
        "CNPJ não informado (campo opcional)." if not empresa.cnpj else None,
    )

    # --------------------------------------------------------------
    # IDENTIFICAÇÃO E CONTEÚDO DA VAGA
    # --------------------------------------------------------------

    situacao_id = (
        SituacaoCampoProntidao.REVISAO_NECESSARIA
        if len(anuncio.id_externo) > 200
        else SituacaoCampoProntidao.EXTRAIDO
    )

    adicionar_campo(
        "externalJobPostingId",
        anuncio.id_externo,
        situacao_id,
    )

    # Nesta etapa estamos somente preparando uma nova publicação.
    #
    # Operações de alteração ficarão para a integração final.
    adicionar_campo(
        "jobPostingOperationType",
        "CREATE",
        SituacaoCampoProntidao.GERADO,
    )

    situacao_titulo = (
        SituacaoCampoProntidao.REVISAO_NECESSARIA
        if len(vaga.titulo_normalizado) > 200
        else SituacaoCampoProntidao.INFERIDO
    )

    adicionar_campo(
        "title",
        vaga.titulo_normalizado,
        situacao_titulo,
    )

    descricao = vaga.descricao_normalizada

    descricao_valida = descricao is not None and 100 <= len(descricao) <= 25_000

    situacao_descricao = (
        SituacaoCampoProntidao.INFERIDO
        if descricao_valida
        else SituacaoCampoProntidao.REVISAO_NECESSARIA
    )

    adicionar_campo(
        "description",
        descricao,
        situacao_descricao,
    )

    # --------------------------------------------------------------
    # LOCALIDADE
    # --------------------------------------------------------------

    endereco, situacao_endereco = _endereco_api(vaga)

    adicionar_campo(
        "location.address",
        endereco,
        situacao_endereco,
    )

    adicionar_campo(
        "location.postalCode",
        vaga.cep,
        SituacaoCampoProntidao.INFERIDO,
    )

    adicionar_campo(
        "location.geolocation",
        _geolocalizacao_api(vaga),
        SituacaoCampoProntidao.ENRIQUECIDO,
    )

    # --------------------------------------------------------------
    # SALÁRIO
    # --------------------------------------------------------------

    salario_minimo, situacao_salario_minimo = _salario_api(
        vaga.salario.minimo if vaga.salario is not None else None
    )

    salario_maximo, situacao_salario_maximo = _salario_api(
        vaga.salario.maximo if vaga.salario is not None else None
    )

    adicionar_campo(
        "salary.min",
        salario_minimo,
        situacao_salario_minimo,
    )

    adicionar_campo(
        "salary.max",
        salario_maximo,
        situacao_salario_maximo,
    )

    # --------------------------------------------------------------
    # CLASSIFICAÇÕES
    # --------------------------------------------------------------

    modalidade = _MODALIDADE_EMPREGOS.get(vaga.modalidade)

    adicionar_campo(
        "workplaceTypes",
        modalidade,
        SituacaoCampoProntidao.INFERIDO,
    )
    # Primeiro tentamos preservar o valor oficial publicado
    # pela própria fonte.
    #
    # Se isso não for possível, usamos a classificação interna.
    regime, situacao_regime = _status_emprego_api(
        anuncio=anuncio,
        vaga=vaga,
    )
    adicionar_campo(
        "employmentStatus",
        regime,
        situacao_regime,
    )

    senioridade = _SENIORIDADE_EMPREGOS.get(vaga.senioridade)

    adicionar_campo(
        "experienceLevel",
        senioridade,
        SituacaoCampoProntidao.INFERIDO,
    )

    adicionar_campo(
        "trackingPixelUrl",
        tracking_pixel_url,
        SituacaoCampoProntidao.GERADO,
    )

    adicionar_campo(
        "expireAt",
        (anuncio.expira_em.isoformat() if anuncio.expira_em is not None else None),
        SituacaoCampoProntidao.EXTRAIDO,
    )

    # --------------------------------------------------------------
    # VALIDAÇÕES DOS CAMPOS OBRIGATÓRIOS
    # --------------------------------------------------------------

    if len(anuncio.id_externo) > 200:
        adicionar_problema(
            "externalJobPostingId",
            "O identificador externo possui mais de 200 caracteres.",
            SeveridadeProntidao.ERRO,
        )

    if len(vaga.titulo_normalizado) > 200:
        adicionar_problema(
            "title",
            "O título possui mais de 200 caracteres.",
            SeveridadeProntidao.ERRO,
        )

    if not descricao:
        adicionar_problema(
            "description",
            "A vaga não possui descrição normalizada.",
            SeveridadeProntidao.ERRO,
        )
    elif len(descricao) < 100:
        adicionar_problema(
            "description",
            "A descrição possui menos de 100 caracteres.",
            SeveridadeProntidao.ERRO,
        )
    elif len(descricao) > 25_000:
        adicionar_problema(
            "description",
            "A descrição possui mais de 25.000 caracteres.",
            SeveridadeProntidao.ERRO,
        )

    if endereco is None:
        adicionar_problema(
            "location.address",
            "A vaga não possui endereço, cidade e estado.",
            SeveridadeProntidao.ERRO,
        )

    # --------------------------------------------------------------
    # AVISOS DOS CAMPOS OPCIONAIS
    # --------------------------------------------------------------

    if modalidade is None:
        adicionar_problema(
            "workplaceTypes",
            "A modalidade de trabalho não foi identificada.",
            SeveridadeProntidao.ALERTA,
        )

    if regime is None:
        adicionar_problema(
            "employmentStatus",
            "O tipo de contratação não foi identificado.",
            SeveridadeProntidao.ALERTA,
        )

    if senioridade is None:
        adicionar_problema(
            "experienceLevel",
            "A senioridade não foi identificada.",
            SeveridadeProntidao.ALERTA,
        )

    if vaga.salario is None:
        adicionar_problema(
            "salary",
            "A vaga não publicou salário.",
            SeveridadeProntidao.ALERTA,
        )

    return RelatorioProntidao(
        campos=tuple(campos),
        problemas=tuple(problemas),
    )
