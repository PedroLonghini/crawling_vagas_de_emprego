"""Cria vagas canônicas a partir dos anúncios associados.

Sem --confirmar, o comando funciona somente como prévia.
Vagas existentes são reutilizadas e atualizadas com dados melhores.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    Senioridade,
)
from observatorio_vagas.domain.vaga import VagaCanonica
from observatorio_vagas.extraction import (
    ErroNormalizacaoVaga,
    converter_anuncio_em_vaga_canonica,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    ErroRepositorioVagasMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioVagasMongoDB,
)


def criar_parser() -> argparse.ArgumentParser:
    """Cria os argumentos de linha de comando."""

    parser = argparse.ArgumentParser(
        description=(
            "Cria vagas canônicas para anúncios associados "
            "a empresas. Sem --confirmar, o MongoDB não é alterado."
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
        help="autoriza salvar ou atualizar vagas canônicas",
    )

    return parser


def _converter_uuid(
    valor: str,
) -> UUID:
    """Converte o identificador textual em UUID."""

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


def _exibir_vaga(
    *,
    posicao: int,
    total: int,
    anuncio: AnuncioVaga,
    vaga: VagaCanonica,
    existente: VagaCanonica | None,
) -> None:
    """Apresenta os valores da vaga normalizada."""

    print()
    print(f"[{posicao}/{total}] Anúncio ID: {anuncio.id}")
    print(f"Vaga ID: {vaga.id}")
    print(f"Empresa ID: {vaga.empresa_id}")
    print(f"Título: {vaga.titulo_normalizado}")
    print(f"Localidade: {vaga.cidade or 'não encontrada'} / {vaga.pais}")
    print(f"Modalidade: {vaga.modalidade.value}")
    print(f"Regime: {vaga.regime.value}")
    print(f"Senioridade: {vaga.senioridade.value}")

    if vaga.salario is None:
        print("Salário: não encontrado")

    else:
        print(
            "Salário: "
            f"{vaga.salario.moeda} "
            f"{vaga.salario.minimo} - "
            f"{vaga.salario.maximo} "
            f"por {vaga.salario.periodo.value}"
        )

    if existente is None:
        print("Ação planejada: CRIAR VAGA CANÔNICA")

    else:
        print("Ação planejada: REUTILIZAR/ATUALIZAR VAGA EXISTENTE")


def _preparar_vaga_atualizada(
    *,
    existente: VagaCanonica,
    extraida: VagaCanonica,
) -> VagaCanonica:
    """Preserva dados existentes e aplica informações melhores."""

    modalidade = existente.modalidade

    if extraida.modalidade is not ModalidadeTrabalho.NAO_INFORMADO:
        modalidade = extraida.modalidade

    senioridade = existente.senioridade

    if extraida.senioridade is not Senioridade.NAO_INFORMADA:
        senioridade = extraida.senioridade

    possui_coordenadas_novas = extraida.latitude is not None and extraida.longitude is not None

    dados = existente.model_dump(mode="python")

    if extraida.salario is not None:
        dados["salario"] = extraida.salario

    dados["modalidade"] = modalidade
    dados["senioridade"] = senioridade

    if possui_coordenadas_novas:
        dados["latitude"] = extraida.latitude
        dados["longitude"] = extraida.longitude

    dados["atualizado_em"] = max(
        existente.atualizado_em,
        extraida.atualizado_em,
    )

    return VagaCanonica.model_validate(dados)


def _exibir_atualizacao(
    vaga: VagaCanonica,
) -> None:
    """Mostra os campos da vaga atualizada."""

    print(f"Vaga existente atualizada: {vaga.id}")
    print(f"Modalidade atualizada: {vaga.modalidade.value}")
    print(f"Senioridade atualizada: {vaga.senioridade.value}")

    if vaga.latitude is not None and vaga.longitude is not None:
        print(f"Geolocalização atual: {vaga.latitude},{vaga.longitude}")

    if vaga.salario is not None:
        print(
            "Salário atual: "
            f"{vaga.salario.moeda} "
            f"{vaga.salario.minimo} - "
            f"{vaga.salario.maximo} "
            f"por {vaga.salario.periodo.value}"
        )


def processar_anuncios(
    anuncios: Sequence[AnuncioVaga],
    *,
    confirmar: bool,
    repositorio_vagas: RepositorioVagasMongoDB,
) -> tuple[int, int, int]:
    """Cria vagas novas e atualiza vagas existentes."""

    criadas = 0
    reutilizadas = 0
    falhas = 0
    total = len(anuncios)

    modo = "GRAVAÇÃO CONFIRMADA" if confirmar else "SOMENTE PRÉVIA"

    print()
    print("# CRIAÇÃO DE VAGAS CANÔNICAS")
    print()
    print(f"Modo: {modo}")
    print(f"Anúncios selecionados: {total}")

    if not confirmar:
        print("Nenhuma alteração será feita no MongoDB.")

    for posicao, anuncio in enumerate(
        anuncios,
        start=1,
    ):
        try:
            vaga = converter_anuncio_em_vaga_canonica(anuncio)

            existente = repositorio_vagas.buscar_por_id(vaga.id)

            _exibir_vaga(
                posicao=posicao,
                total=total,
                anuncio=anuncio,
                vaga=vaga,
                existente=existente,
            )

            if existente is not None:
                vaga_atualizada = _preparar_vaga_atualizada(
                    existente=existente,
                    extraida=vaga,
                )

                if confirmar:
                    vaga_salva = repositorio_vagas.salvar(vaga_atualizada)

                    _exibir_atualizacao(vaga_salva)

                reutilizadas += 1
                continue

            if confirmar:
                vaga_salva = repositorio_vagas.salvar(vaga)

                print(f"Vaga salva: {vaga_salva.id}")

            criadas += 1

        except ErroNormalizacaoVaga as erro:
            falhas += 1

            print()
            print(f"[{posicao}/{total}] Anúncio ID: {anuncio.id}")
            print(f"ERRO SEGURO: {erro}")

    return (
        criadas,
        reutilizadas,
        falhas,
    )


def executar(
    argumentos: list[str] | None = None,
) -> int:
    """Executa a prévia ou gravação das vagas."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)

    try:
        configuracoes = get_settings()

        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)

            repositorio_vagas = RepositorioVagasMongoDB(conexao.banco)

            anuncios = _carregar_anuncios(
                repositorio_anuncios,
                anuncio_id=opcoes.anuncio_id,
                alvo_id=opcoes.alvo_id,
                limite=opcoes.limite,
            )

            (
                criadas,
                reutilizadas,
                falhas,
            ) = processar_anuncios(
                anuncios,
                confirmar=opcoes.confirmar,
                repositorio_vagas=repositorio_vagas,
            )

        print()
        print("# RESULTADO")
        print()
        print(f"Vagas novas: {criadas}")
        print(f"Vagas reutilizadas: {reutilizadas}")
        print(f"Falhas: {falhas}")

        if not opcoes.confirmar and (criadas or reutilizadas):
            print()
            print("Para gravar essas alterações, execute novamente acrescentando --confirmar.")

        return 1 if falhas else 0

    except (
        ErroConexaoMongoDB,
        ErroRepositorioAnunciosMongoDB,
        ErroRepositorioVagasMongoDB,
        LookupError,
        ValueError,
    ) as erro:
        print(f"ERRO: {erro}")
        return 1


if __name__ == "__main__":
    raise SystemExit(executar())
