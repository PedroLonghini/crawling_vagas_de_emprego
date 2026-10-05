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
import io
import json
import multiprocessing
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from contextlib import ExitStack, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from itertools import zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

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
    carregar_inventario_bruto_de_cadernos,
    carregar_inventario_bruto_desde,
)
from observatorio_vagas.crawling.urls import normalizar_url_vaga
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio
from observatorio_vagas.extraction.data_publicacao import ontem_brasilia
from observatorio_vagas.extraction.processador import (
    ParcialExtracao,
    combinar_parciais,
    extrair_parcial,
)
from observatorio_vagas.extraction.resolucao_empresa import (
    CacheEmpresas,
    resolver_e_associar_empresas_em_lote,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
    RepositorioVagasMongoDB,
    preparar_banco,
)

# Suporta tanto execução direta no PowerShell quanto importação pelos testes.
if __package__:
    from . import criar_vagas_canonicas
    from .processar_e_salvar_anuncios import concluir_extracao
    from .processar_e_salvar_anuncios import executar as executar_extracao
else:
    import criar_vagas_canonicas
    from processar_e_salvar_anuncios import concluir_extracao
    from processar_e_salvar_anuncios import executar as executar_extracao

RAIZ_PROJETO = Path(__file__).resolve().parents[1]

DIRETORIO_SCRIPTS = RAIZ_PROJETO / "scripts"

# Códigos documentados por processar_e_salvar_anuncios.py.
CODIGO_EXTRACAO_COM_FALHAS = 2
CODIGO_SEM_ANUNCIOS = 10

# Alvos com mais de duas vezes este número de páginas são divididos em
# pedaços extraídos em paralelo (~95 ms por página: ~10 s por pedaço).
TAMANHO_PEDACO_EXTRACAO = 100

# Abaixo disso, iniciar processos custa mais do que eles economizam.
MINIMO_PAGINAS_PARALELISMO = 300


def _trabalhadores_padrao() -> int:
    """Usa CPU disponível na normalização sem ultrapassar um teto previsível."""

    nucleos = os.cpu_count() or 2
    return max(4, min(24, nucleos * 2))


def _dominio_ignorado(alvo: AlvoColeta) -> bool:
    """Fontes do LinkedIn nunca entram no lote."""

    return "linkedin" in alvo.dominio.casefold()


def _exportar_urls_conhecidas(destino: Path) -> Path | None:
    """Grava as URLs das vagas já salvas no MongoDB para o crawler descartá-las."""

    try:
        banco = ConexaoMongoDB(get_settings()).banco
        urls = {
            normalizar_url_vaga(documento["url"])
            for documento in banco["anuncios"].find({}, {"url": 1})
            if documento.get("url")
        }
    except Exception as erro:
        print(f"AVISO: não foi possível consultar o MongoDB ({type(erro).__name__}); ")
        print("as vagas já gravadas não serão descartadas durante a coleta.")
        return None

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(sorted(urls)), encoding="utf-8")
    print(f"Vagas já gravadas no MongoDB (descartadas se reaparecerem): {len(urls)}")
    return destino


def _processos_coleta_padrao() -> int:
    """Escolhe processos de coleta para aproveitar I/O sem exagerar no notebook."""

    nucleos = os.cpu_count() or 2
    return max(1, min(3, nucleos // 4))


def _processos_extracao_padrao() -> int:
    """Usa os núcleos livres na extração, que é CPU pura e independente por alvo."""

    nucleos = os.cpu_count() or 2
    return max(1, min(64, nucleos - 1))


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
        help="sites por processo do crawler (padrão: 200; entre 1 e 2000)",
    )

    parser.add_argument(
        "--sem-descarte-mongo",
        action="store_true",
        help="não consulta o MongoDB para descartar vagas já gravadas durante a coleta",
    )

    parser.add_argument(
        "--limite-navegacao",
        type=int,
        default=None,
        help=(
            "máximo de páginas de listagem e sitemap por fonte (padrão: 100 quando há "
            "--limite-anuncios). Sem isso cada fonte podia ler até 10.000 páginas."
        ),
    )

    parser.add_argument(
        "--janela-horas",
        type=int,
        default=None,
        help=(
            "coleta só vagas publicadas nas últimas N horas: a fonte é encerrada quando "
            "3 vagas seguidas passam da janela (exige data de publicação na página; "
            "padrão: sem janela)"
        ),
    )

    parser.add_argument(
        "--tempo-maximo-bloco",
        type=int,
        default=None,
        help=(
            "minutos máximos de cada bloco de coleta; ao estourar, o bloco encerra e "
            "o restante continua na próxima rodada (padrão: sem limite)"
        ),
    )

    parser.add_argument(
        "--processos-coleta",
        type=int,
        default=_processos_coleta_padrao(),
        help=(
            "processos do crawler em paralelo, separados por domínio "
            f"(padrão: {_processos_coleta_padrao()}; entre 1 e 12)"
        ),
    )

    parser.add_argument(
        "--processos-extracao",
        type=int,
        default=_processos_extracao_padrao(),
        help=(
            "alvos extraídos em paralelo, um processo por alvo "
            f"(padrão: {_processos_extracao_padrao()}; entre 1 e 64; 1 desativa o paralelismo)"
        ),
    )

    parser.add_argument(
        "--cache-extracao",
        action="store_true",
        help=(
            "reaproveita a análise de páginas idênticas já extraídas; útil ao "
            "reprocessar o mesmo período (o cache fica ao lado do diretório raw, em cache/)"
        ),
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
    datas.add_argument("--publicados-hoje", action="store_true", help="publicados hoje em Brasília")
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

    if _dominio_ignorado(alvo):
        return "domínio ignorado por decisão do usuário (LinkedIn)"

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
    processos_coleta: int,
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
        print(f"Processos de coleta em paralelo: {processos_coleta}")

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
    diretorio_cadernos: Path | None = None,
    estado_incremental: Path | None = None,
    tempo_maximo: int | None = None,
    janela_horas: int | None = None,
    limite_navegacao: int | None = None,
    urls_conhecidas: Path | None = None,
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
            *(
                # Nome único: filas paralelas nunca escrevem no mesmo caderno.
                ("-s", f"RAW_INDEX_FILE={diretorio_cadernos / f'{instante}_{uuid4().hex}.jsonl'}")
                if diretorio_cadernos is not None
                else ()
            ),
            *(("-s", "JAVASCRIPT_ENABLED=True") if javascript else ()),
            # Encerra o bloco com calma ao estourar o tempo: o que faltou é
            # retomado pelo estado incremental na próxima rodada.
            *(("-s", f"CLOSESPIDER_TIMEOUT={tempo_maximo}") if tempo_maximo else ()),
            *(("-a", f"janela_horas={janela_horas}") if janela_horas else ()),
            *(("-a", f"limite_navegacao={limite_navegacao}") if limite_navegacao else ()),
            *(("-a", f"urls_conhecidas={urls_conhecidas}") if urls_conhecidas else ()),
            *(("-a", f"limite_anuncios={limite_anuncios}") if limite_anuncios is not None else ()),
            # Sem isto o estado ficava ao lado do catálogo temporário do bloco e
            # era apagado no fim: todo dia virava uma coleta completa.
            *(
                ("-a", f"estado_incremental={estado_incremental}")
                if estado_incremental is not None
                else ()
            ),
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


def _arquivo_estado(diretorio: Path | None, fila: int) -> Path | None:
    """Estado incremental persistente da fila ``fila`` (None desliga)."""

    return None if diretorio is None else diretorio / f"fila_{fila}.json"


def _coletar_em_blocos(
    *,
    alvos: tuple[AlvoColeta, ...],
    tamanho_bloco: int,
    processos: int,
    limite_anuncios: int | None = None,
    javascript: bool = False,
    diretorio_raw: Path | None = None,
    diretorio_cadernos: Path | None = None,
    diretorio_estado: Path | None = None,
    tempo_maximo: int | None = None,
    janela_horas: int | None = None,
    limite_navegacao: int | None = None,
    urls_conhecidas: Path | None = None,
) -> dict[str, int]:
    """Coleta blocos em paralelo, isolando cada domínio em uma única fila."""

    filas = _distribuir_alvos_por_dominio(
        alvos,
        processos,
        None if diretorio_estado is None else diretorio_estado / "distribuicao.json",
    )
    ativas = [(numero, fila) for numero, fila in enumerate(filas, start=1) if fila]

    if len(ativas) == 1:
        return _coletar_fila_em_blocos(
            alvos=ativas[0][1],
            tamanho_bloco=tamanho_bloco,
            limite_anuncios=limite_anuncios,
            javascript=javascript,
            diretorio_raw=diretorio_raw,
            diretorio_cadernos=diretorio_cadernos,
            estado_incremental=_arquivo_estado(diretorio_estado, ativas[0][0]),
            tempo_maximo=tempo_maximo,
            janela_horas=janela_horas,
            limite_navegacao=limite_navegacao,
            urls_conhecidas=urls_conhecidas,
        )

    falhas: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(ativas))) as executor:
        futuros = {
            executor.submit(
                _coletar_fila_em_blocos,
                alvos=fila,
                tamanho_bloco=tamanho_bloco,
                limite_anuncios=limite_anuncios,
                javascript=javascript,
                diretorio_raw=diretorio_raw,
                diretorio_cadernos=diretorio_cadernos,
                estado_incremental=_arquivo_estado(diretorio_estado, numero),
                tempo_maximo=tempo_maximo,
                janela_horas=janela_horas,
                limite_navegacao=limite_navegacao,
                urls_conhecidas=urls_conhecidas,
            ): numero
            for numero, fila in ativas
        }

        for futuro in as_completed(futuros):
            try:
                falhas.update(futuro.result())
            except Exception as erro:
                numero = futuros[futuro]
                print(f"Falha inesperada na fila de coleta {numero}: {type(erro).__name__}")

    return falhas


def _distribuir_alvos_por_dominio(
    alvos: tuple[AlvoColeta, ...],
    processos: int,
    memoria: Path | None = None,
) -> tuple[tuple[AlvoColeta, ...], ...]:
    """Distribui domínios inteiros entre processos para manter o limite por site.

    Os domínios com mais fontes vão primeiro, cada um para a fila menos
    carregada: os sites grandes ficam em filas separadas e rodam ao mesmo
    tempo, em vez de esperar uns pelos outros. A escolha é guardada em
    ``memoria`` para o domínio voltar sempre à mesma fila, pois o estado
    incremental é gravado por fila. Filas vazias são mantidas (a posição é o
    número da fila).
    """

    if processos < 1:
        raise ValueError("processos precisa ser pelo menos 1")

    peso: dict[str, int] = {}
    for alvo in alvos:
        dominio = alvo.dominio.casefold()
        peso[dominio] = peso.get(dominio, 0) + 1

    destino_por_dominio: dict[str, int] = {}
    if memoria is not None and memoria.exists():
        try:
            guardado = json.loads(memoria.read_text(encoding="utf-8"))
            if guardado.get("processos") == processos:
                destino_por_dominio = {
                    dominio: int(fila)
                    for dominio, fila in guardado.get("dominios", {}).items()
                    if dominio in peso and 0 <= int(fila) < processos
                }
        except (OSError, ValueError, AttributeError):
            destino_por_dominio = {}

    carga = [0] * processos
    for dominio, fila in destino_por_dominio.items():
        carga[fila] += peso[dominio]

    novos = sorted((d for d in peso if d not in destino_por_dominio), key=lambda d: (-peso[d], d))
    for dominio in novos:
        fila = min(range(processos), key=lambda n: (carga[n], n))
        destino_por_dominio[dominio] = fila
        carga[fila] += peso[dominio]

    if memoria is not None and novos:
        try:
            memoria.parent.mkdir(parents=True, exist_ok=True)
            memoria.write_text(
                json.dumps(
                    {"processos": processos, "dominios": destino_por_dominio},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    filas: list[list[AlvoColeta]] = [[] for _ in range(processos)]
    for alvo in alvos:
        filas[destino_por_dominio[alvo.dominio.casefold()]].append(alvo)

    return tuple(tuple(fila) for fila in filas)


def _coletar_fila_em_blocos(
    *,
    alvos: tuple[AlvoColeta, ...],
    tamanho_bloco: int,
    limite_anuncios: int | None = None,
    javascript: bool = False,
    diretorio_raw: Path | None = None,
    diretorio_cadernos: Path | None = None,
    estado_incremental: Path | None = None,
    tempo_maximo: int | None = None,
    janela_horas: int | None = None,
    limite_navegacao: int | None = None,
    urls_conhecidas: Path | None = None,
) -> dict[str, int]:
    """Executa uma fila de domínios de forma sequencial e recuperável."""

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
                diretorio_cadernos=diretorio_cadernos,
                estado_incremental=estado_incremental,
                tempo_maximo=tempo_maximo,
                janela_horas=janela_horas,
                limite_navegacao=limite_navegacao,
                urls_conhecidas=urls_conhecidas,
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
                    diretorio_cadernos=diretorio_cadernos,
                    estado_incremental=estado_incremental,
                    tempo_maximo=tempo_maximo,
                    janela_horas=janela_horas,
                    limite_navegacao=limite_navegacao,
                    urls_conhecidas=urls_conhecidas,
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
    cache_paginas: Path | None = None,
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
            # O lote prepara o banco uma vez, antes de extrair.
            preparar=False,
            cache_paginas=cache_paginas,
        )
    except Exception as erro:
        # Mantém o isolamento antes oferecido pelo subprocesso. Interrupções
        # do usuário (KeyboardInterrupt/SystemExit) não são engolidas.
        print(f"Falha isolada na extração: {type(erro).__name__}")
        return 1


def _extrair_alvo_em_processo(
    parametros: dict[str, object],
) -> tuple[int, str]:
    """Extrai um alvo num processo filho e devolve o código e o log capturado.

    Capturar a saída evita que relatórios de alvos diferentes se misturem.
    """

    saida = io.StringIO()
    inicio = time.perf_counter()

    with redirect_stdout(saida):
        codigo = _executar_extracao(**parametros)
        print(f"Tempo de extração do alvo: {time.perf_counter() - inicio:.1f}s")

    return codigo, saida.getvalue()


def _extrair_alvos(
    alvos: Sequence[AlvoColeta],
    *,
    processos: int,
    registros_por_alvo: dict[str, tuple[RegistroInventarioBruto, ...]],
    diretorio_raw: Path,
    coletado_desde: datetime,
    confirmar: bool,
    publicado_em: date | None,
    cache_paginas: Path | None = None,
) -> Iterator[tuple[AlvoColeta, int]]:
    """Entrega cada alvo com seu código de extração assim que ele termina.

    Com mais de um processo, a ordem é a de conclusão. Quem consome o gerador
    pode gravar no MongoDB enquanto os demais alvos continuam sendo extraídos.
    """

    def parametros(alvo: AlvoColeta) -> dict[str, object]:
        return {
            "publicado_em": publicado_em,
            "alvo": alvo,
            "diretorio_raw": diretorio_raw,
            "coletado_desde": coletado_desde,
            "confirmar": confirmar,
            "registros": registros_por_alvo.pop(alvo.alvo_id, ()),
            "cache_paginas": cache_paginas,
        }

    total_paginas = sum(len(registros_por_alvo.get(alvo.alvo_id, ())) for alvo in alvos)

    # Cada processo leva ~1 a 2 s para iniciar. Com poucas páginas, extrair
    # aqui mesmo é mais rápido (lote de 5 alvos: 9 s em série, 15 s com 11).
    if total_paginas < MINIMO_PAGINAS_PARALELISMO and processos > 1:
        print()
        print(f"Poucas páginas ({total_paginas}): extração neste processo, sem paralelismo.")
        processos = 1

    if processos <= 1 or not alvos:
        for alvo in alvos:
            inicio = time.perf_counter()
            codigo = _executar_extracao(**parametros(alvo))
            print(f"Tempo de extração do alvo: {time.perf_counter() - inicio:.1f}s")
            yield alvo, codigo
        return

    # Os menores primeiro: terminam logo e já são gravados no MongoDB, em vez
    # de esperar horas atrás das centenas de pedaços dos alvos gigantes (no
    # lote de 1.000 fontes, um só site tinha 66% das páginas e nada era
    # gravado até ele acabar). Como os alvos grandes são cortados em pedaços de
    # 100 páginas, deixá-los por último quase não cria sobra no fim.
    ordenados = sorted(
        alvos,
        key=lambda alvo: len(registros_por_alvo.get(alvo.alvo_id, ())),
    )

    print()
    print(f"Extraindo {len(alvos)} alvo(s) com até {processos} processo(s) em paralelo.")
    sys.stdout.flush()

    # "spawn" se comporta igual no Windows, no Linux e no macOS e não herda
    # conexões MongoDB nem threads abertas do processo principal.
    contexto = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(max_workers=processos, mp_context=contexto) as executor:
        futuros_alvo: dict[Future[tuple[int, str]], AlvoColeta] = {}
        futuros_pedaco: dict[Future[ParcialExtracao], tuple[AlvoColeta, int]] = {}
        pedacos: dict[str, list[ParcialExtracao | None]] = {}
        inicio_alvo: dict[str, float] = {}
        # Como refazer cada tarefa aqui mesmo, se o pool quebrar.
        refazer: dict[Future[Any], Callable[[], Any]] = {}

        pedacos_por_alvo: list[
            list[tuple[AlvoColeta, int, tuple[RegistroInventarioBruto, ...]]]
        ] = []

        for alvo in ordenados:
            registros = registros_por_alvo.get(alvo.alvo_id, ())

            if len(registros) <= 2 * TAMANHO_PEDACO_EXTRACAO:
                argumentos = parametros(alvo)
                futuro_alvo = executor.submit(_extrair_alvo_em_processo, argumentos)
                futuros_alvo[futuro_alvo] = alvo
                refazer[futuro_alvo] = partial(_extrair_alvo_em_processo, argumentos)
                continue

            # Alvo grande: cada pedaço contíguo vai para um núcleo livre.
            registros_por_alvo.pop(alvo.alvo_id, None)
            partes = [
                registros[inicio : inicio + TAMANHO_PEDACO_EXTRACAO]
                for inicio in range(0, len(registros), TAMANHO_PEDACO_EXTRACAO)
            ]
            pedacos[alvo.alvo_id] = [None] * len(partes)
            inicio_alvo[alvo.alvo_id] = time.perf_counter()

            pedacos_por_alvo.append(
                [(alvo, posicao, parte) for posicao, parte in enumerate(partes)]
            )

        # Os pedaços dos alvos grandes entram intercalados (um de cada alvo por
        # vez): todos avançam juntos e terminam por volta do mesmo momento, em
        # vez de um alvo gigante terminar e só então o seguinte começar.
        for alvo, posicao, parte in (
            item for rodada in zip_longest(*pedacos_por_alvo) for item in rodada if item is not None
        ):
            futuro = executor.submit(
                extrair_parcial,
                diretorio_raw,
                parte,
                alvo_id=alvo.alvo_id,
                coletado_desde=coletado_desde,
                cache_paginas=cache_paginas,
            )
            futuros_pedaco[futuro] = (alvo, posicao)
            refazer[futuro] = partial(
                extrair_parcial,
                diretorio_raw,
                parte,
                alvo_id=alvo.alvo_id,
                coletado_desde=coletado_desde,
                cache_paginas=cache_paginas,
            )

        try:
            yield from _resultados_extracao(
                futuros_alvo=futuros_alvo,
                futuros_pedaco=futuros_pedaco,
                pedacos=pedacos,
                inicio_alvo=inicio_alvo,
                coletado_desde=coletado_desde,
                confirmar=confirmar,
                publicado_em=publicado_em,
                refazer=refazer,
            )
        except BaseException:
            # Sem isso, uma falha aqui (ou Ctrl+C) deixava o pool extraindo
            # todos os alvos restantes sem ninguém ler os resultados.
            executor.shutdown(wait=False, cancel_futures=True)
            raise


def _resultados_extracao(
    *,
    futuros_alvo: dict[Future[tuple[int, str]], AlvoColeta],
    futuros_pedaco: dict[Future[ParcialExtracao], tuple[AlvoColeta, int]],
    pedacos: dict[str, list[ParcialExtracao | None]],
    inicio_alvo: dict[str, float],
    coletado_desde: datetime,
    confirmar: bool,
    publicado_em: date | None,
    refazer: dict[Future[Any], Callable[[], Any]] | None = None,
) -> Iterator[tuple[AlvoColeta, int]]:
    """Mostra o log de cada alvo concluído e entrega seu código.

    Se um processo filho morrer (ex.: falta de memória), o pool inteiro
    quebra; as tarefas que sobraram são refeitas aqui, em série.

    Alvos inteiros chegam prontos do processo filho. Alvos divididos são
    concluídos aqui quando o último pedaço chega: os pedaços são combinados
    na ordem do inventário e gravados uma única vez.
    """

    falhos: set[str] = set()
    refazer = refazer or {}

    def resultado(futuro: Future[Any]) -> Any:
        try:
            return futuro.result()
        except BrokenProcessPool:
            if futuro not in refazer:
                raise
            return refazer[futuro]()

    for futuro in as_completed([*futuros_alvo, *futuros_pedaco]):
        if futuro in futuros_alvo:
            alvo = futuros_alvo[futuro]

            try:
                codigo, saida = resultado(futuro)
            except Exception as erro:
                # Falha do próprio processo filho, por exemplo falta de memória.
                print(f"\n# ETAPA: EXTRAÇÃO DO ALVO {alvo.alvo_id}")
                print(f"Falha isolada no processo de extração: {type(erro).__name__}")
                yield alvo, 1
                continue

            print(saida, end="")
            sys.stdout.flush()
            yield alvo, codigo
            continue

        alvo, posicao = futuros_pedaco[futuro]

        if alvo.alvo_id in falhos:
            continue

        try:
            pedacos[alvo.alvo_id][posicao] = resultado(futuro)
        except Exception as erro:
            falhos.add(alvo.alvo_id)
            print(f"\n# ETAPA: EXTRAÇÃO DO ALVO {alvo.alvo_id}")
            print("Não foi possível processar as respostas brutas:")
            print(f"{type(erro).__name__}: {erro}")
            yield alvo, 1
            continue

        partes = pedacos[alvo.alvo_id]

        if any(parte is None for parte in partes):
            continue

        print(f"\n# ETAPA: EXTRAÇÃO DO ALVO {alvo.alvo_id}")
        print()
        print("Processando respostas coletadas desde:", coletado_desde.isoformat())
        print(f"Extraído em {len(partes)} pedaço(s) em paralelo.")

        try:
            codigo = concluir_extracao(
                combinar_parciais(
                    [parte for parte in partes if parte is not None],
                    publicado_em=publicado_em,
                ),
                confirmar=confirmar,
                sinalizar_sem_anuncios=True,
                preparar=False,
            )
        except Exception as erro:
            print(f"Falha isolada na extração: {type(erro).__name__}")
            codigo = 1

        print(f"Tempo de extração do alvo: {time.perf_counter() - inicio_alvo[alvo.alvo_id]:.1f}s")
        sys.stdout.flush()
        yield alvo, codigo


def _carregar_anuncios_do_lote(
    repositorio: RepositorioAnunciosMongoDB,
    *,
    alvo_id: str,
    coletado_desde: datetime,
    limite: int,
) -> tuple[AnuncioVaga, ...]:
    """Busca somente anúncios observados no lote atual."""

    anuncios = repositorio.listar_por_alvo(
        alvo_id,
        limite=limite,
    )

    return tuple(
        anuncio for anuncio in anuncios if (anuncio.ultima_observacao_em >= coletado_desde)
    )


@dataclass(frozen=True, slots=True)
class _RepositoriosPosProcessamento:
    """Repositórios de uma única conexão MongoDB, aberta uma vez por lote."""

    anuncios: RepositorioAnunciosMongoDB
    empresas: RepositorioEmpresasMongoDB
    vagas: RepositorioVagasMongoDB
    cache_empresas: CacheEmpresas


def _processar_anuncios_confirmados(
    *,
    alvo: AlvoColeta,
    coletado_desde: datetime,
    limite: int,
    repositorios: _RepositoriosPosProcessamento,
) -> tuple[int, int]:
    """Resolve empresas e vagas do alvo confirmado, em lote.

    Cada empresa é resolvida uma vez por lote (e não uma vez por anúncio), as
    associações são gravadas numa única operação e as vagas são buscadas e
    gravadas também em lote. As regras de resolução e atualização são as
    mesmas dos scripts individuais.
    """

    anuncios = _carregar_anuncios_do_lote(
        repositorios.anuncios,
        alvo_id=alvo.alvo_id,
        coletado_desde=coletado_desde,
        limite=limite,
    )

    if not anuncios:
        print()
        print(f"Nenhum anúncio do lote atual foi encontrado para o alvo {alvo.alvo_id}.")
        print("As etapas de empresa e vaga serão ignoradas.")
        return 0, 0

    print()
    print(f"# ETAPA: EMPRESAS E VAGAS DO ALVO {alvo.alvo_id}")

    inicio = time.perf_counter()
    empresas = resolver_e_associar_empresas_em_lote(
        anuncios,
        repositorio_empresas=repositorios.empresas,
        repositorio_anuncios=repositorios.anuncios,
        cache=repositorios.cache_empresas,
    )
    print(
        f"Empresas: {empresas.empresas_criadas} criada(s), "
        f"{empresas.empresas_atualizadas} atualizada(s); "
        f"{empresas.anuncios_atualizados} anúncio(s) associado(s) "
        f"em {time.perf_counter() - inicio:.1f}s"
    )

    for falha in empresas.falhas:
        print(f"Falha isolada no anúncio {falha.anuncio_id}: {falha.mensagem}")

    inicio = time.perf_counter()
    criadas, reutilizadas, falhas_vagas = criar_vagas_canonicas.processar_anuncios_em_lote(
        empresas.anuncios,
        repositorio_vagas=repositorios.vagas,
    )
    print(
        f"Vagas: {criadas} nova(s), {reutilizadas} atualizada(s) "
        f"em {time.perf_counter() - inicio:.1f}s"
    )

    for anuncio_id, mensagem in falhas_vagas:
        print(f"Falha isolada no anúncio {anuncio_id}: {mensagem}")

    falhas = len(empresas.falhas) + len(falhas_vagas)
    sucessos = len(empresas.anuncios) - len(falhas_vagas)

    return sucessos, falhas


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

    inicio_lote = time.perf_counter()
    tempos: dict[str, float] = {}
    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)
    # Fixa a data antes do lote para evitar mudar o filtro ao atravessar meia-noite.
    if opcoes.publicados_hoje:
        publicado_em = ontem_brasilia() + timedelta(days=1)
    elif opcoes.publicados_ontem:
        publicado_em = ontem_brasilia()
    else:
        publicado_em = opcoes.publicados_em

    if opcoes.limite < 1 or opcoes.limite > 10000:
        print("ERRO: limite deve estar entre 1 e 10000")
        return 2

    if opcoes.alvos_por_coleta < 1 or opcoes.alvos_por_coleta > 2000:
        print("ERRO: alvos-por-coleta deve estar entre 1 e 2000")
        return 2

    if opcoes.limite_navegacao is not None and opcoes.limite_navegacao < 1:
        print("ERRO: limite-navegacao deve ser pelo menos 1")
        return 2

    if opcoes.janela_horas is not None and opcoes.janela_horas < 1:
        print("ERRO: janela-horas deve ser pelo menos 1")
        return 2

    if opcoes.tempo_maximo_bloco is not None and opcoes.tempo_maximo_bloco < 1:
        print("ERRO: tempo-maximo-bloco deve ser de pelo menos 1 minuto")
        return 2

    if not 1 <= opcoes.processos_coleta <= 12:
        print("ERRO: processos-coleta deve estar entre 1 e 12")
        return 2

    if not 1 <= opcoes.processos_extracao <= 64:
        print("ERRO: processos-extracao deve estar entre 1 e 64")
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
        and not _dominio_ignorado(alvo)
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
        processos_coleta=opcoes.processos_coleta,
        somente_republicaveis=opcoes.somente_republicaveis,
    )

    if not executaveis:
        print()
        print("Nenhum alvo está autorizado para coleta.")
        return 0

    falhas_coleta: dict[str, int] = {}
    diretorio_cadernos: Path | None = None

    if opcoes.coletar:
        if opcoes.limite_anuncios is not None:
            print(
                "Limites separados: listagens conforme catálogo; "
                f"detalhes por fonte: {opcoes.limite_anuncios}"
            )
        # O horário é registrado antes da primeira requisição.
        coletado_desde = datetime.now(UTC)

        # Cadernos deste lote: um JSONL por processo do crawler.
        diretorio_cadernos = (
            diretorio_raw / "cadernos" / f"lote_{coletado_desde.strftime('%Y%m%dT%H%M%S%fZ')}"
        )

        inicio_fase = time.perf_counter()
        urls_conhecidas = (
            None
            if opcoes.sem_descarte_mongo
            else _exportar_urls_conhecidas(diretorio_cadernos.parent / "urls_conhecidas.txt")
        )
        falhas_coleta = _coletar_em_blocos(
            diretorio_raw=diretorio_raw,
            diretorio_cadernos=diretorio_cadernos,
            # Um arquivo de estado por fila: as filas rodam em paralelo e cada
            # uma regrava o seu inteiro; compartilhar um só perderia dados.
            diretorio_estado=catalogo.parent / ".cache" / "lote",
            javascript=opcoes.javascript,
            alvos=executaveis,
            tamanho_bloco=opcoes.alvos_por_coleta,
            processos=opcoes.processos_coleta,
            tempo_maximo=(opcoes.tempo_maximo_bloco * 60 if opcoes.tempo_maximo_bloco else None),
            janela_horas=opcoes.janela_horas,
            limite_navegacao=opcoes.limite_navegacao,
            urls_conhecidas=urls_conhecidas,
            **(
                {"limite_anuncios": opcoes.limite_anuncios}
                if opcoes.limite_anuncios is not None
                else {}
            ),
        )
        tempos["coleta"] = time.perf_counter() - inicio_fase

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

    inicio_fase = time.perf_counter()

    try:
        if diretorio_cadernos is not None:
            # Coleta feita agora: lê os cadernos do lote, poucos arquivos
            # em vez de um JSON por resposta.
            registros = carregar_inventario_bruto_de_cadernos(
                sorted(diretorio_cadernos.glob("*.jsonl"))
            )
        else:
            # Reprocessamento: lê somente as pastas diárias alcançadas pelo
            # lote. Percorrer todo o histórico custava minutos.
            registros = carregar_inventario_bruto_desde(diretorio_raw, desde=coletado_desde)
    except (ErroInventarioBruto, OSError) as erro:
        print(f"ERRO NO INVENTÁRIO: {erro}")
        return 1

    registros_por_alvo, descartados = _indexar_registros_do_lote(
        registros, executaveis, coletado_desde
    )
    print(f"Inventário: {len(registros)} registros; fora dos alvos/período: {descartados}")
    del registros
    tempos["inventario"] = time.perf_counter() - inicio_fase

    alvos_com_falha = len(falhas_coleta)
    resultados_alvos: list[tuple[str, str, str]] = [
        (
            alvo_id,
            "FALHA",
            f"crawler terminou com código {codigo}",
        )
        for alvo_id, codigo in falhas_coleta.items()
    ]

    repositorios: _RepositoriosPosProcessamento | None = None
    recursos = ExitStack()

    if opcoes.confirmar:
        try:
            conexao = recursos.enter_context(ConexaoMongoDB(get_settings()))
            # Índices e coleções são preparados uma vez por lote, e não
            # uma vez por alvo como antes.
            preparar_banco(conexao.banco)
        except ErroConexaoMongoDB as erro:
            print(f"ERRO NO MONGODB: {erro}")
            recursos.close()
            return 1

        repositorios = _RepositoriosPosProcessamento(
            anuncios=RepositorioAnunciosMongoDB(conexao.banco),
            empresas=RepositorioEmpresasMongoDB(conexao.banco),
            vagas=RepositorioVagasMongoDB(conexao.banco),
            cache_empresas=CacheEmpresas.vazio(),
        )

    inicio_fase = time.perf_counter()

    with recursos:
        _resultado = _processar_alvos(
            opcoes=opcoes,
            executaveis=executaveis,
            falhas_coleta=falhas_coleta,
            registros_por_alvo=registros_por_alvo,
            diretorio_raw=diretorio_raw,
            coletado_desde=coletado_desde,
            publicado_em=publicado_em,
            repositorios=repositorios,
            resultados_alvos=resultados_alvos,
            tempos=tempos,
        )

    # A extração e a gravação acontecem intercaladas; a extração é o resto.
    tempos["extracao"] = time.perf_counter() - inicio_fase - tempos.get("empresas_e_vagas", 0.0)

    (
        alvos_com_sucesso,
        alvos_sem_anuncios,
        alvos_incompativeis,
        alvos_com_falha_processamento,
        anuncios_concluidos,
        anuncios_com_falha,
    ) = _resultado
    alvos_com_falha += alvos_com_falha_processamento

    print()
    print("# RESULTADO FINAL DO LOTE")
    print()
    return _mostrar_resultado_final(
        opcoes=opcoes,
        alvos_com_sucesso=alvos_com_sucesso,
        alvos_sem_anuncios=alvos_sem_anuncios,
        alvos_incompativeis=alvos_incompativeis,
        alvos_com_falha=alvos_com_falha,
        anuncios_concluidos=anuncios_concluidos,
        anuncios_com_falha=anuncios_com_falha,
        ignorados=ignorados,
        falhas_catalogo=falhas_catalogo,
        resultados_alvos=resultados_alvos,
        tempos=tempos,
        inicio_lote=inicio_lote,
    )


def _processar_alvos(
    *,
    opcoes: argparse.Namespace,
    executaveis: tuple[AlvoColeta, ...],
    falhas_coleta: dict[str, int],
    registros_por_alvo: dict[str, tuple[RegistroInventarioBruto, ...]],
    diretorio_raw: Path,
    coletado_desde: datetime,
    publicado_em: date | None,
    repositorios: _RepositoriosPosProcessamento | None,
    resultados_alvos: list[tuple[str, str, str]],
    tempos: dict[str, float],
) -> tuple[int, int, int, int, int, int]:
    """Extrai cada alvo e, com --confirmar, resolve empresas e vagas."""

    alvos_com_sucesso = 0
    alvos_sem_anuncios = 0
    alvos_incompativeis = 0
    alvos_com_falha = 0
    anuncios_concluidos = 0
    anuncios_com_falha = 0

    alvos_a_extrair = tuple(alvo for alvo in executaveis if alvo.alvo_id not in falhas_coleta)

    alvos_processados = 0

    for alvo, codigo_extracao in _extrair_alvos(
        alvos_a_extrair,
        processos=opcoes.processos_extracao,
        registros_por_alvo=registros_por_alvo,
        diretorio_raw=diretorio_raw,
        coletado_desde=coletado_desde,
        confirmar=opcoes.confirmar,
        publicado_em=publicado_em,
        # Desligado por padrão: entre dias, só ~8% das páginas voltaram
        # idênticas (medido em 29-30/09/2026), pouco para o espaço em disco.
        cache_paginas=(
            diretorio_raw.parent / "cache" / "extracao_paginas.sqlite"
            if opcoes.cache_extracao
            else None
        ),
    ):
        alvos_processados += 1
        print()
        print(f"Progresso: alvo {alvos_processados}/{len(alvos_a_extrair)} ({alvo.alvo_id})")
        sys.stdout.flush()

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

        inicio_pos = time.perf_counter()

        try:
            sucessos, falhas = _processar_anuncios_confirmados(
                alvo=alvo,
                coletado_desde=coletado_desde,
                limite=opcoes.limite,
                repositorios=repositorios,
            )

        except Exception as erro:
            # Erros de banco ou de dados afetam só este alvo.
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

        finally:
            tempos["empresas_e_vagas"] = tempos.get("empresas_e_vagas", 0.0) + (
                time.perf_counter() - inicio_pos
            )

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

    return (
        alvos_com_sucesso,
        alvos_sem_anuncios,
        alvos_incompativeis,
        alvos_com_falha,
        anuncios_concluidos,
        anuncios_com_falha,
    )


def _mostrar_tempos(tempos: dict[str, float], *, total: float) -> None:
    """Mostra quanto cada fase levou, para achar o gargalo de cada execução."""

    print("## TEMPO POR FASE")
    for fase, rotulo in (
        ("coleta", "Coleta pela internet"),
        ("inventario", "Leitura do inventário"),
        ("extracao", "Extração"),
        ("empresas_e_vagas", "Empresas e vagas (MongoDB)"),
    ):
        if fase in tempos:
            segundos = tempos[fase]
            print(f"- {rotulo}: {segundos:.1f}s ({segundos / max(total, 1e-9):.0%})")
    print(f"- Total: {total:.1f}s")
    print()


def _mostrar_resultado_final(
    *,
    opcoes: argparse.Namespace,
    alvos_com_sucesso: int,
    alvos_sem_anuncios: int,
    alvos_incompativeis: int,
    alvos_com_falha: int,
    anuncios_concluidos: int,
    anuncios_com_falha: int,
    ignorados: tuple[AlvoColeta, ...],
    falhas_catalogo: tuple[FalhaLinhaCatalogo, ...],
    resultados_alvos: list[tuple[str, str, str]],
    tempos: dict[str, float],
    inicio_lote: float,
) -> int:
    """Mostra o resumo do lote e devolve o código de saída."""

    _mostrar_tempos(tempos, total=time.perf_counter() - inicio_lote)

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
    # No Windows, a saída redirecionada para arquivo usa cp1252. Um único
    # caractere fora dele (ex.: "ı" turco num título) derrubava o lote ou
    # descartava o alvo inteiro. Caracteres impossíveis viram escapes.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(executar())
