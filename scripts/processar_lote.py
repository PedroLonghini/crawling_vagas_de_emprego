"""Orquestra o processamento em lote das fontes autorizadas.

O comando nunca envia vagas para a API do Empregos.

Sem --confirmar:
- não altera o MongoDB;
- executa somente a prévia da extração.

Com --confirmar:
- salva anúncios;
- resolve empresas;
- cria ou atualiza vagas canônicas.

A coleta pela internet exige explicitamente a opção --coletar.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.catalog import (
    AlvoColeta,
    ErroCatalogoFontes,
    FalhaLinhaCatalogo,
    carregar_alvos_csv_tolerante,
)
from observatorio_vagas.crawling.inventario import (
    ErroInventarioBruto,
    RegistroInventarioBruto,
    carregar_inventario_bruto,
)
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio
from observatorio_vagas.extraction.data_publicacao import ontem_brasilia
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    RepositorioAnunciosMongoDB,
)

# Suporta tanto execução direta no PowerShell quanto importação pelos testes.
if __package__:
    from .processar_e_salvar_anuncios import executar as executar_extracao
else:
    from processar_e_salvar_anuncios import executar as executar_extracao

RAIZ_PROJETO = Path(__file__).resolve().parents[1]

DIRETORIO_SCRIPTS = RAIZ_PROJETO / "scripts"

# Códigos documentados por processar_e_salvar_anuncios.py.
CODIGO_EXTRACAO_COM_FALHAS = 2
CODIGO_SEM_ANUNCIOS = 10


def _trabalhadores_padrao() -> int:
    """Escolhe paralelismo útil sem ocupar todos os núcleos do computador."""

    nucleos = os.cpu_count() or 2
    return max(1, min(12, nucleos - 1))


def _converter_data_hora(
    valor: str,
) -> datetime:
    """Converte uma data ISO 8601 com fuso horário."""

    try:
        resultado = datetime.fromisoformat(valor)

    except ValueError as erro:
        raise argparse.ArgumentTypeError("coletado-desde precisa usar uma data ISO 8601") from erro

    if resultado.tzinfo is None or resultado.utcoffset() is None:
        raise argparse.ArgumentTypeError("coletado-desde precisa informar o fuso horário")

    return resultado


def criar_parser() -> argparse.ArgumentParser:
    """Cria os argumentos aceitos pelo lote."""

    parser = argparse.ArgumentParser(
        description=(
            "Processa várias fontes de vagas com isolamento por alvo e por momento da coleta."
        )
    )
    parser.add_argument(
        "--limite-anuncios",
        type=int,
        default=None,
        help="detalhes por fonte, separados de limite_paginas (listagens); entre 1 e 10000",
    )
    parser.add_argument("--javascript", action="store_true", help="renderiza HTML com Chromium")

    parser.add_argument(
        "--catalogo",
        type=Path,
        default=Path("config/catalogo_fontes.csv"),
        help=("arquivo CSV contendo as fontes do lote (padrão: config/catalogo_fontes.csv)"),
    )

    parser.add_argument(
        "--diretorio-raw",
        type=Path,
        default=Path("data/raw"),
        help="armazenamento bruto (padrão: data/raw)",
    )

    origem = parser.add_mutually_exclusive_group(required=True)

    origem.add_argument(
        "--coletar",
        action="store_true",
        help=("executa o crawler e utiliza somente as respostas desta nova coleta"),
    )

    origem.add_argument(
        "--coletado-desde",
        type=_converter_data_hora,
        help=("reprocessa respostas existentes coletadas desde a data ISO 8601 informada"),
    )

    parser.add_argument(
        "--limite",
        type=int,
        default=10000,
        help=("quantidade máxima de anúncios processados por alvo (padrão: 10000)"),
    )

    parser.add_argument(
        "--alvos-por-coleta",
        type=int,
        default=200,
        help=("quantidade máxima de sites em cada processo do crawler (padrão: 200)"),
    )

    parser.add_argument(
        "--trabalhadores-posprocessamento",
        type=int,
        default=_trabalhadores_padrao(),
        help=(
            "quantidade de anúncios resolvidos em paralelo após a coleta "
            f"(padrão: {_trabalhadores_padrao()}; entre 1 e 32)"
        ),
    )

    parser.add_argument(
        "--confirmar",
        action="store_true",
        help=("autoriza gravações no MongoDB; não autoriza publicação no Empregos"),
    )
    parser.add_argument(
        "--somente-republicaveis",
        action="store_true",
        help=(
            "executa somente alvos cuja política atual permite republicação; "
            "fontes somente_coleta ficam fora do lote"
        ),
    )

    datas = parser.add_mutually_exclusive_group()
    datas.add_argument(
        "--publicados-ontem", action="store_true", help="publicados ontem em Brasília"
    )
    datas.add_argument(
        "--publicados-em", type=date.fromisoformat, help="data de publicação YYYY-MM-DD"
    )
    return parser


def _resolver_caminho(
    caminho: Path,
) -> Path:
    """Transforma um caminho relativo em caminho do projeto."""

    if caminho.is_absolute():
        return caminho.resolve()

    return (RAIZ_PROJETO / caminho).resolve()


def _motivo_alvo_ignorado(
    alvo: AlvoColeta,
    *,
    somente_republicaveis: bool = False,
) -> str:
    """Explica por que um alvo não poderá ser coletado."""

    restricao = encontrar_restricao_dominio(alvo.dominio)

    if restricao is not None:
        return f"{restricao.nome}: {restricao.motivo}"

    if not alvo.ativa:
        return "desativado no catálogo"

    if somente_republicaveis and not alvo.habilitado_para_publicacao:
        return "republicação não autorizada pela política da fonte"

    return f"política da fonte: {alvo.politica.status.value}"


def _mostrar_plano(
    *,
    catalogo: Path,
    executaveis: tuple[AlvoColeta, ...],
    ignorados: tuple[AlvoColeta, ...],
    falhas_catalogo: tuple[FalhaLinhaCatalogo, ...],
    confirmar: bool,
    coletar: bool,
    alvos_por_coleta: int,
    somente_republicaveis: bool,
) -> None:
    """Mostra todas as decisões antes da execução."""

    print()
    print("# PLANO DO PROCESSAMENTO EM LOTE")
    print()
    print(f"Catálogo: {catalogo}")
    print(f"Coleta pela internet: {'SIM' if coletar else 'NÃO'}")
    print(f"Gravação no MongoDB: {'SIM' if confirmar else 'NÃO'}")
    print("Envio para API do Empregos: NÃO")
    print(f"Filtro somente republicáveis: {'SIM' if somente_republicaveis else 'NÃO'}")
    print(f"Alvos executáveis: {len(executaveis)}")
    print(f"Alvos ignorados: {len(ignorados)}")
    print(f"Linhas inválidas ignoradas: {len(falhas_catalogo)}")

    if coletar:
        print(f"Alvos por processo de coleta: {alvos_por_coleta}")

    if executaveis:
        print()
        print("## ALVOS EXECUTÁVEIS")

        for alvo in executaveis:
            print(
                "-",
                alvo.alvo_id,
                "|",
                alvo.empresa_nome,
                "|",
                alvo.politica.status.value,
                "|",
                alvo.url_inicial,
            )

    if ignorados:
        print()
        print("## ALVOS IGNORADOS")

        for alvo in ignorados:
            print(
                "-",
                alvo.alvo_id,
                "|",
                _motivo_alvo_ignorado(
                    alvo,
                    somente_republicaveis=somente_republicaveis,
                ),
            )

    if falhas_catalogo:
        print()
        print("## LINHAS INVÁLIDAS IGNORADAS")

        for falha in falhas_catalogo:
            print(
                "- linha",
                falha.numero_linha,
                "|",
                falha.alvo_id,
                "|",
                falha.mensagem,
            )


def _executar_python(
    *,
    etapa: str,
    argumentos: Sequence[str],
) -> int:
    """Executa uma etapa Python sem utilizar shell=True."""

    comando = [
        sys.executable,
        *argumentos,
    ]

    print()
    print("=" * 72)
    print(f"ETAPA: {etapa}")
    print("=" * 72)
    sys.stdout.flush()

    try:
        resultado = subprocess.run(
            comando,
            cwd=RAIZ_PROJETO,
            check=False,
        )

    except OSError as erro:
        print()
        print(f"Não foi possível iniciar a etapa {etapa}: {erro}")
        return 70

    return resultado.returncode


def _executar_crawler(
    *,
    catalogo: Path,
    etiqueta: str,
    limite_respostas: int,
    limite_anuncios: int | None = None,
    javascript: bool = False,
    diretorio_raw: Path | None = None,
) -> int:
    """Executa uma coleta limitada a um fragmento do catálogo."""

    instante = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    cobertura = RAIZ_PROJETO / "outputs" / "cobertura" / f"coleta_{instante}.json"
    return _executar_python(
        etapa=f"COLETA DAS FONTES — {etiqueta}",
        argumentos=(
            "-m",
            "scrapy",
            "crawl",
            "catalogo_fontes",
            "-a",
            f"catalogo={catalogo}",
            "-a",
            f"relatorio_cobertura={cobertura}",
            "-s",
            f"CLOSESPIDER_PAGECOUNT={limite_respostas}",
            *(
                ("-s", f"RAW_STORAGE_DIRECTORY={diretorio_raw}")
                if diretorio_raw is not None
                else ()
            ),
            *(("-s", "JAVASCRIPT_ENABLED=True") if javascript else ()),
            *(("-a", f"limite_anuncios={limite_anuncios}") if limite_anuncios is not None else ()),
        ),
    )


def _dividir_alvos(
    alvos: tuple[AlvoColeta, ...],
    tamanho: int,
) -> tuple[tuple[AlvoColeta, ...], ...]:
    """Divide alvos em blocos previsíveis sem carregar geradores tardios."""

    return tuple(
        alvos[inicio : inicio + tamanho]
        for inicio in range(
            0,
            len(alvos),
            tamanho,
        )
    )


def _escrever_catalogo_temporario(
    caminho: Path,
    alvos: tuple[AlvoColeta, ...],
) -> None:
    """Cria um catálogo estrito contendo somente alvos já validados."""

    with caminho.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(
            (
                "alvo_id",
                "empresa_nome",
                "fonte",
                "url_inicial",
                "ativa",
                "limite_paginas",
                "status_politica",
                "licenca_nome",
                "licenca_url",
                "atribuicao_obrigatoria",
                "republicacao_permitida",
                "autorizacao_escrita",
                "referencia_autorizacao",
            )
        )

        for alvo in alvos:
            escritor.writerow(
                (
                    alvo.alvo_id,
                    alvo.empresa_nome,
                    alvo.fonte.value,
                    alvo.url_inicial,
                    "true" if alvo.ativa else "false",
                    alvo.limite_paginas,
                    alvo.politica.status.value,
                    alvo.politica.licenca_nome,
                    alvo.politica.licenca_url,
                    "true" if alvo.politica.atribuicao_obrigatoria else "false",
                    "true" if alvo.politica.republicacao_permitida else "false",
                    "true" if alvo.politica.autorizacao_escrita else "false",
                    alvo.politica.referencia_autorizacao,
                )
            )


def _calcular_limite_respostas(
    alvos: tuple[AlvoColeta, ...],
) -> int:
    """Reserva páginas do catálogo, robots, redirecionamentos e erros."""

    paginas_configuradas = sum(alvo.limite_paginas for alvo in alvos)

    # A fábrica limita as páginas normais por alvo. A margem evita que
    # robots.txt, redirecionamentos e respostas de erro encerrem o bloco
    # antes que seus últimos alvos sejam tentados.
    margem_operacional = (3 * len(alvos)) + 20

    return paginas_configuradas + margem_operacional


def _coletar_em_blocos(
    *,
    alvos: tuple[AlvoColeta, ...],
    tamanho_bloco: int,
    limite_anuncios: int | None = None,
    javascript: bool = False,
    diretorio_raw: Path | None = None,
) -> dict[str, int]:
    """Coleta blocos e recupera individualmente um bloco que falhar."""

    falhas: dict[str, int] = {}
    blocos = _dividir_alvos(
        alvos,
        tamanho_bloco,
    )

    with TemporaryDirectory(prefix="observatorio_vagas_lote_") as temporario:
        diretorio = Path(temporario)

        for numero_bloco, bloco in enumerate(
            blocos,
            start=1,
        ):
            caminho_bloco = diretorio / f"bloco_{numero_bloco:04d}.csv"
            _escrever_catalogo_temporario(
                caminho_bloco,
                bloco,
            )

            codigo = _executar_crawler(
                diretorio_raw=diretorio_raw,
                javascript=javascript,
                catalogo=caminho_bloco,
                etiqueta=f"BLOCO {numero_bloco}/{len(blocos)}",
                limite_respostas=_calcular_limite_respostas(bloco)
                + (limite_anuncios or 0) * len(bloco),
                **({"limite_anuncios": limite_anuncios} if limite_anuncios is not None else {}),
            )

            if codigo == 0:
                continue

            print()
            print("O bloco terminou com erro. Seus alvos serão repetidos individualmente.")

            for numero_alvo, alvo in enumerate(
                bloco,
                start=1,
            ):
                caminho_alvo = diretorio / (f"bloco_{numero_bloco:04d}_alvo_{numero_alvo:04d}.csv")
                alvo_isolado = (alvo,)

                _escrever_catalogo_temporario(
                    caminho_alvo,
                    alvo_isolado,
                )

                codigo_alvo = _executar_crawler(
                    diretorio_raw=diretorio_raw,
                    javascript=javascript,
                    catalogo=caminho_alvo,
                    etiqueta=f"ALVO ISOLADO {alvo.alvo_id}",
                    limite_respostas=_calcular_limite_respostas(alvo_isolado)
                    + (limite_anuncios or 0),
                    **({"limite_anuncios": limite_anuncios} if limite_anuncios is not None else {}),
                )

                if codigo_alvo != 0:
                    falhas[alvo.alvo_id] = codigo_alvo

    return falhas


def _executar_extracao(
    *,
    alvo: AlvoColeta,
    diretorio_raw: Path,
    coletado_desde: datetime,
    confirmar: bool,
    registros: tuple[RegistroInventarioBruto, ...] | None = None,
    publicado_em: date | None = None,
) -> int:
    """Extrai e opcionalmente salva anúncios de um alvo."""

    print(f"\n# ETAPA: EXTRAÇÃO DO ALVO {alvo.alvo_id}")
    try:
        return executar_extracao(
            alvo_id=alvo.alvo_id,
            diretorio_raw=diretorio_raw,
            coletado_desde=coletado_desde,
            confirmar=confirmar,
            sinalizar_sem_anuncios=True,
            registros=registros,
            publicado_em=publicado_em,
        )
    except Exception as erro:
        # Mantém o isolamento antes oferecido pelo subprocesso. Interrupções
        # do usuário (KeyboardInterrupt/SystemExit) não são engolidas.
        print(f"Falha isolada na extração: {type(erro).__name__}")
        return 1


def _carregar_anuncios_do_lote(
    *,
    alvo_id: str,
    coletado_desde: datetime,
    limite: int,
) -> tuple[UUID, ...]:
    """Busca somente anúncios observados no lote atual."""

    configuracoes = get_settings()

    with ConexaoMongoDB(configuracoes) as conexao:
        repositorio = RepositorioAnunciosMongoDB(conexao.banco)

        anuncios = repositorio.listar_por_alvo(
            alvo_id,
            limite=limite,
        )

    return tuple(
        anuncio.id for anuncio in anuncios if (anuncio.ultima_observacao_em >= coletado_desde)
    )


def _resolver_empresa(
    anuncio_id: UUID,
) -> int:
    """Resolve a empresa de um anúncio atual."""

    return _executar_python(
        etapa=(f"RESOLUÇÃO DA EMPRESA DO ANÚNCIO {anuncio_id}"),
        argumentos=(
            str(DIRETORIO_SCRIPTS / "resolver_empresas_anuncios.py"),
            "--anuncio-id",
            str(anuncio_id),
            "--confirmar",
        ),
    )


def _criar_vaga(
    anuncio_id: UUID,
) -> int:
    """Cria ou atualiza a vaga canônica do anúncio."""

    return _executar_python(
        etapa=(f"VAGA CANÔNICA DO ANÚNCIO {anuncio_id}"),
        argumentos=(
            str(DIRETORIO_SCRIPTS / "criar_vagas_canonicas.py"),
            "--anuncio-id",
            str(anuncio_id),
            "--confirmar",
        ),
    )


def _processar_anuncios_confirmados(
    *,
    alvo: AlvoColeta,
    coletado_desde: datetime,
    limite: int,
    trabalhadores: int,
) -> tuple[int, int]:
    """Resolve empresas e vagas do alvo confirmado."""

    anuncios = _carregar_anuncios_do_lote(
        alvo_id=alvo.alvo_id,
        coletado_desde=coletado_desde,
        limite=limite,
    )

    if not anuncios:
        print()
        print(f"Nenhum anúncio do lote atual foi encontrado para o alvo {alvo.alvo_id}.")
        print("As etapas de empresa e vaga serão ignoradas.")

        return (
            0,
            0,
        )

    sucessos = 0
    falhas = 0
    anuncios_com_empresa: list[UUID] = []

    # A associação da empresa é serial para impedir que duas vagas do mesmo
    # empregador tentem criar o mesmo cadastro ao mesmo tempo. Normalmente é
    # uma etapa curta de banco de dados; o processamento pesado vem a seguir.
    for anuncio_id in anuncios:
        if _resolver_empresa(anuncio_id) == 0:
            anuncios_com_empresa.append(anuncio_id)
        else:
            falhas += 1

    if not anuncios_com_empresa:
        return sucessos, falhas

    print(
        "Processando "
        f"{len(anuncios_com_empresa)} vaga(s) canônica(s) com até {trabalhadores} "
        "trabalhador(es) em paralelo."
    )

    # Cada vaga é identificada pelo anúncio e as gravações usam operações
    # atômicas no MongoDB. A empresa já está associada antes de qualquer tarefa
    # concorrente, eliminando a corrida por um cadastro de empresa compartilhado.
    with ThreadPoolExecutor(max_workers=trabalhadores) as executor:
        futuros = {
            executor.submit(_criar_vaga, anuncio_id): anuncio_id
            for anuncio_id in anuncios_com_empresa
        }
        for futuro in as_completed(futuros):
            try:
                if futuro.result() == 0:
                    sucessos += 1
                else:
                    falhas += 1
            except Exception as erro:
                falhas += 1
                print(f"Falha isolada no anúncio {futuros[futuro]}: {type(erro).__name__}")

    return (
        sucessos,
        falhas,
    )


def _indexar_registros_do_lote(
    registros: Sequence[RegistroInventarioBruto],
    alvos: Sequence[AlvoColeta],
    coletado_desde: datetime,
) -> tuple[dict[str, tuple[RegistroInventarioBruto, ...]], int]:
    """Distribui o inventário uma vez; preserva sua ordem de observação."""
    por_alvo: dict[str, list[RegistroInventarioBruto]] = {alvo.alvo_id: [] for alvo in alvos}
    descartados = 0
    for registro in registros:
        if registro.alvo_id in por_alvo and registro.coletado_em >= coletado_desde:
            por_alvo[registro.alvo_id].append(registro)
        else:
            descartados += 1
    return {alvo: tuple(itens) for alvo, itens in por_alvo.items()}, descartados


def executar(
    argumentos: Sequence[str] | None = None,
) -> int:
    """Executa o lote completo ou apenas sua prévia."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)
    # Fixa a data antes do lote para evitar mudar o filtro ao atravessar meia-noite.
    publicado_em = ontem_brasilia() if opcoes.publicados_ontem else opcoes.publicados_em

    if opcoes.limite < 1 or opcoes.limite > 10000:
        print("ERRO: limite deve estar entre 1 e 10000")
        return 2

    if opcoes.alvos_por_coleta < 1 or opcoes.alvos_por_coleta > 200:
        print("ERRO: alvos-por-coleta deve estar entre 1 e 200")
        return 2

    if not 1 <= opcoes.trabalhadores_posprocessamento <= 32:
        print("ERRO: trabalhadores-posprocessamento deve estar entre 1 e 32")
        return 2

    if opcoes.limite_anuncios is not None and not 1 <= opcoes.limite_anuncios <= 10000:
        print("ERRO: limite-anuncios deve estar entre 1 e 10000")
        return 2

    catalogo = _resolver_caminho(opcoes.catalogo)

    diretorio_raw = _resolver_caminho(opcoes.diretorio_raw)

    try:
        resultado_catalogo = carregar_alvos_csv_tolerante(catalogo)

    except ErroCatalogoFontes as erro:
        print(f"ERRO NO CATÁLOGO: {erro}")
        return 2

    alvos = resultado_catalogo.alvos
    falhas_catalogo = resultado_catalogo.falhas

    executaveis = tuple(
        alvo
        for alvo in alvos
        if alvo.habilitado_para_coleta
        and (not opcoes.somente_republicaveis or alvo.habilitado_para_publicacao)
    )

    ignorados = tuple(alvo for alvo in alvos if alvo not in executaveis)

    _mostrar_plano(
        catalogo=catalogo,
        executaveis=executaveis,
        ignorados=ignorados,
        falhas_catalogo=falhas_catalogo,
        confirmar=opcoes.confirmar,
        coletar=opcoes.coletar,
        alvos_por_coleta=opcoes.alvos_por_coleta,
        somente_republicaveis=opcoes.somente_republicaveis,
    )

    if not executaveis:
        print()
        print("Nenhum alvo está autorizado para coleta.")
        return 0

    falhas_coleta: dict[str, int] = {}

    if opcoes.coletar:
        if opcoes.limite_anuncios is not None:
            print(
                "Limites separados: listagens conforme catálogo; "
                f"detalhes por fonte: {opcoes.limite_anuncios}"
            )
        # O horário é registrado antes da primeira requisição.
        coletado_desde = datetime.now(UTC)

        falhas_coleta = _coletar_em_blocos(
            diretorio_raw=diretorio_raw,
            javascript=opcoes.javascript,
            alvos=executaveis,
            tamanho_bloco=opcoes.alvos_por_coleta,
            **(
                {"limite_anuncios": opcoes.limite_anuncios}
                if opcoes.limite_anuncios is not None
                else {}
            ),
        )

    else:
        coletado_desde = opcoes.coletado_desde

        if coletado_desde is None:
            print("ERRO: informe --coletar ou --coletado-desde.")
            return 2

    print()
    print(
        "Início considerado para o lote:",
        coletado_desde.isoformat(),
    )

    try:
        registros = carregar_inventario_bruto(diretorio_raw)
    except (ErroInventarioBruto, OSError) as erro:
        print(f"ERRO NO INVENTÁRIO: {erro}")
        return 1

    registros_por_alvo, descartados = _indexar_registros_do_lote(
        registros, executaveis, coletado_desde
    )
    print(f"Inventário: {len(registros)} registros; fora dos alvos/período: {descartados}")
    del registros

    alvos_com_sucesso = 0
    alvos_sem_anuncios = 0
    alvos_incompativeis = 0
    alvos_com_falha = len(falhas_coleta)
    anuncios_concluidos = 0
    anuncios_com_falha = 0
    resultados_alvos: list[tuple[str, str, str]] = [
        (
            alvo_id,
            "FALHA",
            f"crawler terminou com código {codigo}",
        )
        for alvo_id, codigo in falhas_coleta.items()
    ]

    for alvo in executaveis:
        if alvo.alvo_id in falhas_coleta:
            continue

        codigo_extracao = _executar_extracao(
            publicado_em=publicado_em,
            alvo=alvo,
            diretorio_raw=diretorio_raw,
            coletado_desde=coletado_desde,
            confirmar=opcoes.confirmar,
            registros=registros_por_alvo.pop(alvo.alvo_id, ()),
        )

        if codigo_extracao == CODIGO_SEM_ANUNCIOS:
            alvos_sem_anuncios += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "IGNORADO",
                    "nenhum anúncio extraível foi encontrado",
                )
            )
            continue

        if codigo_extracao == CODIGO_EXTRACAO_COM_FALHAS:
            alvos_incompativeis += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "IGNORADO",
                    "conteúdo não pôde ser extraído com segurança",
                )
            )
            continue

        if codigo_extracao != 0:
            alvos_com_falha += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "FALHA",
                    f"extração terminou com código {codigo_extracao}",
                )
            )
            continue

        if not opcoes.confirmar:
            alvos_com_sucesso += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "CONCLUÍDO",
                    "prévia da extração concluída",
                )
            )
            continue

        try:
            sucessos, falhas = _processar_anuncios_confirmados(
                alvo=alvo,
                coletado_desde=coletado_desde,
                limite=opcoes.limite,
                trabalhadores=opcoes.trabalhadores_posprocessamento,
            )

        except (
            ErroConexaoMongoDB,
            ErroRepositorioAnunciosMongoDB,
            ValueError,
        ) as erro:
            print()
            print(f"ERRO NO ALVO {alvo.alvo_id}: {erro}")

            alvos_com_falha += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "FALHA",
                    str(erro),
                )
            )
            continue

        anuncios_concluidos += sucessos
        anuncios_com_falha += falhas

        if falhas:
            alvos_com_falha += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "FALHA",
                    f"{falhas} anúncio(s) não concluído(s)",
                )
            )

        else:
            alvos_com_sucesso += 1
            resultados_alvos.append(
                (
                    alvo.alvo_id,
                    "CONCLUÍDO",
                    f"{sucessos} anúncio(s) concluído(s)",
                )
            )

    print()
    print("# RESULTADO FINAL DO LOTE")
    print()
    print(f"Alvos concluídos: {alvos_com_sucesso}")
    print(f"Alvos sem anúncios extraíveis: {alvos_sem_anuncios}")
    print(f"Alvos incompatíveis ignorados: {alvos_incompativeis}")
    print(f"Alvos com falha: {alvos_com_falha}")
    print(f"Alvos ignorados por política/configuração: {len(ignorados)}")
    print(f"Linhas inválidas ignoradas: {len(falhas_catalogo)}")

    if resultados_alvos:
        print()
        print("## RESULTADO POR ALVO")

        for alvo_id, situacao, motivo in resultados_alvos:
            print(
                "-",
                alvo_id,
                "|",
                situacao,
                "|",
                motivo,
            )

    if opcoes.confirmar:
        print(f"Anúncios concluídos: {anuncios_concluidos}")
        print(f"Anúncios com falha: {anuncios_com_falha}")

    else:
        print("Modo de prévia: nenhum anúncio, empresa ou vaga foi alterado no MongoDB.")
        print("Para executar as gravações, revise o resultado e acrescente --confirmar.")

    return 1 if alvos_com_falha or anuncios_com_falha else 0


if __name__ == "__main__":
    raise SystemExit(executar())
