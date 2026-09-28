"""Funções responsáveis pelos cálculos da demonstração.

Este arquivo não cria gráficos nem mostra botões.

A responsabilidade dele é:

1. abrir o arquivo CSV;
2. verificar se os dados estão corretos;
3. preparar os dados;
4. calcular os indicadores que serão mostrados no painel.
"""

# Path ajuda o Python a trabalhar com caminhos de arquivos.
#
# Usar Path é melhor do que juntar caminhos manualmente com barras,
# porque funciona tanto no Windows quanto em outros sistemas.
from pathlib import Path

# Pandas é a biblioteca que trabalha com dados em formato de tabela.
#
# Por convenção, praticamente todo projeto importa pandas usando o nome "pd".
import pandas as pd

# Este conjunto guarda os nomes das colunas que obrigatoriamente
# precisam existir no nosso arquivo CSV.
#
# Se alguém apagar ou escrever incorretamente uma dessas colunas,
# o programa mostrará um erro explicando quais estão faltando.
COLUNAS_OBRIGATORIAS = {
    "id_vaga",
    "empresa",
    "cargo",
    "area",
    "cidade",
    "estado",
    "modalidade",
    "salario_min",
    "salario_max",
    "publicada_em",
    "presente_empregos",
    "presente_externa",
    "fonte_externa",
}


def carregar_vagas(caminho: str | Path) -> pd.DataFrame:
    """Abre o CSV, valida as colunas e prepara os dados.

    Parâmetros
    ----------
    caminho:
        Local em que o arquivo CSV está salvo.

    Retorno
    -------
    pd.DataFrame:
        Uma tabela do Pandas contendo as vagas preparadas.
    """

    # Converte o caminho recebido para um objeto Path.
    #
    # Isso permite usar recursos como is_file(), que verifica
    # se o arquivo realmente existe.
    caminho_csv = Path(caminho)

    # Antes de tentar abrir o CSV, verificamos se ele existe.
    #
    # Isso produz uma mensagem mais fácil de entender do que
    # deixar o Pandas gerar um erro interno enorme.
    if not caminho_csv.is_file():
        raise FileNotFoundError(f"O arquivo de demonstração não foi encontrado: {caminho_csv}")

    # read_csv abre o arquivo CSV e transforma suas linhas e
    # colunas em uma tabela do Pandas chamada DataFrame.
    dados = pd.read_csv(caminho_csv)

    # Comparamos as colunas obrigatórias com as colunas
    # encontradas no arquivo.
    colunas_ausentes = COLUNAS_OBRIGATORIAS.difference(dados.columns)

    # Se o conjunto não estiver vazio, alguma coluna está faltando.
    if colunas_ausentes:
        # sorted coloca os nomes em ordem alfabética.
        #
        # join transforma vários nomes em um único texto,
        # separados por vírgula.
        nomes = ", ".join(sorted(colunas_ausentes))

        raise ValueError(f"O CSV não possui as seguintes colunas obrigatórias: {nomes}")

    # Converte os textos da coluna publicada_em para datas reais.
    #
    # Por exemplo:
    # "2026-08-01" deixa de ser um texto comum e passa a ser
    # entendido pelo Python como uma data.
    #
    # errors="raise" faz o programa avisar se encontrar
    # alguma data escrita incorretamente.
    dados["publicada_em"] = pd.to_datetime(
        dados["publicada_em"],
        errors="raise",
    )

    # Percorremos as duas colunas relacionadas ao salário.
    for coluna in ("salario_min", "salario_max"):
        # Converte os valores para números.
        #
        # errors="coerce" transforma valores inválidos em NaN.
        # NaN significa que aquele valor não foi informado.
        dados[coluna] = pd.to_numeric(
            dados[coluna],
            errors="coerce",
        )

    # Fazemos a conversão das colunas que representam
    # respostas de verdadeiro ou falso.
    for coluna in ("presente_empregos", "presente_externa"):
        dados[coluna] = dados[coluna].map(_converter_para_booleano)

    # Calculamos o centro da faixa salarial de cada vaga.
    #
    # Exemplo:
    # salario_min = 6000
    # salario_max = 8000
    # salario_medio = 7000
    #
    # axis=1 significa que o cálculo será feito linha por linha.
    #
    # skipna=False impede que o programa invente uma média
    # quando apenas um dos dois salários estiver preenchido.
    dados["salario_medio"] = dados[["salario_min", "salario_max"]].mean(
        axis=1,
        skipna=False,
    )

    # apply executa a função _classificar_comparacao
    # para cada vaga da tabela.
    #
    # A nova coluna mostrará se a vaga está:
    # - nas duas fontes;
    # - somente no Empregos;
    # - somente em uma fonte externa.
    dados["status_comparacao"] = dados.apply(
        _classificar_comparacao,
        axis=1,
    )

    # Entrega a tabela preparada para outras partes do projeto.
    return dados


def calcular_indicadores(dados: pd.DataFrame) -> dict[str, int | float | None]:
    """Calcula os números principais que aparecerão no painel."""

    # nunique conta quantos identificadores diferentes existem.
    #
    # Isso evita contar duas vezes uma vaga repetida.
    total_vagas = int(dados["id_vaga"].nunique())

    # Se não houver vagas, não podemos calcular porcentagens.
    #
    # Por isso retornamos tudo zerado imediatamente.
    if total_vagas == 0:
        return {
            "total_vagas": 0,
            "total_empresas": 0,
            "salario_mediano": None,
            "cobertura_empregos": 0.0,
            "percentual_com_salario": 0.0,
        }

    # Conta quantas empresas diferentes aparecem na tabela.
    total_empresas = int(dados["empresa"].nunique())

    # A mediana representa o valor central dos salários.
    # Ela costuma ser mais segura do que a média quando existem
    # salários muito altos ou muito baixos.
    salario_mediano = dados["salario_medio"].median()

    # isna verifica se o salário está ausente.
    # Se a mediana estiver ausente, guardamos None.
    # Caso exista, transformamos o resultado em float.
    salario_mediano_final = None if pd.isna(salario_mediano) else float(salario_mediano)

    # Em Python:
    # True funciona como 1.
    # False funciona como 0.
    #
    # Portanto, a média de vários valores True e False
    # representa a porcentagem de valores verdadeiros.
    cobertura_empregos = float(dados["presente_empregos"].mean() * 100)

    # notna retorna True para salários que foram informados.
    #
    # Calculamos qual porcentagem das vagas possui salário.
    percentual_com_salario = float(dados["salario_medio"].notna().mean() * 100)

    # O dicionário organiza os indicadores por nome.
    return {
        "total_vagas": total_vagas,
        "total_empresas": total_empresas,
        "salario_mediano": salario_mediano_final,
        "cobertura_empregos": cobertura_empregos,
        "percentual_com_salario": percentual_com_salario,
    }


def _converter_para_booleano(valor: object) -> bool:
    """Converte diferentes representações para True ou False."""

    # Se o valor já for True ou False, podemos devolvê-lo.
    if isinstance(valor, bool):
        return valor

    # Transformamos o valor em texto, removemos espaços
    # e convertemos as letras para minúsculas.
    texto = str(valor).strip().lower()

    # Estas palavras serão consideradas verdadeiras.
    if texto in {"true", "1", "sim", "s", "verdadeiro"}:
        return True

    # Estas palavras serão consideradas falsas.
    if texto in {"false", "0", "nao", "não", "n", "falso"}:
        return False

    # Se o conteúdo não estiver em nenhum dos grupos,
    # avisamos exatamente qual valor está incorreto.
    raise ValueError(f"Valor de verdadeiro/falso inválido encontrado no CSV: {valor!r}")


def _classificar_comparacao(linha: pd.Series) -> str:
    """Classifica onde uma vaga foi encontrada."""

    # Extraímos os dois valores para deixar a condição
    # abaixo mais fácil de ler.
    no_empregos = bool(linha["presente_empregos"])
    na_fonte_externa = bool(linha["presente_externa"])

    # Se as duas condições forem verdadeiras,
    # a vaga foi encontrada nas duas origens.
    if no_empregos and na_fonte_externa:
        return "Encontrada em ambas"

    # Se somente a primeira condição for verdadeira,
    # a vaga está somente no Empregos.
    if no_empregos:
        return "Somente Empregos"

    # Se somente a segunda condição for verdadeira,
    # a vaga está somente na fonte externa.
    if na_fonte_externa:
        return "Somente externa"

    # Essa situação não deveria acontecer na demonstração,
    # mas deixamos uma classificação para evitar erros.
    return "Sem fonte identificada"
