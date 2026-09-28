"""Testes das funções utilizadas pela demonstração.

Os testes executam os cálculos automaticamente e comparam
os resultados encontrados com os resultados esperados.

Se alguém alterar o CSV ou as funções incorretamente,
um destes testes deverá falhar e avisar sobre o problema.
"""

from pathlib import Path

import pandas as pd
import pytest

from observatorio_vagas.demo.analytics import (
    calcular_indicadores,
    carregar_vagas,
)

# __file__ representa o caminho deste próprio arquivo de teste.
#
# parents[3] volta três níveis:
#
# test_analytics.py -> demo -> unit -> tests -> raiz do projeto
#
# Depois acrescentamos o caminho do CSV.
CAMINHO_DEMO = Path(__file__).resolve().parents[3] / "data" / "demo" / "vagas_demo.csv"


@pytest.fixture
def dados() -> pd.DataFrame:
    """Carrega a base fictícia para ser utilizada nos testes.

    Uma fixture evita repetir o mesmo comando de carregamento
    dentro de todos os testes.
    """

    return carregar_vagas(CAMINHO_DEMO)


def test_carrega_as_doze_vagas(dados: pd.DataFrame) -> None:
    """Verifica se todas as vagas fictícias foram carregadas."""

    # O CSV que criamos possui exatamente 12 registros.
    assert len(dados) == 12

    # Cada vaga precisa ter um identificador único.
    #
    # Se dois registros tiverem o mesmo id_vaga,
    # is_unique será False e o teste falhará.
    assert dados["id_vaga"].is_unique


def test_cria_as_colunas_calculadas(dados: pd.DataFrame) -> None:
    """Verifica se a preparação criou as novas informações."""

    # salario_medio não existe originalmente como cálculo pronto.
    # Ele precisa ser criado pela função carregar_vagas.
    assert "salario_medio" in dados.columns

    # status_comparacao também precisa ser criado durante a leitura.
    assert "status_comparacao" in dados.columns

    # A data precisa ter sido convertida de texto para data real.
    assert pd.api.types.is_datetime64_any_dtype(dados["publicada_em"])


def test_calcula_os_indicadores_principais(
    dados: pd.DataFrame,
) -> None:
    """Verifica os números que aparecerão no topo do painel."""

    indicadores = calcular_indicadores(dados)

    # Nossa base possui 12 vagas e 4 empresas fictícias.
    assert indicadores["total_vagas"] == 12
    assert indicadores["total_empresas"] == 4

    # A mediana das faixas salariais da base é R$ 4.750.
    assert indicadores["salario_mediano"] == 4750.0

    # Oito das doze vagas estão marcadas como presentes no Empregos.
    #
    # pytest.approx é utilizado porque divisões podem produzir
    # números decimais muito longos.
    assert indicadores["cobertura_empregos"] == pytest.approx(8 / 12 * 100)

    # Todas as 12 vagas fictícias possuem uma faixa salarial.
    assert indicadores["percentual_com_salario"] == 100.0


def test_classifica_a_comparacao_das_fontes(
    dados: pd.DataFrame,
) -> None:
    """Verifica a comparação entre Empregos e fontes externas."""

    # value_counts conta quantas vezes cada classificação aparece.
    contagem = dados["status_comparacao"].value_counts().to_dict()

    assert contagem == {
        # Cinco vagas foram encontradas nas duas origens.
        "Encontrada em ambas": 5,
        # Três vagas aparecem somente no Empregos.
        "Somente Empregos": 3,
        # Quatro vagas aparecem somente nas fontes externas.
        "Somente externa": 4,
    }
