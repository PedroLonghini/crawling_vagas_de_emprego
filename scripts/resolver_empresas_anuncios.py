"""Resolve empresas dos anúncios coletados e associa os registros.

Por segurança, o comando executa em modo de prévia por padrão.
Somente a opção --confirmar autoriza gravações no MongoDB.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.extraction import (
    ConflitoResolucaoEmpresa,
    ErroResolucaoEmpresa,
    extrair_empresa_do_anuncio,
    resolver_e_associar_empresa,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    ErroRepositorioEmpresasMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
)


def criar_parser() -> argparse.ArgumentParser:
    """Cria os argumentos aceitos pelo comando."""

    parser = argparse.ArgumentParser(
        description=(
            "Resolve as empresas dos anúncios coletados. "
            "Sem --confirmar, nenhuma informação é alterada."
        )
    )

    selecao = parser.add_mutually_exclusive_group()

    selecao.add_argument(
        "--anuncio-id",
        help=("UUID de um anúncio específico; não pode ser usado junto com --alvo-id"),
    )

    selecao.add_argument(
        "--alvo-id",
        help=("processa somente anúncios pertencentes ao alvo informado no catálogo"),
    )

    parser.add_argument(
        "--limite",
        type=int,
        default=20,
        help="quantidade máxima de anúncios analisados (padrão: 20)",
    )

    parser.add_argument(
        "--confirmar",
        action="store_true",
        help=("autoriza criar empresas e atualizar empresa_id dos anúncios"),
    )

    return parser


def _converter_uuid(
    valor: str,
) -> UUID:
    """Converte o argumento em UUID com mensagem compreensível."""

    try:
        return UUID(valor)

    except ValueError as erro:
        raise ValueError(f"anuncio-id não é um UUID válido: {valor}") from erro


def _carregar_anuncios(
    repositorio: RepositorioAnunciosMongoDB,
    *,
    anuncio_id: str | None,
    alvo_id: str | None,
    limite: int,
) -> list[AnuncioVaga]:
    """Carrega anúncios usando uma seleção segura."""

    if limite < 1 or limite > 10000:
        raise ValueError("limite deve estar entre 1 e 10000")

    if anuncio_id is not None:
        identificador = _converter_uuid(anuncio_id)

        anuncio = repositorio.buscar_por_id(identificador)

        if anuncio is None:
            raise LookupError(f"anúncio não encontrado: {identificador}")

        return [anuncio]

    if alvo_id is not None:
        alvo_normalizado = alvo_id.strip()

        if not alvo_normalizado:
            raise ValueError("alvo-id não pode ser vazio")

        anuncios = repositorio.listar_por_alvo(
            alvo_normalizado,
            limite=limite,
        )

        if not anuncios:
            raise LookupError(f"nenhum anúncio encontrado para o alvo: {alvo_normalizado}")

        return anuncios

    return repositorio.listar_recentes(
        limite=limite,
    )


def _buscar_candidatas(
    empresa: Empresa,
    repositorio: RepositorioEmpresasMongoDB,
) -> tuple[Empresa, ...]:
    """Busca correspondências sem realizar qualquer escrita."""

    candidatas: dict[UUID, Empresa] = {}

    if empresa.cnpj is not None:
        por_cnpj = repositorio.buscar_por_cnpj(empresa.cnpj)

        if por_cnpj is not None:
            candidatas[por_cnpj.id] = por_cnpj

    if empresa.dominio is not None:
        por_dominio = repositorio.buscar_por_dominio(empresa.dominio)

        if por_dominio is not None:
            candidatas[por_dominio.id] = por_dominio

    por_id = repositorio.buscar_por_id(empresa.id)

    if por_id is not None:
        candidatas[por_id.id] = por_id

    return tuple(candidatas.values())


def _valor_ou_ausente(
    valor: object,
) -> object:
    """Substitui valores ausentes por um texto amigável."""

    if valor is None:
        return "não encontrado"

    return valor


def _exibir_cabecalho(
    *,
    confirmar: bool,
    quantidade: int,
) -> None:
    """Explica se o comando pode escrever no banco."""

    modo = "GRAVAÇÃO CONFIRMADA" if confirmar else "SOMENTE PRÉVIA"

    print()
    print("# RESOLUÇÃO DE EMPRESAS")
    print()
    print(f"Modo: {modo}")
    print(f"Anúncios selecionados: {quantidade}")

    if not confirmar:
        print("Nenhuma alteração será feita no MongoDB.")


def _exibir_empresa(
    *,
    posicao: int,
    total: int,
    anuncio: AnuncioVaga,
    empresa: Empresa,
) -> None:
    """Mostra os dados extraídos antes da gravação."""

    print()
    print(f"[{posicao}/{total}] Anúncio: {anuncio.id}")

    print(f"Título: {anuncio.titulo_original}")

    print(f"Empresa extraída: {empresa.nome_exibicao}")

    print(f"Domínio: {_valor_ou_ausente(empresa.dominio)}")

    print(f"CNPJ: {_valor_ou_ausente(empresa.cnpj)}")

    print(f"País: {empresa.pais}")


def _processar_anuncio(
    anuncio: AnuncioVaga,
    *,
    confirmar: bool,
    repositorio_empresas: RepositorioEmpresasMongoDB,
    repositorio_anuncios: RepositorioAnunciosMongoDB,
) -> None:
    """Mostra a decisão e efetua a associação autorizada."""

    empresa_extraida = extrair_empresa_do_anuncio(anuncio)

    candidatas = _buscar_candidatas(
        empresa_extraida,
        repositorio_empresas,
    )

    if len(candidatas) > 1:
        raise ConflitoResolucaoEmpresa(
            "CNPJ, domínio e identificador apontam para empresas diferentes"
        )

    if anuncio.empresa_id is not None:
        acao = "VALIDAR/ATUALIZAR EMPRESA JÁ ASSOCIADA"

    elif candidatas:
        acao = f"REUTILIZAR EMPRESA {candidatas[0].id} E ASSOCIAR ANÚNCIO"

    else:
        acao = f"CRIAR EMPRESA {empresa_extraida.id} E ASSOCIAR ANÚNCIO"

    print(f"Ação planejada: {acao}")

    # Sem --confirmar, encerramos antes de qualquer escrita.
    if not confirmar:
        return

    resultado = resolver_e_associar_empresa(
        anuncio,
        repositorio_empresas=repositorio_empresas,
        repositorio_anuncios=repositorio_anuncios,
    )

    print(f"Empresa salva: {resultado.empresa.id}")

    print(f"Anúncio associado: {resultado.anuncio.empresa_id}")

    empresa_criada = "sim" if resultado.empresa_criada else "não"

    empresa_atualizada = "sim" if resultado.empresa_atualizada else "não"

    print(f"Empresa criada: {empresa_criada}")

    print(f"Empresa atualizada: {empresa_atualizada}")


def processar_anuncios(
    anuncios: Sequence[AnuncioVaga],
    *,
    confirmar: bool,
    repositorio_empresas: RepositorioEmpresasMongoDB,
    repositorio_anuncios: RepositorioAnunciosMongoDB,
) -> tuple[int, int]:
    """Processa os anúncios e isola falhas individuais."""

    sucessos = 0
    falhas = 0
    total = len(anuncios)

    _exibir_cabecalho(
        confirmar=confirmar,
        quantidade=total,
    )

    for posicao, anuncio in enumerate(
        anuncios,
        start=1,
    ):
        try:
            empresa = extrair_empresa_do_anuncio(anuncio)

            _exibir_empresa(
                posicao=posicao,
                total=total,
                anuncio=anuncio,
                empresa=empresa,
            )

            _processar_anuncio(
                anuncio,
                confirmar=confirmar,
                repositorio_empresas=repositorio_empresas,
                repositorio_anuncios=repositorio_anuncios,
            )

            sucessos += 1

        except ErroResolucaoEmpresa as erro:
            falhas += 1

            print()
            print(f"[{posicao}/{total}] Anúncio: {anuncio.id}")

            print(f"ERRO SEGURO: {erro}")

    return (
        sucessos,
        falhas,
    )


def executar(
    argumentos: list[str] | None = None,
) -> int:
    """Abre a conexão e executa a operação."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)

    try:
        configuracoes = get_settings()

        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)

            repositorio_empresas = RepositorioEmpresasMongoDB(conexao.banco)

            anuncios = _carregar_anuncios(
                repositorio_anuncios,
                anuncio_id=opcoes.anuncio_id,
                alvo_id=opcoes.alvo_id,
                limite=opcoes.limite,
            )

            sucessos, falhas = processar_anuncios(
                anuncios,
                confirmar=opcoes.confirmar,
                repositorio_empresas=repositorio_empresas,
                repositorio_anuncios=repositorio_anuncios,
            )

        print()
        print("# RESULTADO")
        print()
        print(f"Sucessos: {sucessos}")
        print(f"Falhas: {falhas}")

        if not opcoes.confirmar and sucessos:
            print()
            print("Para gravar exatamente essas associações, execute novamente")
            print("o mesmo comando acrescentando a opção --confirmar.")

        return 1 if falhas else 0

    except (
        ErroConexaoMongoDB,
        ErroRepositorioAnunciosMongoDB,
        ErroRepositorioEmpresasMongoDB,
        LookupError,
        ValueError,
    ) as erro:
        print(f"ERRO: {erro}")
        return 1


if __name__ == "__main__":
    raise SystemExit(executar())
