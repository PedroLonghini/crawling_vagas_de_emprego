"""Testa o repositório de vagas canônicas no MongoDB real."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.domain.vaga import SalarioNormalizado, VagaCanonica
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioVagasMongoDB,
    preparar_banco,
)

# Identificadores exclusivos deste diagnóstico.
VAGA_ID = UUID("90000000-0000-4000-8000-000000000009")
EMPRESA_ID = UUID("91000000-0000-4000-8000-000000000091")

# Datas fixas permitem conferir se criado_em será preservado.
DATA_CRIACAO = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
DATA_ATUALIZACAO = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)


def criar_salario(
    minimo: str,
    maximo: str,
) -> SalarioNormalizado:
    """Cria uma faixa salarial mensal em reais."""

    return SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        moeda="BRL",
        periodo=PeriodoSalario.MES,
        minimo=Decimal(minimo),
        maximo=Decimal(maximo),
        mensal_minimo=Decimal(minimo),
        mensal_maximo=Decimal(maximo),
        regra_normalizacao="valor mensal publicado",
    )


def criar_vaga_inicial() -> VagaCanonica:
    """Cria a primeira versão da vaga fictícia."""

    return VagaCanonica(
        id=VAGA_ID,
        empresa_id=EMPRESA_ID,
        titulo_normalizado="Pessoa Desenvolvedora Python",
        descricao_normalizada="Desenvolvimento de aplicações em Python.",
        familia_cargo="Desenvolvimento de software",
        area="Tecnologia",
        senioridade=Senioridade.PLENO,
        cidade="São Paulo",
        estado="SP",
        pais="BR",
        modalidade=ModalidadeTrabalho.REMOTO,
        regime=RegimeContratacao.CLT,
        salario=criar_salario("5000.00", "7000.00"),
        quantidade_vagas=1,
        tecnologias=("Python", "MongoDB"),
        requisitos=("Experiência com Python",),
        responsabilidades=("Desenvolver aplicações",),
        beneficios=("Plano de saúde",),
        criado_em=DATA_CRIACAO,
        atualizado_em=DATA_CRIACAO,
    )


def criar_vaga_atualizada() -> VagaCanonica:
    """Cria uma versão atualizada da mesma vaga."""

    return VagaCanonica(
        id=VAGA_ID,
        empresa_id=EMPRESA_ID,
        titulo_normalizado="Pessoa Desenvolvedora Python Sênior",
        descricao_normalizada="Desenvolvimento de APIs em Python.",
        familia_cargo="Desenvolvimento de software",
        area="Tecnologia",
        senioridade=Senioridade.SENIOR,
        cidade="São Paulo",
        estado="SP",
        pais="BR",
        modalidade=ModalidadeTrabalho.HIBRIDO,
        regime=RegimeContratacao.CLT,
        salario=criar_salario("7000.00", "9000.00"),
        quantidade_vagas=2,
        tecnologias=("Python", "MongoDB", "FastAPI"),
        requisitos=("Experiência com Python e APIs",),
        responsabilidades=("Desenvolver e revisar APIs",),
        beneficios=("Plano de saúde", "Auxílio home office"),
        criado_em=DATA_ATUALIZACAO,
        atualizado_em=DATA_ATUALIZACAO,
    )


def executar_diagnostico() -> None:
    """Executa inserção, atualização, consulta e limpeza."""

    configuracoes = get_settings()

    with ConexaoMongoDB(configuracoes) as conexao:
        preparar_banco(conexao.banco)
        repositorio = RepositorioVagasMongoDB(conexao.banco)

        try:
            vaga_salva = repositorio.salvar(criar_vaga_inicial())

            print("1. Vaga canônica salva.")
            print("   Título:", vaga_salva.titulo_normalizado)

            vaga_encontrada = repositorio.buscar_por_id(VAGA_ID)

            if vaga_encontrada is None:
                raise RuntimeError("A vaga não foi encontrada pelo UUID.")

            print("2. Vaga encontrada pelo UUID.")
            print("   Tecnologias:", ", ".join(vaga_encontrada.tecnologias))

            vaga_atualizada = repositorio.salvar(criar_vaga_atualizada())

            print("3. Vaga atualizada.")
            print("   Novo título:", vaga_atualizada.titulo_normalizado)

            if vaga_atualizada.criado_em != DATA_CRIACAO:
                raise RuntimeError("A data original de criação não foi preservada.")

            print("4. A data original de criação foi preservada.")

            if vaga_atualizada.salario is None:
                raise RuntimeError("O salário normalizado não foi recuperado.")

            print(
                "5. Faixa salarial:",
                vaga_atualizada.salario.mensal_minimo,
                "até",
                vaga_atualizada.salario.mensal_maximo,
                vaga_atualizada.salario.moeda,
            )

            vagas_da_empresa = repositorio.listar_por_empresa(
                EMPRESA_ID,
                limite=10,
            )

            vagas_do_diagnostico = [vaga for vaga in vagas_da_empresa if vaga.id == VAGA_ID]

            if len(vagas_do_diagnostico) != 1:
                raise RuntimeError("Era esperada exatamente uma vaga canônica.")

            print("6. A atualização não criou uma vaga duplicada.")
            print("7. Diagnóstico concluído com sucesso.")

        finally:
            resultado = conexao.banco["vagas_canonicas"].delete_one({"_id": VAGA_ID})

            print("8. Registros fictícios removidos:", resultado.deleted_count)


if __name__ == "__main__":
    executar_diagnostico()
