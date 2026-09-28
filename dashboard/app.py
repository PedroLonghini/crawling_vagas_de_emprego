"""Painel visual da demonstração do Observatório de Vagas.

Para executar este arquivo:

    .\.venv\Scripts\python.exe -m streamlit run dashboard/app.py

Esta versão utiliza somente dados fictícios.
API, crawler e banco de dados ainda não estão conectados.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from observatorio_vagas.demo.analytics import (
    calcular_indicadores,
    carregar_vagas,
)

# __file__ representa o caminho deste arquivo app.py.
#
# parents[1] sai da pasta dashboard e chega à raiz do projeto.
RAIZ_DO_PROJETO = Path(__file__).resolve().parents[1]

# Montamos o caminho completo do CSV.
#
# Isso permite abrir o painel mesmo que o terminal esteja
# inicialmente apontando para outra pasta.
CAMINHO_CSV = RAIZ_DO_PROJETO / "data" / "demo" / "vagas_demo.csv"


# Esta deve ser a primeira instrução do Streamlit.
#
# Ela configura a aba do navegador e o tamanho da página.
st.set_page_config(
    page_title="Observatório de Vagas",
    page_icon="📊",
    layout="wide",
)


@st.cache_data
def obter_dados() -> pd.DataFrame:
    """Carrega a base fictícia utilizada pelo painel.

    O cache evita abrir o mesmo CSV novamente toda vez
    que o usuário altera um filtro.
    """

    return carregar_vagas(CAMINHO_CSV)


def formatar_reais(valor: float | None) -> str:
    """Transforma um número em uma representação de dinheiro.

    Exemplo:
        4750.0 se transforma em R$ 4.750
    """

    # Se não existir salário, mostramos uma mensagem.
    if valor is None or pd.isna(valor):
        return "Não informado"

    # A formatação cria separadores de milhar com vírgula.
    resultado = f"R$ {valor:,.0f}"

    # Como estamos no Brasil, trocamos a vírgula por ponto.
    return resultado.replace(",", ".")


# Carregamos todas as vagas.
#
# Se o arquivo estiver incorreto, as validações existentes em
# analytics.py mostrarão uma mensagem de erro.
try:
    dados_completos = obter_dados()
except (FileNotFoundError, ValueError) as erro:
    st.error(f"Não foi possível abrir a demonstração: {erro}")
    st.stop()


# Título principal.
st.title("Observatório de Vagas e Salários")

# Texto menor abaixo do título.
st.caption("Comparação de empresas, cargos, salários e cobertura do Empregos")

# Este aviso precisa permanecer na demonstração.
#
# Ele deixa claro que ainda não estamos apresentando
# números coletados de fontes reais.
st.warning(
    "DEMONSTRAÇÃO COM DADOS FICTÍCIOS — "
    "A API, o crawler e o banco de produção ainda não estão conectados."
)


# ------------------------------------------------------------------
# FILTROS LATERAIS
# ------------------------------------------------------------------

st.sidebar.header("Filtros")

st.sidebar.caption("Se nenhum item for selecionado, o painel considera todos.")


# sorted coloca as empresas em ordem alfabética.
empresas_selecionadas = st.sidebar.multiselect(
    "Empresa",
    options=sorted(dados_completos["empresa"].unique()),
)

areas_selecionadas = st.sidebar.multiselect(
    "Área",
    options=sorted(dados_completos["area"].unique()),
)

estados_selecionados = st.sidebar.multiselect(
    "Estado",
    options=sorted(dados_completos["estado"].unique()),
)

modalidades_selecionadas = st.sidebar.multiselect(
    "Modalidade",
    options=sorted(dados_completos["modalidade"].unique()),
)


# Criamos uma cópia para não alterar a tabela original.
dados_filtrados = dados_completos.copy()


# Cada condição abaixo só será aplicada quando o usuário
# realmente selecionar alguma opção.
if empresas_selecionadas:
    dados_filtrados = dados_filtrados[dados_filtrados["empresa"].isin(empresas_selecionadas)]

if areas_selecionadas:
    dados_filtrados = dados_filtrados[dados_filtrados["area"].isin(areas_selecionadas)]

if estados_selecionados:
    dados_filtrados = dados_filtrados[dados_filtrados["estado"].isin(estados_selecionados)]

if modalidades_selecionadas:
    dados_filtrados = dados_filtrados[dados_filtrados["modalidade"].isin(modalidades_selecionadas)]


# Se a combinação de filtros não encontrar nenhuma vaga,
# mostramos um aviso e interrompemos a montagem do restante da tela.
if dados_filtrados.empty:
    st.warning("Nenhuma vaga atende aos filtros escolhidos.")
    st.stop()


# ------------------------------------------------------------------
# INDICADORES PRINCIPAIS
# ------------------------------------------------------------------

indicadores = calcular_indicadores(dados_filtrados)

# st.columns divide a tela em cinco espaços horizontais.
coluna_1, coluna_2, coluna_3, coluna_4, coluna_5 = st.columns(5)

coluna_1.metric(
    label="Vagas",
    value=indicadores["total_vagas"],
)

coluna_2.metric(
    label="Empresas",
    value=indicadores["total_empresas"],
)

coluna_3.metric(
    label="Salário mediano",
    value=formatar_reais(indicadores["salario_mediano"]),
)

coluna_4.metric(
    label="Cobertura no Empregos",
    value=f"{indicadores['cobertura_empregos']:.1f}%",
)

coluna_5.metric(
    label="Vagas com salário",
    value=f"{indicadores['percentual_com_salario']:.1f}%",
)


# ------------------------------------------------------------------
# ABAS DO PAINEL
# ------------------------------------------------------------------

aba_geral, aba_comparacao, aba_salarios, aba_vagas = st.tabs(
    [
        "Visão geral",
        "Comparação",
        "Salários",
        "Lista de vagas",
    ]
)


# ------------------------------------------------------------------
# ABA: VISÃO GERAL
# ------------------------------------------------------------------

with aba_geral:
    st.subheader("Visão geral do mercado")

    grafico_esquerdo, grafico_direito = st.columns(2)

    with grafico_esquerdo:
        st.markdown("#### Vagas por área")

        # value_counts conta quantas vagas existem em cada área.
        vagas_por_area = dados_filtrados["area"].value_counts().sort_values(ascending=False)

        st.bar_chart(vagas_por_area)

    with grafico_direito:
        st.markdown("#### Comparação entre as fontes")

        comparacao_fontes = (
            dados_filtrados["status_comparacao"].value_counts().sort_values(ascending=False)
        )

        st.bar_chart(comparacao_fontes)


# ------------------------------------------------------------------
# ABA: COMPARAÇÃO
# ------------------------------------------------------------------

with aba_comparacao:
    st.subheader("Cobertura do Empregos por empresa")

    # groupby reúne as vagas que pertencem à mesma empresa.
    resumo_empresas = dados_filtrados.groupby(
        "empresa",
        as_index=False,
    ).agg(
        # Conta quantas vagas únicas a empresa possui.
        total_vagas=("id_vaga", "nunique"),
        # Soma quantas vagas possuem presente_empregos=True.
        vagas_no_empregos=("presente_empregos", "sum"),
    )

    # Convertemos o resultado da soma para número inteiro.
    resumo_empresas["vagas_no_empregos"] = resumo_empresas["vagas_no_empregos"].astype(int)

    # Calculamos a cobertura percentual de cada empresa.
    resumo_empresas["cobertura_percentual"] = (
        resumo_empresas["vagas_no_empregos"] / resumo_empresas["total_vagas"] * 100
    ).round(1)

    # Criamos uma cópia com nomes amigáveis para apresentação.
    tabela_empresas = resumo_empresas.rename(
        columns={
            "empresa": "Empresa",
            "total_vagas": "Vagas conhecidas",
            "vagas_no_empregos": "Vagas no Empregos",
            "cobertura_percentual": "Cobertura (%)",
        }
    )

    st.dataframe(
        tabela_empresas,
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("#### Vagas encontradas somente em fontes externas")

    somente_externas = dados_filtrados[dados_filtrados["status_comparacao"] == "Somente externa"]

    tabela_externas = somente_externas[
        [
            "empresa",
            "cargo",
            "cidade",
            "estado",
            "fonte_externa",
        ]
    ].rename(
        columns={
            "empresa": "Empresa",
            "cargo": "Cargo",
            "cidade": "Cidade",
            "estado": "UF",
            "fonte_externa": "Fonte externa",
        }
    )

    st.dataframe(
        tabela_externas,
        hide_index=True,
        use_container_width=True,
    )


# ------------------------------------------------------------------
# ABA: SALÁRIOS
# ------------------------------------------------------------------

with aba_salarios:
    st.subheader("Salário mediano por cargo")

    # Removemos vagas que não possuem salário calculado.
    vagas_com_salario = dados_filtrados.dropna(subset=["salario_medio"])

    # Agrupamos os salários pelo nome do cargo.
    salarios_por_cargo = (
        vagas_com_salario.groupby("cargo")["salario_medio"].median().sort_values(ascending=False)
    )

    st.bar_chart(salarios_por_cargo)

    st.caption("O cálculo considera somente vagas que publicaram salário mínimo e máximo.")


# ------------------------------------------------------------------
# ABA: LISTA DE VAGAS
# ------------------------------------------------------------------

with aba_vagas:
    st.subheader("Vagas encontradas")

    # Escolhemos somente as colunas interessantes para a apresentação.
    tabela_vagas = dados_filtrados[
        [
            "empresa",
            "cargo",
            "area",
            "cidade",
            "estado",
            "modalidade",
            "salario_medio",
            "status_comparacao",
            "fonte_externa",
        ]
    ].copy()

    # Formatamos o salário antes de mostrar a tabela.
    tabela_vagas["salario_medio"] = tabela_vagas["salario_medio"].map(formatar_reais)

    # Renomeamos as colunas para títulos mais amigáveis.
    tabela_vagas = tabela_vagas.rename(
        columns={
            "empresa": "Empresa",
            "cargo": "Cargo",
            "area": "Área",
            "cidade": "Cidade",
            "estado": "UF",
            "modalidade": "Modalidade",
            "salario_medio": "Salário médio da faixa",
            "status_comparacao": "Comparação",
            "fonte_externa": "Fonte externa",
        }
    )

    st.dataframe(
        tabela_vagas,
        hide_index=True,
        use_container_width=True,
    )
