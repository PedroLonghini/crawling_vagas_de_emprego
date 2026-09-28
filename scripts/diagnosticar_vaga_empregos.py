"""Consulta o MongoDB e exibe a prontidão para o Empregos.

Este comando é somente leitura.

Ele não:

- altera documentos;
- cria empresas;
- modifica vagas;
- envia informações para a API do Empregos;
- utiliza token da API.

Além dos 24 campos, ele verifica se a fonte permite republicação.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.catalog import (
    AlvoColeta,
    carregar_alvos_csv,
)
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.elegibilidade import (
    ResultadoElegibilidadePublicacao,
)
from observatorio_vagas.domain.prontidao import (
    CampoProntidao,
    RelatorioProntidao,
)
from observatorio_vagas.integrations.empregos import (
    preparar_publicacao_empregos,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioVagasMongoDB,
)


def criar_parser() -> argparse.ArgumentParser:
    """Cria e documenta os argumentos aceitos pelo comando."""

    parser = argparse.ArgumentParser(
        description=(
            "Consulta vagas no MongoDB, mostra os 24 campos "
            "da API do Empregos e verifica a política da fonte. "
            "Nenhuma vaga é enviada."
        )
    )

    # O usuário precisa escolher entre:
    #
    # 1. listar os registros;
    # 2. diagnosticar um anúncio específico.
    modo = parser.add_mutually_exclusive_group(required=True)

    modo.add_argument(
        "--listar",
        action="store_true",
        help="lista anúncios e vagas disponíveis para diagnóstico",
    )

    modo.add_argument(
        "--anuncio-id",
        help="UUID interno do anúncio que será diagnosticado",
    )

    parser.add_argument(
        "--vaga-id",
        help="UUID interno da vaga canônica que será diagnosticada",
    )

    parser.add_argument(
        "--limite",
        type=int,
        default=20,
        help="quantidade de anúncios exibidos por --listar (padrão: 20)",
    )

    # O catálogo é a fonte atual de verdade sobre autorizações.
    #
    # Não confiamos em uma autorização antiga salva no MongoDB porque
    # uma fonte pode ser bloqueada depois de uma coleta.
    parser.add_argument(
        "--catalogo",
        type=Path,
        default=Path("config/catalogo_fontes.csv"),
        help=("CSV contendo os alvos e suas políticas. Padrão: config/catalogo_fontes.csv"),
    )

    parser.add_argument(
        "--company-id-empregos",
        help="identificador opcional da empresa dentro do Empregos",
    )

    parser.add_argument(
        "--tracking-pixel-url",
        help="URL opcional de rastreamento que entrará no payload",
    )

    parser.add_argument(
        "--saida-json",
        type=Path,
        help="arquivo opcional no qual o payload aprovado será salvo",
    )

    return parser


def _converter_uuid(
    valor: str,
    *,
    nome: str,
) -> UUID:
    """Converte um texto em UUID com mensagem amigável."""

    try:
        return UUID(valor)

    except ValueError as erro:
        raise ValueError(f"{nome} não é um UUID válido: {valor}") from erro


def _indexar_catalogo(
    caminho_catalogo: Path,
) -> dict[str, AlvoColeta]:
    """Carrega o catálogo e permite buscas rápidas por alvo_id."""

    alvos = carregar_alvos_csv(caminho_catalogo)

    # O próprio carregador do catálogo já impede IDs duplicados.
    #
    # O dicionário evita procurar linha por linha toda vez que um
    # anúncio precisar ser diagnosticado.
    return {alvo.alvo_id: alvo for alvo in alvos}


def _resolver_alvo_do_anuncio(
    anuncio: AnuncioVaga,
    catalogo: Mapping[str, AlvoColeta],
) -> AlvoColeta:
    """Encontra a política atual da fonte de um anúncio."""

    # Anúncios antigos podem não possuir alvo_id.
    #
    # Nesse caso falhamos de maneira fechada: não inventamos uma
    # autorização e não escolhemos outra fonte automaticamente.
    if anuncio.alvo_id is None:
        raise ValueError(
            "o anúncio não possui alvo_id; "
            "reprocesse a resposta bruta antes de avaliar a publicação"
        )

    alvo = catalogo.get(anuncio.alvo_id)

    if alvo is None:
        raise ValueError(f"o alvo do anúncio não existe no catálogo atual: {anuncio.alvo_id}")

    # Também conferimos se o tipo geral da fonte continua igual.
    #
    # Isso detecta alterações incorretas no CSV.
    if alvo.fonte is not anuncio.fonte:
        raise ValueError(
            "a fonte do anúncio não corresponde à fonte do catálogo: "
            f"anúncio={anuncio.fonte.value}, "
            f"catálogo={alvo.fonte.value}"
        )

    return alvo


def _resumir_texto(
    valor: object,
    limite: int = 90,
) -> str:
    """Prepara valores grandes para a tabela do terminal."""

    if valor is None:
        return "-"

    if isinstance(
        valor,
        (dict, list, tuple),
    ):
        texto = json.dumps(
            valor,
            ensure_ascii=False,
            default=str,
        )
    else:
        texto = str(valor)

    # Remove quebras de linha para não destruir a tabela.
    texto = " ".join(texto.split())

    if len(texto) <= limite:
        return texto

    # O valor é reduzido somente para a exibição.
    #
    # O payload continua preservando o conteúdo completo.
    return f"{texto[: limite - 3]}..."


def _imprimir_linha_campo(
    campo: CampoProntidao,
) -> None:
    """Mostra uma linha da tabela de prontidão."""

    obrigatorio = "sim" if campo.obrigatorio else "não"
    valor = _resumir_texto(campo.valor)

    print(f"{campo.campo:<34} {obrigatorio:<6} {campo.situacao.value:<20} {valor}")


def imprimir_relatorio(
    relatorio: RelatorioProntidao,
) -> None:
    """Exibe resumo, campos obrigatórios, erros e alertas."""

    print()
    print("RELATÓRIO DOS 24 CAMPOS DA API DO EMPREGOS")
    print("=" * 78)

    print(f"Campos avaliados:      {relatorio.total_campos}")
    print(f"Campos preenchidos:    {len(relatorio.campos_preenchidos)}")
    print(f"Percentual preenchido: {relatorio.percentual_preenchimento:.2f}%")
    print(f"Campos obrigatórios OK: {'SIM' if relatorio.pronto_para_envio else 'NÃO'}")
    print("Descrição da vaga:     campo 'description'")
    print("Descrição da empresa:  campo 'company.description'")

    print()
    print(f"{'Campo':<34} {'Obrig.':<6} {'Situação':<20} Valor")

    print("-" * 110)

    for campo in relatorio.campos:
        _imprimir_linha_campo(campo)

    if relatorio.erros:
        print()
        print("CAMPOS OBRIGATÓRIOS QUE BLOQUEIAM O PAYLOAD")
        print("-" * 78)

        for problema in relatorio.erros:
            print(f"- {problema.campo}: {problema.mensagem}")

    if relatorio.alertas:
        print()
        print("ALERTAS SOBRE CAMPOS OPCIONAIS")
        print("-" * 78)

        for problema in relatorio.alertas:
            print(f"- {problema.campo}: {problema.mensagem}")


def imprimir_elegibilidade(
    resultado: ResultadoElegibilidadePublicacao,
) -> None:
    """Mostra a decisão final, incluindo regras fora do payload."""

    print()
    print("DECISÃO FINAL DE ELEGIBILIDADE")
    print("=" * 78)

    print(f"Elegível para publicação: {'SIM' if resultado.elegivel else 'NÃO'}")

    if resultado.elegivel:
        print("A vaga passou pelas regras obrigatórias e pela política da fonte.")
        return

    print()
    print("BLOQUEIOS ENCONTRADOS")
    print("-" * 78)

    for bloqueio in resultado.bloqueios:
        complemento_campo = f" | campo={bloqueio.campo}" if bloqueio.campo is not None else ""

        print(f"- {bloqueio.codigo.value}{complemento_campo}: {bloqueio.mensagem}")


def listar_registros(
    *,
    repositorio_anuncios: RepositorioAnunciosMongoDB,
    repositorio_empresas: RepositorioEmpresasMongoDB,
    repositorio_vagas: RepositorioVagasMongoDB,
    catalogo: Mapping[str, AlvoColeta],
    limite: int,
) -> None:
    """Lista os IDs e a situação atual das fontes."""

    if limite < 1 or limite > 100:
        raise ValueError("limite deve estar entre 1 e 100")

    anuncios = repositorio_anuncios.listar_recentes(
        limite=limite,
    )

    if not anuncios:
        print("Nenhum anúncio foi encontrado no MongoDB.")
        return

    print()
    print("ANÚNCIOS E VAGAS DISPONÍVEIS")
    print("=" * 78)

    for anuncio in anuncios:
        print()
        print(f"Anúncio ID: {anuncio.id}")
        print(f"Título original: {anuncio.titulo_original}")
        print(f"Fonte: {anuncio.fonte.value}")
        print(f"Alvo ID: {anuncio.alvo_id or 'não informado'}")

        alvo = catalogo.get(anuncio.alvo_id) if anuncio.alvo_id is not None else None

        if alvo is None:
            print("Política: não foi possível localizar o alvo no catálogo")
            print("Republicação permitida: NÃO")
        else:
            print(f"Política: {alvo.politica.status.value}")
            print(f"Domínio autorizado: {alvo.politica.dominio}")
            print(f"Coleta permitida: {'SIM' if alvo.habilitado_para_coleta else 'NÃO'}")
            print(f"Republicação permitida: {'SIM' if alvo.habilitado_para_publicacao else 'NÃO'}")
            print(f"Licença: {alvo.politica.licenca_nome or 'não documentada'}")
            print(f"Comprovação: {alvo.politica.licenca_url or 'não documentada'}")

        if anuncio.empresa_id is None:
            print("Empresa: ainda não associada")
            print("Vagas canônicas: indisponíveis até resolver a empresa")
            continue

        empresa = repositorio_empresas.buscar_por_id(anuncio.empresa_id)

        if empresa is None:
            print(f"Empresa ID: {anuncio.empresa_id} (documento não encontrado)")
            continue

        print(f"Empresa: {empresa.nome_exibicao}")
        print(f"Empresa ID: {empresa.id}")

        vagas = repositorio_vagas.listar_por_empresa(
            empresa.id,
            limite=10,
        )

        if not vagas:
            print("Vagas canônicas: nenhuma encontrada")
            continue

        print("Vagas canônicas da empresa:")

        for vaga in vagas:
            print(f"  - {vaga.id} | {vaga.titulo_normalizado}")


def diagnosticar_registro(
    *,
    repositorio_anuncios: RepositorioAnunciosMongoDB,
    repositorio_empresas: RepositorioEmpresasMongoDB,
    repositorio_vagas: RepositorioVagasMongoDB,
    catalogo: Mapping[str, AlvoColeta],
    anuncio_id: UUID,
    vaga_id: UUID,
    company_id_empregos: str | None,
    tracking_pixel_url: str | None,
    saida_json: Path | None,
) -> None:
    """Carrega os documentos e exibe o diagnóstico completo."""

    anuncio = repositorio_anuncios.buscar_por_id(anuncio_id)

    if anuncio is None:
        raise LookupError(f"anúncio não encontrado: {anuncio_id}")

    # Descobre a política atual usando o alvo_id que foi
    # preservado durante a extração.
    alvo = _resolver_alvo_do_anuncio(
        anuncio,
        catalogo,
    )

    vaga = repositorio_vagas.buscar_por_id(vaga_id)

    if vaga is None:
        raise LookupError(f"vaga canônica não encontrada: {vaga_id}")

    if anuncio.empresa_id is None:
        raise ValueError("o anúncio ainda não foi associado a uma empresa")

    # Impede combinar o anúncio de uma empresa
    # com a vaga canônica de outra empresa.
    if anuncio.empresa_id != vaga.empresa_id:
        raise ValueError("o anúncio e a vaga canônica pertencem a empresas diferentes")

    empresa = repositorio_empresas.buscar_por_id(vaga.empresa_id)

    if empresa is None:
        raise LookupError(f"empresa não encontrada: {vaga.empresa_id}")

    # Primeiro avaliamos os 24 campos e tentamos preparar
    # o conteúdo do payload.
    #
    # Nenhuma chamada HTTP é realizada.
    preparacao = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=None,
        anuncio=anuncio,
        vaga=vaga,
        # A preparação agora exige a política da fonte.
        politica_fonte=alvo.politica,
        company_id_empregos=company_id_empregos,
        tracking_pixel_url=tracking_pixel_url,
    )

    # Depois combinamos os campos obrigatórios com:
    #
    # - política da fonte;
    # - domínio;
    # - status da vaga;
    # - data de expiração;
    # - proibição de usar o Empregos como fonte.
    # A própria preparação já calculou a elegibilidade.
    #
    # Assim não corremos o risco de utilizar duas regras diferentes.
    elegibilidade = preparacao.elegibilidade
    print()
    print("DADOS SELECIONADOS")
    print("=" * 78)

    print(f"Empresa: {empresa.nome_exibicao}")
    print(f"Anúncio: {anuncio.titulo_original}")
    print(f"Vaga canônica: {vaga.titulo_normalizado}")
    print(f"Alvo ID: {alvo.alvo_id}")
    print(f"Política da fonte: {alvo.politica.status.value}")
    print(f"Domínio autorizado: {alvo.politica.dominio}")
    print(f"Licença: {alvo.politica.licenca_nome or 'não documentada'}")
    print(f"Comprovação: {alvo.politica.licenca_url or 'não documentada'}")

    imprimir_relatorio(preparacao.relatorio)

    imprimir_elegibilidade(elegibilidade)

    # Sem payload estrutural, faltam campos obrigatórios.
    if preparacao.payload is None:
        print()
        print("O payload não foi criado porque existem campos obrigatórios ausentes ou inválidos.")
        return

    # Mesmo que todos os campos estejam preenchidos,
    # uma política proibida, domínio incorreto, status encerrado
    # ou data expirada bloqueiam a liberação do JSON.
    if not elegibilidade.elegivel:
        print()
        print(
            "O payload possui os campos necessários, "
            "mas não foi liberado por uma regra de elegibilidade."
        )
        return

    texto_json = json.dumps(
        preparacao.payload,
        ensure_ascii=False,
        indent=2,
    )

    print()
    print("PAYLOAD JSON APROVADO")
    print("=" * 78)
    print(texto_json)

    # O arquivo só é escrito quando:
    #
    # 1. os obrigatórios estão preenchidos;
    # 2. a política permite republicação;
    # 3. todas as outras regras foram aprovadas;
    # 4. o usuário informou --saida-json.
    if saida_json is not None:
        saida_json.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        saida_json.write_text(
            texto_json,
            encoding="utf-8",
        )

        print()
        print(f"JSON salvo em: {saida_json.resolve()}")


def executar(
    argumentos: list[str] | None = None,
) -> int:
    """Executa o comando e devolve seu código de saída."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)

    if opcoes.anuncio_id and not opcoes.vaga_id:
        parser.error("--vaga-id é obrigatório junto com --anuncio-id")

    # O catálogo é carregado antes da conexão com o banco.
    #
    # Se ele estiver inválido, o programa não continua.
    try:
        catalogo = _indexar_catalogo(opcoes.catalogo)

    except (
        OSError,
        TypeError,
        ValueError,
    ) as erro:
        print(f"ERRO AO CARREGAR O CATÁLOGO: {erro}")
        return 1

    configuracoes = get_settings()

    try:
        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio_anuncios = RepositorioAnunciosMongoDB(conexao.banco)

            repositorio_empresas = RepositorioEmpresasMongoDB(conexao.banco)

            repositorio_vagas = RepositorioVagasMongoDB(conexao.banco)

            if opcoes.listar:
                listar_registros(
                    repositorio_anuncios=(repositorio_anuncios),
                    repositorio_empresas=(repositorio_empresas),
                    repositorio_vagas=(repositorio_vagas),
                    catalogo=catalogo,
                    limite=opcoes.limite,
                )

                return 0

            anuncio_id = _converter_uuid(
                opcoes.anuncio_id,
                nome="anuncio-id",
            )

            vaga_id = _converter_uuid(
                opcoes.vaga_id,
                nome="vaga-id",
            )

            diagnosticar_registro(
                repositorio_anuncios=(repositorio_anuncios),
                repositorio_empresas=(repositorio_empresas),
                repositorio_vagas=(repositorio_vagas),
                catalogo=catalogo,
                anuncio_id=anuncio_id,
                vaga_id=vaga_id,
                company_id_empregos=(opcoes.company_id_empregos),
                tracking_pixel_url=(opcoes.tracking_pixel_url),
                saida_json=opcoes.saida_json,
            )

    except (
        LookupError,
        RuntimeError,
        ValueError,
    ) as erro:
        # Não exibimos a URI nem credenciais.
        print(f"ERRO: {erro}")
        return 1

    return 0


def main() -> None:
    """Ponto de entrada executado pelo Python."""

    raise SystemExit(executar())


if __name__ == "__main__":
    main()
