"""Painel visual das respostas reais coletadas pelo crawler."""

from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from observatorio_vagas.crawling.inventario import (
    ErroInventarioBruto,
    carregar_inventario_bruto,
)
from observatorio_vagas.domain.enums import TipoPaginaColeta

# Descobre automaticamente a raiz do projeto.
RAIZ_DO_PROJETO = Path(__file__).resolve().parents[1]

# Pasta que contém as respostas reais do crawler.
DIRETORIO_RAW = RAIZ_DO_PROJETO / "data" / "raw"
# Nomes exibidos no dashboard.
#
# O banco continua usando os valores técnicos, enquanto a interface
# apresenta textos mais fáceis de entender.
ROTULOS_TIPO_PAGINA = {
    TipoPaginaColeta.INICIAL.value: "Página inicial",
    TipoPaginaColeta.DETALHE_VAGA.value: "Detalhe de vaga",
    TipoPaginaColeta.AVULSA.value: "Página avulsa",
}


# Precisa ser o primeiro comando visual do Streamlit.
st.set_page_config(
    page_title="Monitor de Coletas",
    page_icon="🕷️",
    layout="wide",
)


@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def obter_coletas() -> pd.DataFrame:
    """Carrega o inventário e o transforma em uma tabela."""

    registros = carregar_inventario_bruto(
        DIRETORIO_RAW,
    )

    # Antes da primeira coleta, a tabela ficará vazia.
    if not registros:
        return pd.DataFrame()

    # asdict transforma cada dataclass em um dicionário.
    #
    # O Pandas transforma os dicionários em uma tabela.
    dados = pd.DataFrame([asdict(registro) for registro in registros])

    # Converte as datas para o tipo de data do Pandas.
    dados["coletado_em"] = pd.to_datetime(
        dados["coletado_em"],
        utc=True,
    )

    # Arquivos antigos não possuem empresa_nome.
    dados["empresa_exibicao"] = dados["empresa_nome"].fillna("Sem empresa identificada")
    # Troca os valores técnicos por nomes amigáveis somente na interface.
    dados["tipo_pagina_exibicao"] = dados["tipo_pagina"].replace(
        ROTULOS_TIPO_PAGINA,
    )

    return dados


def formatar_bytes(total: int) -> str:
    """Transforma bytes em uma medida mais fácil de ler."""

    valor = float(total)

    unidades = (
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    )

    for unidade in unidades:
        if valor < 1024 or unidade == unidades[-1]:
            return f"{valor:.1f} {unidade}"

        valor /= 1024

    return f"{valor:.1f} TB"


# ------------------------------------------------------------------
# CABEÇALHO
# ------------------------------------------------------------------

st.title("Monitor de Coletas Reais")

st.caption("Inventário das respostas externas preservadas pelo crawler")

st.info(
    "Uma página coletada ainda não representa uma vaga extraída. "
    "Esta tela mostra as respostas brutas e seus metadados."
)


# ------------------------------------------------------------------
# CARREGAMENTO
# ------------------------------------------------------------------

st.sidebar.header("Controles")

if st.sidebar.button(
    "Atualizar dados",
    use_container_width=True,
):
    # Limpa o cache para ler novamente os arquivos JSON.
    obter_coletas.clear()
    st.rerun()

try:
    dados_completos = obter_coletas()

except ErroInventarioBruto as erro:
    st.error(f"Não foi possível montar o inventário: {erro}")
    st.stop()

if dados_completos.empty:
    st.warning("Nenhuma coleta foi encontrada em data/raw/respostas.")
    st.stop()


# ------------------------------------------------------------------
# FILTROS
# ------------------------------------------------------------------

empresas = sorted(dados_completos["empresa_exibicao"].unique())

fontes = sorted(dados_completos["fonte"].unique())

tipos_pagina = sorted(
    dados_completos["tipo_pagina_exibicao"].unique(),
)

status_http = sorted(int(status) for status in dados_completos["status_http"].unique())

empresas_selecionadas = st.sidebar.multiselect(
    "Empresa",
    options=empresas,
)

fontes_selecionadas = st.sidebar.multiselect(
    "Fonte",
    options=fontes,
)
tipos_pagina_selecionados = st.sidebar.multiselect(
    "Tipo de página",
    options=tipos_pagina,
)

status_selecionados = st.sidebar.multiselect(
    "Status HTTP",
    options=status_http,
)

dados_filtrados = dados_completos.copy()

if empresas_selecionadas:
    dados_filtrados = dados_filtrados[
        dados_filtrados["empresa_exibicao"].isin(empresas_selecionadas)
    ]

if fontes_selecionadas:
    dados_filtrados = dados_filtrados[dados_filtrados["fonte"].isin(fontes_selecionadas)]

    if tipos_pagina_selecionados:
        dados_filtrados = dados_filtrados[
            dados_filtrados["tipo_pagina_exibicao"].isin(
                tipos_pagina_selecionados,
            )
        ]

if status_selecionados:
    dados_filtrados = dados_filtrados[dados_filtrados["status_http"].isin(status_selecionados)]

if dados_filtrados.empty:
    st.warning("Nenhuma coleta atende aos filtros escolhidos.")
    st.stop()


# ------------------------------------------------------------------
# INDICADORES
# ------------------------------------------------------------------

total_coletas = len(dados_filtrados)

total_empresas = int(dados_filtrados["alvo_id"].dropna().nunique())

total_sucessos = int(
    dados_filtrados["status_http"]
    .between(
        200,
        299,
    )
    .sum()
)

# Conta somente páginas que representam vagas específicas.
total_detalhes = int((dados_filtrados["tipo_pagina"] == TipoPaginaColeta.DETALHE_VAGA.value).sum())

total_bytes = int(
    dados_filtrados["tamanho_bytes"].sum(),
)

ultima_coleta = dados_filtrados["coletado_em"].max()

coluna_1, coluna_2, coluna_3, coluna_4, coluna_5, coluna_6 = st.columns(6)

coluna_1.metric(
    "Coletas",
    total_coletas,
)

coluna_2.metric(
    "Empresas identificadas",
    total_empresas,
)

coluna_3.metric(
    "Respostas com sucesso",
    total_sucessos,
)

coluna_4.metric(
    "Detalhes de vagas",
    total_detalhes,
)

coluna_5.metric(
    "Volume armazenado",
    formatar_bytes(total_bytes),
)

coluna_6.metric(
    "Última coleta",
    ultima_coleta.strftime("%d/%m/%Y %H:%M UTC"),
)


# ------------------------------------------------------------------
# ABAS
# ------------------------------------------------------------------

aba_resumo, aba_tabela, aba_detalhes = st.tabs(
    [
        "Resumo",
        "Tabela de coletas",
        "Detalhes",
    ]
)


# ------------------------------------------------------------------
# ABA DE RESUMO
# ------------------------------------------------------------------

with aba_resumo:
    esquerda, direita = st.columns(2)

    with esquerda:
        st.subheader("Coletas por empresa")

        coletas_por_empresa = dados_filtrados["empresa_exibicao"].value_counts()

        st.bar_chart(coletas_por_empresa)

    with direita:
        st.subheader("Respostas por status HTTP")

        coletas_por_status = dados_filtrados["status_http"].value_counts()

        st.bar_chart(coletas_por_status)

    st.subheader("Coletas por fonte")

    coletas_por_fonte = dados_filtrados["fonte"].value_counts()

    st.bar_chart(coletas_por_fonte)
    st.subheader("Coletas por tipo de página")

    coletas_por_tipo = dados_filtrados["tipo_pagina_exibicao"].value_counts()

    st.bar_chart(coletas_por_tipo)


# ------------------------------------------------------------------
# ABA COM A TABELA
# ------------------------------------------------------------------

with aba_tabela:
    st.subheader("Respostas armazenadas")

    tabela = dados_filtrados[
        [
            "coletado_em",
            "empresa_exibicao",
            "alvo_id",
            "fonte",
            "tipo_pagina_exibicao",
            "numero_pagina",
            "status_http",
            "tamanho_bytes",
            "url_final",
            "referencia",
        ]
    ].copy()

    tabela["coletado_em"] = tabela["coletado_em"].dt.strftime("%d/%m/%Y %H:%M:%S UTC")

    tabela["tamanho_bytes"] = tabela["tamanho_bytes"].map(formatar_bytes)

    tabela = tabela.rename(
        columns={
            "coletado_em": "Coletado em",
            "empresa_exibicao": "Empresa",
            "alvo_id": "ID do alvo",
            "fonte": "Fonte",
            "tipo_pagina_exibicao": "Tipo de página",
            "numero_pagina": "Número da página",
            "status_http": "Status HTTP",
            "tamanho_bytes": "Tamanho",
            "url_final": "URL final",
            "referencia": "Arquivo de metadados",
        }
    )

    st.dataframe(
        tabela,
        hide_index=True,
        use_container_width=True,
        column_config={
            "URL final": st.column_config.LinkColumn("URL final"),
        },
    )


# ------------------------------------------------------------------
# ABA DE DETALHES
# ------------------------------------------------------------------

with aba_detalhes:
    st.subheader("Inspecionar uma coleta")

    referencias = dados_filtrados["referencia"].tolist()

    referencia_selecionada = st.selectbox(
        "Arquivo de metadados",
        options=referencias,
    )

    linha = dados_filtrados.loc[dados_filtrados["referencia"] == referencia_selecionada].iloc[0]

    st.json(
        {
            "versao_schema": int(linha["versao_schema"]),
            "alvo_id": linha["alvo_id"],
            "empresa_nome": linha["empresa_nome"],
            "numero_pagina": int(linha["numero_pagina"]),
            "tipo_pagina": linha["tipo_pagina"],
            "fonte": linha["fonte"],
            "url_solicitada": linha["url_solicitada"],
            "url_final": linha["url_final"],
            "status_http": int(linha["status_http"]),
            "tamanho_bytes": int(linha["tamanho_bytes"]),
            "coletado_em": linha["coletado_em"].isoformat(),
            "caminho_corpo": linha["caminho_corpo"],
            "referencia": linha["referencia"],
        }
    )

    st.caption(
        "O corpo HTML não é renderizado nesta tela. "
        "Isso evita executar conteúdo externo dentro do dashboard."
    )
