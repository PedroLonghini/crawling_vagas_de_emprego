"""Testa o repositório de empresas usando o MongoDB real.

Este arquivo não faz parte do crawler.

Ele serve apenas para comprovar que:

1. o projeto consegue conectar ao MongoDB;
2. uma empresa pode ser salva;
3. a empresa pode ser encontrada por ID;
4. a empresa pode ser encontrada por CNPJ;
5. a empresa pode ser encontrada por domínio;
6. o registro fictício é removido depois do teste.
"""

# UUID representa o identificador único da empresa.
#
# Usaremos um UUID fixo para que este teste seja previsível
# e para sabermos exatamente qual registro poderá ser apagado.
from uuid import UUID

# get_settings carrega as configurações do arquivo .env.
from observatorio_vagas.config import get_settings

# Empresa é o nosso modelo de domínio validado pelo Pydantic.
from observatorio_vagas.domain.empresa import Empresa

# Importamos a conexão, a preparação do banco e o repositório
# pela interface pública criada no passo anterior.
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioEmpresasMongoDB,
    preparar_banco,
)

# Este é um identificador reservado somente para esta demonstração.
#
# Não utilizaremos o ID de nenhuma empresa real.
ID_EMPRESA_TESTE = UUID("00000000-0000-4000-8000-000000000001")


def executar_diagnostico() -> None:
    """Executa a gravação, as consultas e a limpeza do teste."""

    # Carrega:
    #
    # - OBS_MONGODB_URI;
    # - OBS_MONGODB_DATABASE;
    # - OBS_MONGODB_SERVER_SELECTION_TIMEOUT_MS.
    #
    # Esses valores vêm do arquivo .env.
    configuracoes = get_settings()

    # Criamos uma empresa completamente fictícia.
    #
    # O domínio ".invalid" é reservado para exemplos e testes.
    # Portanto, não representa o site de uma empresa verdadeira.
    empresa_teste = Empresa(
        id=ID_EMPRESA_TESTE,
        razao_social="Empresa de Demonstração do Observatório Ltda.",
        nome_fantasia="Empresa Demonstração",
        cnpj="99.999.999/9999-99",
        dominio="empresa-demonstracao.invalid",
        setor="Tecnologia",
        porte="Teste",
        cidade="São Paulo",
        estado="SP",
        pais="BR",
    )

    # A palavra "with" garante que a conexão seja fechada
    # automaticamente no final da execução.
    #
    # Ao entrar neste bloco, ConexaoMongoDB também executa um ping.
    with ConexaoMongoDB(configuracoes) as conexao:
        # Garante que as coleções e os índices necessários existam.
        #
        # Se eles já existirem, o MongoDB apenas os mantém.
        preparar_banco(conexao.banco)

        # Entregamos o banco selecionado para o repositório.
        #
        # O repositório não precisa conhecer a URI nem o túnel SSH.
        repositorio = RepositorioEmpresasMongoDB(conexao.banco)

        try:
            # Salva a empresa fictícia.
            #
            # Como o repositório utiliza upsert:
            #
            # - se o ID não existe, ele insere;
            # - se o ID já existe, ele atualiza.
            repositorio.salvar(empresa_teste)

            print("1. Empresa fictícia salva com sucesso.")

            # Consulta pelo UUID interno.
            empresa_por_id = repositorio.buscar_por_id(empresa_teste.id)

            # Se o resultado for None, significa que o documento
            # não foi encontrado depois da gravação.
            if empresa_por_id is None:
                raise RuntimeError("A empresa não foi encontrada pelo ID.")

            print(
                "2. Consulta por ID:",
                empresa_por_id.nome_exibicao,
            )

            # O CNPJ é informado com pontuação de propósito.
            #
            # O repositório deve remover pontos, barra e traço
            # antes de fazer a consulta.
            empresa_por_cnpj = repositorio.buscar_por_cnpj("99.999.999/9999-99")

            if empresa_por_cnpj is None:
                raise RuntimeError("A empresa não foi encontrada pelo CNPJ.")

            print(
                "3. Consulta por CNPJ:",
                empresa_por_cnpj.cnpj,
            )

            # Informamos uma URL completa de propósito.
            #
            # O repositório deve extrair apenas o domínio.
            empresa_por_dominio = repositorio.buscar_por_dominio(
                "https://www.empresa-demonstracao.invalid/"
            )

            if empresa_por_dominio is None:
                raise RuntimeError("A empresa não foi encontrada pelo domínio.")

            print(
                "4. Consulta por domínio:",
                empresa_por_dominio.dominio,
            )

            print("5. Diagnóstico do repositório concluído com sucesso.")

        finally:
            # O finally é executado mesmo se alguma consulta acima falhar.
            #
            # Apagamos somente o documento que possui o UUID reservado
            # para esta demonstração.
            resultado_exclusao = conexao.banco["empresas"].delete_one({"_id": empresa_teste.id})

            print(
                "6. Registros fictícios removidos:",
                resultado_exclusao.deleted_count,
            )


# Esta condição impede que o diagnóstico seja executado
# apenas por alguém importar este arquivo.
#
# Ele só roda quando chamamos o arquivo diretamente no terminal.
if __name__ == "__main__":
    executar_diagnostico()
