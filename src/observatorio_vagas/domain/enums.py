"""Enumerações persistíveis utilizadas no domínio."""

from enum import StrEnum


# StrEnum cria valores que se comportam como texto, mas só permitem opções
# conhecidas. Isso evita "Empregos", "emprego" e "EMPREGOS" representando a
# mesma fonte no banco.
class Fonte(StrEnum):
    # Fonte oficial e principal deste produto.
    EMPREGOS = "empregos"

    # Plataforma externa de recrutamento.
    GUPY = "gupy"

    # Plataforma de recrutamento operada pelo Infojobs.
    PANDAPE = "pandape"

    # Agregador de vagas com distribuição feita por API licenciada.
    ADZUNA = "adzuna"

    # Agregador de vagas com distribuição feita por API licenciada.
    JOOBLE = "jooble"

    # Diários oficiais municipais disponibilizados pela API pública
    # do projeto Querido Diário.
    QUERIDO_DIARIO = "querido_diario"

    # Catálogos governamentais de dados abertos que usam a API CKAN.
    # A página inicial descreve o conjunto e aponta para recursos CSV.
    CKAN = "ckan"

    # Página própria de carreiras de uma empresa.
    PAGINA_CARREIRAS = "pagina_carreiras"

    # Conteúdo estruturado encontrado dentro de uma página.
    JSON_LD = "json_ld"

    OUTRA = "outra"


class TipoPaginaColeta(StrEnum):
    """Papel que uma página exerce dentro da coleta."""

    # Primeira página acessada para uma empresa.
    #
    # Normalmente é a página de carreiras ou a listagem de vagas.
    INICIAL = "inicial"

    # Página que representa uma vaga específica.
    DETALHE_VAGA = "detalhe_vaga"

    # Página coletada sem ter vindo do catálogo.
    #
    # É usada, por exemplo, pelo spider pagina_unica.
    AVULSA = "avulsa"


class StatusPoliticaFonte(StrEnum):
    """Situação de autorização aplicada a uma fonte externa."""

    # A fonte ainda precisa ser analisada.
    # Enquanto estiver pendente, o sistema não coleta nem publica.
    PENDENTE = "pendente"

    # A fonte foi aprovada para coleta e republicação.
    APROVADA = "aprovada"

    # A coleta é permitida somente para uso interno.
    # As vagas desta fonte não podem ser republicadas.
    SOMENTE_COLETA = "somente_coleta"

    # A fonte não pode ser acessada pelo crawler.
    BLOQUEADA = "bloqueada"

    # A fonte foi desligada temporariamente por uma decisão operacional.
    DESATIVADA = "desativada"


# Representa o ciclo observado do anúncio. AUSENTE não significa encerrado
# imediatamente, pois a fonte pode apenas ter falhado temporariamente.
class StatusAnuncio(StrEnum):
    DESCOBERTO = "descoberto"
    ATIVO = "ativo"
    ALTERADO = "alterado"
    AUSENTE = "ausente_aguardando_confirmacao"
    ENCERRADO = "encerrado"
    REABERTO = "reaberto"
    INVALIDO = "invalido"
    DESCONHECIDO = "desconhecido"


class ModalidadeTrabalho(StrEnum):
    """Onde a pessoa executará o trabalho."""

    PRESENCIAL = "presencial"
    HIBRIDO = "hibrido"
    REMOTO = "remoto"
    NAO_INFORMADO = "nao_informado"


class RegimeContratacao(StrEnum):
    """Tipo de vínculo anunciado para a oportunidade."""

    CLT = "clt"
    PJ = "pj"
    ESTAGIO = "estagio"
    TEMPORARIO = "temporario"
    APRENDIZ = "aprendiz"
    FREELANCER = "freelancer"
    OUTRO = "outro"
    NAO_INFORMADO = "nao_informado"


class Senioridade(StrEnum):
    """Nível profissional normalizado da oportunidade."""

    ESTAGIO = "estagio"
    APRENDIZ = "aprendiz"
    JUNIOR = "junior"
    PLENO = "pleno"
    SENIOR = "senior"
    ESPECIALISTA = "especialista"
    LIDERANCA = "lideranca"
    NAO_INFORMADA = "nao_informada"


class PeriodoSalario(StrEnum):
    """Unidade temporal do valor salarial publicado."""

    HORA = "hora"
    DIA = "dia"
    SEMANA = "semana"
    MES = "mes"
    ANO = "ano"
    NAO_INFORMADO = "nao_informado"


class NaturezaSalario(StrEnum):
    """Separa valor publicado, conversão matemática e estimativa."""

    # Valor encontrado explicitamente na fonte.
    PUBLICADO = "publicado"

    # Valor produzido por conversão, como anual dividido por doze.
    CALCULADO = "calculado"

    # Valor inferido; não deve ser misturado silenciosamente ao publicado.
    ESTIMADO = "estimado"


class StatusColeta(StrEnum):
    """Situação operacional de uma execução do conector."""

    PENDENTE = "pendente"
    EXECUTANDO = "executando"
    CONCLUIDA = "concluida"
    PARCIAL = "parcial"
    FALHOU = "falhou"


class SituacaoPublicacaoEmpregos(StrEnum):
    """Estado persistido de uma operação enviada ao Empregos."""

    PREPARADA = "preparada"
    ENVIANDO = "enviando"
    SUCESSO = "sucesso"
    REJEITADA = "rejeitada"
    INDETERMINADA = "indeterminada"


class ClassificacaoCorrespondencia(StrEnum):
    """Resultado da comparação de dois anúncios."""

    CONFIRMADA = "confirmada"
    PROVAVEL = "provavel"
    AMBIGUA = "ambigua"
    REJEITADA = "rejeitada"


class MetodoExtracao(StrEnum):
    """Método responsável por produzir um campo estruturado."""

    API = "api"
    JSON_LD = "json_ld"
    HTML = "html"
    REGRA = "regra"
    CLASSIFICADOR = "classificador"
    MODELO_LINGUAGEM = "modelo_linguagem"
    MANUAL = "manual"
