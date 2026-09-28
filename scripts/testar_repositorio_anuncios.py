"""Testa o repositório de anúncios usando o MongoDB real.

Este diagnóstico comprova que:

1. um anúncio novo pode ser salvo;
2. ele pode ser encontrado pela fonte e pelo ID externo;
3. uma nova observação atualiza o anúncio existente;
4. o UUID e a primeira observação são preservados;
5. não é criado um anúncio duplicado;
6. os dados fictícios são removidos no final.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import Fonte, StatusAnuncio
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    preparar_banco,
)

# Identificadores exclusivos desta demonstração.
ID_ANUNCIO_INICIAL = UUID("50000000-0000-4000-8000-000000000005")
ID_ANUNCIO_REOBSERVADO = UUID("60000000-0000-4000-8000-000000000006")
ID_EMPRESA_TESTE = UUID("70000000-0000-4000-8000-000000000007")

# A fonte e o ID externo formam a identidade da vaga na origem.
FONTE_TESTE = Fonte.PAGINA_CARREIRAS
ID_EXTERNO_TESTE = "diagnostico-vaga-001"

# Datas fixas deixam o resultado previsível.
PRIMEIRA_OBSERVACAO = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
SEGUNDA_OBSERVACAO = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)


def criar_primeira_observacao() -> AnuncioVaga:
    """Cria a primeira versão da vaga fictícia."""

    return AnuncioVaga(
        id=ID_ANUNCIO_INICIAL,
        fonte=FONTE_TESTE,
        id_externo=ID_EXTERNO_TESTE,
        url="https://empresa-demonstracao.example/vagas/001",
        url_candidatura="https://empresa-demonstracao.example/candidatura/001",
        empresa_id=ID_EMPRESA_TESTE,
        titulo_original="Pessoa Desenvolvedora Python",
        descricao_original="Desenvolvimento de aplicações em Python.",
        empresa_original="Empresa Demonstração",
        localidade_original="São Paulo - SP",
        salario_original="R$ 5.000 por mês",
        modalidade_original="Remoto",
        regime_original="CLT",
        requisitos_original="Python e MongoDB.",
        beneficios_original="Vale-refeição e plano de saúde.",
        publicado_em=date(2026, 8, 20),
        status=StatusAnuncio.ATIVO,
        hash_conteudo="a" * 64,
        referencia_bruta="diagnosticos/anuncio-001.html",
        primeira_observacao_em=PRIMEIRA_OBSERVACAO,
        ultima_observacao_em=PRIMEIRA_OBSERVACAO,
    )


def criar_segunda_observacao() -> AnuncioVaga:
    """Cria uma versão atualizada da mesma vaga."""

    return AnuncioVaga(
        id=ID_ANUNCIO_REOBSERVADO,
        fonte=FONTE_TESTE,
        id_externo=ID_EXTERNO_TESTE,
        url="https://empresa-demonstracao.example/vagas/001",
        url_candidatura="https://empresa-demonstracao.example/candidatura/001",
        empresa_id=ID_EMPRESA_TESTE,
        titulo_original="Pessoa Desenvolvedora Python Sênior",
        descricao_original="Desenvolvimento de aplicações Python e APIs.",
        empresa_original="Empresa Demonstração",
        localidade_original="São Paulo - SP",
        salario_original="R$ 7.000 por mês",
        modalidade_original="Híbrido",
        regime_original="CLT",
        requisitos_original="Python, APIs e MongoDB.",
        beneficios_original="Vale-refeição, plano de saúde e auxílio home office.",
        publicado_em=date(2026, 8, 20),
        status=StatusAnuncio.ALTERADO,
        hash_conteudo="b" * 64,
        referencia_bruta="diagnosticos/anuncio-001-atualizado.html",
        primeira_observacao_em=SEGUNDA_OBSERVACAO,
        ultima_observacao_em=SEGUNDA_OBSERVACAO,
    )


def executar_diagnostico() -> None:
    """Executa o diagnóstico completo no MongoDB real."""

    configuracoes = get_settings()

    with ConexaoMongoDB(configuracoes) as conexao:
        preparar_banco(conexao.banco)

        repositorio = RepositorioAnunciosMongoDB(conexao.banco)

        try:
            anuncio_inicial = criar_primeira_observacao()
            anuncio_salvo = repositorio.salvar(anuncio_inicial)

            print("1. Anúncio fictício salvo.")
            print("   UUID armazenado:", anuncio_salvo.id)

            anuncio_encontrado = repositorio.buscar_por_fonte(
                FONTE_TESTE,
                ID_EXTERNO_TESTE,
            )

            if anuncio_encontrado is None:
                raise RuntimeError("O anúncio não foi encontrado pela fonte.")

            print("2. Consulta pela fonte realizada.")
            print("   Título encontrado:", anuncio_encontrado.titulo_original)

            segunda_observacao = criar_segunda_observacao()
            anuncio_atualizado = repositorio.salvar(segunda_observacao)

            print("3. Segunda observação salva.")
            print("   Título atualizado:", anuncio_atualizado.titulo_original)

            if anuncio_atualizado.id != anuncio_salvo.id:
                raise RuntimeError("O UUID original do anúncio não foi preservado.")

            print("4. O UUID original foi preservado.")

            if anuncio_atualizado.primeira_observacao_em != PRIMEIRA_OBSERVACAO:
                raise RuntimeError("A primeira observação não foi preservada.")

            print("5. A data da primeira observação foi preservada.")

            anuncios_da_empresa = repositorio.listar_por_empresa(
                ID_EMPRESA_TESTE,
                limite=10,
            )

            anuncios_do_diagnostico = [
                anuncio for anuncio in anuncios_da_empresa if anuncio.id_externo == ID_EXTERNO_TESTE
            ]

            if len(anuncios_do_diagnostico) != 1:
                raise RuntimeError("Era esperado exatamente um anúncio, sem duplicação.")

            print("6. Quantidade de anúncios encontrados:", len(anuncios_do_diagnostico))
            print("7. Diagnóstico concluído com sucesso.")

        finally:
            # Remove apenas os anúncios com a identidade usada pelo diagnóstico.
            resultado_exclusao = conexao.banco["anuncios"].delete_many(
                {
                    "fonte": FONTE_TESTE.value,
                    "id_externo": ID_EXTERNO_TESTE,
                }
            )

            print(
                "8. Registros fictícios removidos:",
                resultado_exclusao.deleted_count,
            )


if __name__ == "__main__":
    executar_diagnostico()
