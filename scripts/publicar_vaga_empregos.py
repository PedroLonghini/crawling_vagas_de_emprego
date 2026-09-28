"""Prepara e, mediante confirmação explícita, publica uma vaga no Empregos.

O comportamento padrão é uma simulação: consulta o MongoDB, avalia os
24 campos, aplica a política da fonte e calcula a identidade da operação,
mas não grava histórico nem realiza requisição HTTP.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from observatorio_vagas.config import Settings, get_settings
from observatorio_vagas.crawling.catalog import AlvoColeta, carregar_alvos_csv
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.publicacao import (
    calcular_chave_idempotencia_empregos,
)
from observatorio_vagas.domain.vaga import VagaCanonica
from observatorio_vagas.integrations.empregos import (
    ClienteEmpregos,
    PublicadorEmpregos,
    ResultadoPreparacaoEmpregos,
    preparar_publicacao_empregos,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    RepositorioVagasMongoDB,
    preparar_banco,
)


@dataclass(frozen=True, slots=True)
class ContextoPublicacao:
    """Objetos conferidos antes da criação do payload."""

    anuncio: AnuncioVaga
    vaga: VagaCanonica
    empresa: Empresa
    alvo: AlvoColeta
    preparacao: ResultadoPreparacaoEmpregos


def criar_parser() -> argparse.ArgumentParser:
    """Define uma interface explícita e segura para uma única vaga."""

    parser = argparse.ArgumentParser(
        description=(
            "Avalia e publica uma única vaga no Empregos. "
            "Sem --confirmar-publicacao, nenhuma escrita ou chamada HTTP é realizada."
        )
    )
    parser.add_argument(
        "--anuncio-id",
        required=True,
        help="UUID interno do anúncio de origem",
    )
    parser.add_argument(
        "--vaga-id",
        required=True,
        help="UUID interno da vaga canônica",
    )
    parser.add_argument(
        "--catalogo",
        type=Path,
        default=Path("config/catalogo_fontes.csv"),
        help="catálogo atual de fontes e autorizações",
    )
    parser.add_argument(
        "--company-id-empregos",
        help="identificador opcional da empresa fornecido pelo Empregos",
    )
    parser.add_argument(
        "--tracking-pixel-url",
        help="URL opcional de rastreamento autorizada",
    )
    parser.add_argument(
        "--mostrar-payload",
        action="store_true",
        help="mostra o JSON completo quando ele for aprovado",
    )
    parser.add_argument(
        "--confirmar-publicacao",
        action="store_true",
        help=(
            "autoriza a gravação do histórico e um único POST; "
            "também exige todas as travas do arquivo .env"
        ),
    )
    return parser


def _converter_uuid(valor: str, *, nome: str) -> UUID:
    """Converte um UUID com mensagem adequada à linha de comando."""

    try:
        return UUID(valor)
    except ValueError as erro:
        raise ValueError(f"{nome} não é um UUID válido: {valor}") from erro


def _indexar_catalogo(caminho: Path) -> dict[str, AlvoColeta]:
    """Carrega a fonte atual de verdade sobre autorizações."""

    return {alvo.alvo_id: alvo for alvo in carregar_alvos_csv(caminho)}


def _resolver_alvo(
    anuncio: AnuncioVaga,
    catalogo: Mapping[str, AlvoColeta],
) -> AlvoColeta:
    """Relaciona o anúncio à política atual sem autorização implícita."""

    if anuncio.alvo_id is None:
        raise ValueError("o anúncio não possui alvo_id e não pode ser publicado")

    alvo = catalogo.get(anuncio.alvo_id)

    if alvo is None:
        raise ValueError(f"o alvo não existe no catálogo atual: {anuncio.alvo_id}")

    if alvo.fonte is not anuncio.fonte:
        raise ValueError("a fonte do anúncio não corresponde à fonte do catálogo")

    return alvo


def carregar_contexto(
    *,
    repositorio_anuncios: RepositorioAnunciosMongoDB,
    repositorio_empresas: RepositorioEmpresasMongoDB,
    repositorio_vagas: RepositorioVagasMongoDB,
    catalogo: Mapping[str, AlvoColeta],
    anuncio_id: UUID,
    vaga_id: UUID,
    company_id_empregos: str | None,
    tracking_pixel_url: str | None,
) -> ContextoPublicacao:
    """Carrega e cruza anúncio, vaga, empresa e política."""

    anuncio = repositorio_anuncios.buscar_por_id(anuncio_id)

    if anuncio is None:
        raise LookupError(f"anúncio não encontrado: {anuncio_id}")

    vaga = repositorio_vagas.buscar_por_id(vaga_id)

    if vaga is None:
        raise LookupError(f"vaga canônica não encontrada: {vaga_id}")

    if anuncio.empresa_id is None:
        raise ValueError("o anúncio ainda não foi associado a uma empresa")

    if anuncio.empresa_id != vaga.empresa_id:
        raise ValueError("o anúncio e a vaga pertencem a empresas diferentes")

    empresa = repositorio_empresas.buscar_por_id(vaga.empresa_id)

    if empresa is None:
        raise LookupError(f"empresa não encontrada: {vaga.empresa_id}")

    alvo = _resolver_alvo(anuncio, catalogo)
    preparacao = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=None,
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=alvo.politica,
        company_id_empregos=company_id_empregos,
        tracking_pixel_url=tracking_pixel_url,
    )
    return ContextoPublicacao(
        anuncio=anuncio,
        vaga=vaga,
        empresa=empresa,
        alvo=alvo,
        preparacao=preparacao,
    )


def _resumir(valor: object, limite: int = 82) -> str:
    """Mantém a tabela legível sem modificar o payload."""

    if valor is None:
        return "-"

    if isinstance(valor, (dict, list, tuple)):
        texto = json.dumps(valor, ensure_ascii=False, default=str)
    else:
        texto = str(valor)

    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else f"{texto[: limite - 3]}..."


def imprimir_previa(contexto: ContextoPublicacao, *, mostrar_payload: bool) -> None:
    """Exibe dados, 24 campos, alertas e bloqueios antes da decisão."""

    preparacao = contexto.preparacao
    relatorio = preparacao.relatorio

    print()
    print("PRÉVIA DA PUBLICAÇÃO NO EMPREGOS")
    print("=" * 78)
    print(f"Anúncio ID: {contexto.anuncio.id}")
    print(f"Vaga ID: {contexto.vaga.id}")
    print(f"Empresa: {contexto.empresa.nome_exibicao}")
    print(f"Título: {contexto.vaga.titulo_normalizado}")
    print(f"Alvo: {contexto.alvo.alvo_id}")
    print(f"Política: {contexto.alvo.politica.status.value}")
    print(f"Republicação permitida: {'SIM' if contexto.alvo.habilitado_para_publicacao else 'NÃO'}")
    print(f"Licença: {contexto.alvo.politica.licenca_nome or 'não documentada'}")
    print(f"Comprovação: {contexto.alvo.politica.licenca_url or 'não documentada'}")
    print()
    print(
        f"Campos preenchidos: {len(relatorio.campos_preenchidos)}/"
        f"{relatorio.total_campos} ({relatorio.percentual_preenchimento:.2f}%)"
    )
    print(f"Campos obrigatórios OK: {'SIM' if relatorio.pronto_para_envio else 'NÃO'}")
    print()
    print(f"{'Campo':<34} {'Obrig.':<7} {'Situação':<20} Valor")
    print("-" * 110)

    for campo in relatorio.campos:
        obrigatorio = "sim" if campo.obrigatorio else "não"
        print(
            f"{campo.campo:<34} {obrigatorio:<7} {campo.situacao.value:<20} {_resumir(campo.valor)}"
        )

    if preparacao.elegibilidade.bloqueios:
        print()
        print("BLOQUEIOS")
        print("-" * 78)

        for bloqueio in preparacao.elegibilidade.bloqueios:
            campo = f" | campo={bloqueio.campo}" if bloqueio.campo else ""
            print(f"- {bloqueio.codigo.value}{campo}: {bloqueio.mensagem}")

    if relatorio.alertas:
        print()
        print("ALERTAS OPCIONAIS")
        print("-" * 78)

        for alerta in relatorio.alertas:
            print(f"- {alerta.campo}: {alerta.mensagem}")

    if mostrar_payload and preparacao.payload is not None:
        print()
        print("PAYLOAD APROVADO")
        print("-" * 78)
        print(json.dumps(preparacao.payload, ensure_ascii=False, indent=2))


def executar_publicacao(
    *,
    contexto: ContextoPublicacao,
    configuracoes: Settings,
    conexao: ConexaoMongoDB,
    confirmar_publicacao: bool,
) -> None:
    """Executa a simulação ou o fluxo persistente de um único POST."""

    cliente = ClienteEmpregos(configuracoes)
    repositorio = RepositorioPublicacoesEmpregosMongoDB(conexao.banco)
    publicador = PublicadorEmpregos(cliente, repositorio)

    if confirmar_publicacao:
        # Validamos antes de qualquer escrita. A validação é repetida pelo
        # coordenador imediatamente antes da reserva como defesa adicional.
        cliente.validar_configuracao_publicacao()
        preparar_banco(conexao.banco)

    resultado = publicador.publicar(
        contexto.preparacao,
        vaga_id=contexto.vaga.id,
        anuncio_id=contexto.anuncio.id,
        confirmar_publicacao=confirmar_publicacao,
    )
    envio = resultado.envio

    print()
    print("RESULTADO")
    print("=" * 78)

    if resultado.simulada:
        chave = calcular_chave_idempotencia_empregos(
            external_job_posting_id=envio.external_job_posting_id,
            operation_type=envio.operation_type,
        )
        print("Modo: SIMULAÇÃO")
        print("POST executado: NÃO")
        print("Histórico gravado: NÃO")
        print(f"External ID: {envio.external_job_posting_id}")
        print(f"Operação: {envio.operation_type}")
        print(f"Payload SHA-256: {envio.payload_sha256}")
        print(f"Chave idempotente: {chave}")
        print()
        print("Para publicar, revise a prévia e acrescente --confirmar-publicacao.")
        return

    assert resultado.operacao is not None
    print("Modo: PUBLICAÇÃO CONFIRMADA")
    print(f"Situação: {resultado.operacao.situacao.value}")
    print(f"Operação ID: {resultado.operacao.id}")
    print(f"Status HTTP: {resultado.operacao.status_http}")
    print(f"Request ID: {resultado.operacao.request_id or 'não informado'}")
    print(f"Resultado reutilizado: {'SIM' if resultado.reutilizada else 'NÃO'}")


def executar(argumentos: list[str] | None = None) -> int:
    """Ponto testável do comando."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)

    try:
        anuncio_id = _converter_uuid(opcoes.anuncio_id, nome="anuncio-id")
        vaga_id = _converter_uuid(opcoes.vaga_id, nome="vaga-id")
        catalogo = _indexar_catalogo(opcoes.catalogo)
        configuracoes = get_settings()

        with ConexaoMongoDB(configuracoes) as conexao:
            contexto = carregar_contexto(
                repositorio_anuncios=RepositorioAnunciosMongoDB(conexao.banco),
                repositorio_empresas=RepositorioEmpresasMongoDB(conexao.banco),
                repositorio_vagas=RepositorioVagasMongoDB(conexao.banco),
                catalogo=catalogo,
                anuncio_id=anuncio_id,
                vaga_id=vaga_id,
                company_id_empregos=opcoes.company_id_empregos,
                tracking_pixel_url=opcoes.tracking_pixel_url,
            )
            imprimir_previa(contexto, mostrar_payload=opcoes.mostrar_payload)

            if not contexto.preparacao.pronto_para_envio:
                print()
                print("PUBLICAÇÃO BLOQUEADA: nenhuma chamada ou gravação foi realizada.")
                return 2

            executar_publicacao(
                contexto=contexto,
                configuracoes=configuracoes,
                conexao=conexao,
                confirmar_publicacao=opcoes.confirmar_publicacao,
            )

    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 1

    return 0


def main() -> None:
    """Executa o comando no terminal."""

    raise SystemExit(executar())


if __name__ == "__main__":
    main()
