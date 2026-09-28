"""Testa o repositório de execuções de coleta no MongoDB real."""

from datetime import UTC, datetime
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.coleta import ExecucaoColeta, MetricasColeta
from observatorio_vagas.domain.enums import Fonte, StatusColeta
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioColetasMongoDB,
    preparar_banco,
)

EXECUCAO_ID = UUID("b0000000-0000-4000-8000-00000000000b")

DATA_INICIAL = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
DATA_INCORRETA_DA_ATUALIZACAO = datetime(2026, 8, 20, 10, 30, tzinfo=UTC)
DATA_FINAL = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)


def criar_execucao_inicial() -> ExecucaoColeta:
    """Cria uma coleta que ainda está em andamento."""

    return ExecucaoColeta(
        id=EXECUCAO_ID,
        fonte=Fonte.GUPY,
        versao_conector="0.1.0",
        status=StatusColeta.EXECUTANDO,
        iniciado_em=DATA_INICIAL,
        checkpoint={"pagina": 1},
        metricas=MetricasColeta(
            requisicoes=1,
            recebidos=0,
        ),
    )


def criar_execucao_finalizada() -> ExecucaoColeta:
    """Cria a atualização final da mesma coleta."""

    return ExecucaoColeta(
        id=EXECUCAO_ID,
        fonte=Fonte.GUPY,
        versao_conector="0.1.0",
        status=StatusColeta.CONCLUIDA,
        iniciado_em=DATA_INCORRETA_DA_ATUALIZACAO,
        finalizado_em=DATA_FINAL,
        checkpoint={"pagina": 10, "concluido": True},
        metricas=MetricasColeta(
            requisicoes=8,
            recebidos=100,
            novos=40,
            alterados=10,
            inalterados=45,
            invalidos=5,
            erros=2,
        ),
    )


def executar_diagnostico() -> None:
    """Executa inserção, atualização, consulta e limpeza."""

    configuracoes = get_settings()

    with ConexaoMongoDB(configuracoes) as conexao:
        preparar_banco(conexao.banco)
        repositorio = RepositorioColetasMongoDB(conexao.banco)

        try:
            execucao_salva = repositorio.salvar(criar_execucao_inicial())

            print("1. Execução de coleta iniciada.")
            print("   Status:", execucao_salva.status.value)

            execucao_final = repositorio.salvar(criar_execucao_finalizada())

            print("2. Execução de coleta atualizada.")
            print("   Status:", execucao_final.status.value)

            if execucao_final.iniciado_em != DATA_INICIAL:
                raise RuntimeError("O horário inicial da coleta não foi preservado.")

            print("3. O horário inicial foi preservado.")

            execucao_encontrada = repositorio.buscar_por_id(EXECUCAO_ID)

            if execucao_encontrada is None:
                raise RuntimeError("A execução não foi encontrada pelo UUID.")

            print("4. Execução encontrada pelo UUID.")
            print(
                "   Recebidos:",
                execucao_encontrada.metricas.recebidos,
            )
            print(
                "   Novos:",
                execucao_encontrada.metricas.novos,
            )
            print(
                "   Alterados:",
                execucao_encontrada.metricas.alterados,
            )
            print(
                "   Inválidos:",
                execucao_encontrada.metricas.invalidos,
            )
            print(
                "   Erros:",
                execucao_encontrada.metricas.erros,
            )

            execucoes_gupy = repositorio.listar_recentes(
                fonte=Fonte.GUPY,
                limite=20,
            )

            execucoes_do_diagnostico = [
                execucao for execucao in execucoes_gupy if execucao.id == EXECUCAO_ID
            ]

            if len(execucoes_do_diagnostico) != 1:
                raise RuntimeError("Era esperada exatamente uma execução.")

            print("5. A coleta não foi duplicada.")
            print("6. Diagnóstico concluído com sucesso.")

        finally:
            resultado = conexao.banco["execucoes_coleta"].delete_one({"_id": EXECUCAO_ID})

            print(
                "7. Registros fictícios removidos:",
                resultado.deleted_count,
            )


if __name__ == "__main__":
    executar_diagnostico()
