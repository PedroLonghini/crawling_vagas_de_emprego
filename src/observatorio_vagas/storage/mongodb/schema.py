"""Definição das coleções e índices iniciais do MongoDB."""

from __future__ import annotations

# ASCENDING cria índices em ordem crescente.
#
# DESCENDING cria índices em ordem decrescente, útil para
# mostrar primeiro as informações mais recentes.
#
# TEXT cria índices próprios para pesquisas por palavras.
#
# IndexModel descreve um índice que será criado posteriormente.
from pymongo import ASCENDING, DESCENDING, TEXT, IndexModel

# Database representa o banco selecionado pelo PyMongo.
from pymongo.database import Database

# Centralizamos os nomes para evitar erros de digitação.
#
# Sem essas constantes, um arquivo poderia utilizar "empresa"
# e outro utilizar "empresas", criando duas coleções diferentes.
COLECAO_EMPRESAS = "empresas"
COLECAO_ANUNCIOS = "anuncios"
COLECAO_VAGAS_CANONICAS = "vagas_canonicas"
COLECAO_EXECUCOES_COLETA = "execucoes_coleta"
COLECAO_PUBLICACOES_EMPREGOS = "publicacoes_empregos"


# Este dicionário relaciona cada coleção aos seus índices.
#
# Um índice funciona como o índice de um livro:
# ele permite encontrar informações sem ler todos os documentos.
INDICES_POR_COLECAO: dict[str, tuple[IndexModel, ...]] = {
    COLECAO_EMPRESAS: (
        # CNPJ deve identificar no máximo uma empresa.
        #
        # partialFilterExpression faz o índice considerar
        # somente documentos que realmente possuem um CNPJ textual.
        IndexModel(
            [("cnpj", ASCENDING)],
            name="uq_empresas_cnpj",
            unique=True,
            partialFilterExpression={
                "cnpj": {"$type": "string"},
            },
        ),
        # Domínio também pode identificar uma empresa.
        #
        # exemplo.com e www.exemplo.com já serão normalizados
        # antes de chegar ao banco.
        IndexModel(
            [("dominio", ASCENDING)],
            name="uq_empresas_dominio",
            unique=True,
            partialFilterExpression={
                "dominio": {"$type": "string"},
            },
        ),
        # Este índice acelera filtros por localização e setor.
        IndexModel(
            [
                ("estado", ASCENDING),
                ("cidade", ASCENDING),
                ("setor", ASCENDING),
            ],
            name="ix_empresas_localidade_setor",
        ),
        # Índice utilizado para pesquisar empresas pelo nome.
        IndexModel(
            [
                ("razao_social", TEXT),
                ("nome_fantasia", TEXT),
                ("nomes_alternativos", TEXT),
            ],
            name="txt_empresas_nomes",
            # Nomes principais possuem mais importância
            # do que nomes alternativos.
            weights={
                "razao_social": 10,
                "nome_fantasia": 10,
                "nomes_alternativos": 5,
            },
            # A maioria dos textos será escrita em português.
            default_language="portuguese",
        ),
    ),
    COLECAO_ANUNCIOS: (
        # Esta é a principal proteção contra anúncios duplicados.
        #
        # Um mesmo id_externo pode existir em fontes diferentes.
        # Por isso fonte e id_externo precisam estar juntos.
        IndexModel(
            [
                ("fonte", ASCENDING),
                ("id_externo", ASCENDING),
            ],
            name="uq_anuncios_fonte_id_externo",
            unique=True,
        ),
        # Este índice acelera a tela que lista os anúncios
        # mais recentes de determinada empresa.
        IndexModel(
            [
                ("empresa_id", ASCENDING),
                ("status", ASCENDING),
                ("ultima_observacao_em", DESCENDING),
            ],
            name="ix_anuncios_empresa_status_observacao",
        ),
        # O pós-processamento do lote lista os anúncios de cada alvo pelo
        # período da coleta. Sem este índice cada alvo varria a coleção inteira
        # (342 MB, ~0,4 s por consulta no teste de 10 mil fontes).
        IndexModel(
            [("alvo_id", ASCENDING), ("ultima_observacao_em", DESCENDING)],
            name="ix_anuncios_alvo_observacao",
        ),
        # Permite localizar anúncios que possuem exatamente
        # o mesmo conteúdo calculado pelo SHA-256.
        IndexModel(
            [("hash_conteudo", ASCENDING)],
            name="ix_anuncios_hash_conteudo",
        ),
        # Índice para pesquisar palavras na descrição da vaga.
        #
        # Este índice será a primeira base do mecanismo
        # de procura por tecnologias, requisitos e benefícios.
        IndexModel(
            [
                ("titulo_original", TEXT),
                ("descricao_original", TEXT),
                ("responsabilidades_original", TEXT),
                ("requisitos_original", TEXT),
                ("beneficios_original", TEXT),
            ],
            name="txt_anuncios_conteudo",
            weights={
                "titulo_original": 10,
                "requisitos_original": 7,
                "responsabilidades_original": 5,
                "descricao_original": 3,
                "beneficios_original": 2,
            },
            default_language="portuguese",
        ),
        # Acelera consultas por data de publicação.
        IndexModel(
            [("publicado_em", DESCENDING)],
            name="ix_anuncios_publicado_em",
        ),
    ),
    COLECAO_VAGAS_CANONICAS: (
        # Lista rapidamente as vagas normalizadas de uma empresa.
        IndexModel(
            [
                ("empresa_id", ASCENDING),
                ("atualizado_em", DESCENDING),
            ],
            name="ix_vagas_empresa_atualizacao",
        ),
        # Acelera análises por cargo, área e senioridade.
        IndexModel(
            [
                ("titulo_normalizado", ASCENDING),
                ("area", ASCENDING),
                ("senioridade", ASCENDING),
            ],
            name="ix_vagas_cargo_area_senioridade",
        ),
        # Acelera filtros geográficos e de modalidade.
        IndexModel(
            [
                ("estado", ASCENDING),
                ("cidade", ASCENDING),
                ("modalidade", ASCENDING),
            ],
            name="ix_vagas_localidade_modalidade",
        ),
        # Permite procurar faixas de salário mensal.
        IndexModel(
            [
                ("salario.mensal_minimo", ASCENDING),
                ("salario.mensal_maximo", ASCENDING),
            ],
            name="ix_vagas_salario_mensal",
        ),
        # Pesquisa textual sobre os dados já normalizados.
        IndexModel(
            [
                ("titulo_normalizado", TEXT),
                ("descricao_normalizada", TEXT),
                ("tecnologias", TEXT),
                ("requisitos", TEXT),
                ("responsabilidades", TEXT),
                ("beneficios", TEXT),
            ],
            name="txt_vagas_conteudo",
            weights={
                "titulo_normalizado": 10,
                "tecnologias": 8,
                "requisitos": 7,
                "responsabilidades": 5,
                "descricao_normalizada": 3,
                "beneficios": 2,
            },
            default_language="portuguese",
        ),
    ),
    COLECAO_EXECUCOES_COLETA: (
        # Localiza as execuções mais recentes de determinada fonte.
        IndexModel(
            [
                ("fonte", ASCENDING),
                ("iniciado_em", DESCENDING),
            ],
            name="ix_execucoes_fonte_inicio",
        ),
        # Localiza rapidamente execuções que falharam,
        # estão pendentes ou continuam em andamento.
        IndexModel(
            [
                ("status", ASCENDING),
                ("iniciado_em", DESCENDING),
            ],
            name="ix_execucoes_status_inicio",
        ),
    ),
    COLECAO_PUBLICACOES_EMPREGOS: (
        # A chave impede duas gravações da mesma operação lógica.
        IndexModel(
            [("chave_idempotencia", ASCENDING)],
            name="uq_publicacoes_empregos_idempotencia",
            unique=True,
        ),
        # Permite consultar o histórico completo de uma vaga.
        IndexModel(
            [
                ("vaga_id", ASCENDING),
                ("criado_em", DESCENDING),
            ],
            name="ix_publicacoes_empregos_vaga_criacao",
        ),
        # Ajuda a localizar operações travadas ou indeterminadas.
        IndexModel(
            [
                ("situacao", ASCENDING),
                ("atualizado_em", DESCENDING),
            ],
            name="ix_publicacoes_empregos_situacao_atualizacao",
        ),
        # Mesma vaga vinda de outro site: consultada antes de cada publicação.
        IndexModel(
            [
                ("assinatura_conteudo", ASCENDING),
                ("situacao", ASCENDING),
            ],
            name="ix_publicacoes_empregos_assinatura",
        ),
    ),
}


def preparar_banco(
    banco: Database,
) -> dict[str, list[str]]:
    """Cria as coleções e seus índices de forma idempotente."""

    # Guardaremos os nomes devolvidos pelo MongoDB.
    #
    # Isso permite registrar e testar quais índices foram preparados.
    indices_criados: dict[str, list[str]] = {}

    # Percorremos cada coleção e seus índices.
    for nome_colecao, indices in INDICES_POR_COLECAO.items():
        # Selecionar uma coleção ainda não executa nenhuma escrita.
        colecao = banco[nome_colecao]

        # create_indexes cria a coleção caso ela ainda não exista.
        #
        # Se os índices já existirem com a mesma configuração,
        # o MongoDB apenas confirma seus nomes.
        nomes = colecao.create_indexes(list(indices))

        # Guardamos o resultado da preparação.
        indices_criados[nome_colecao] = nomes

    return indices_criados
