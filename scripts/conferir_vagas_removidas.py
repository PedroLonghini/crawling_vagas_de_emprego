"""Detecta vagas que saíram do site de origem e as marca como encerradas.

Como funciona (regras em ``domain/vagas_removidas.py``):

1. Lê o relatório de cobertura da coleta do dia e o registro local de links
   vistos nas listagens (``.cache/lote/fila_*.json``).
2. Para cada fonte cuja coleta foi COMPLETA (sem janela de horas, sem limite
   atingido, sem erro de acesso), compara pelo link as vagas no MongoDB com os
   links vistos naquele dia. Só entram vagas cujo link já apareceu alguma vez
   numa listagem da fonte. Vaga que não apareceu ganha uma ausência, anotada em
   ``presenca_anuncios``; o status dela NÃO muda (sumir da listagem é comum em
   sites de notícia, em que a vaga só desce de página).
3. Com ``AUSENCIAS_PARA_CONFERIR`` ausências seguidas, e só com ``--verificar``,
   a página da vaga é aberta (respeitando robots.txt e um intervalo por site;
   as conferidas há mais tempo vão primeiro): 404/410, redirecionamento para a
   listagem ou aviso de encerramento → ``encerrado``. Só aqui o status muda.
   Vaga encerrada que volta a aparecer na listagem é ``reaberto``.
4. Vagas encerradas que já foram publicadas no Empregos entram na
   ``fila_remocao_api`` do relatório. Nada é enviado à API por aqui.

Sem ``--confirmar`` nada é gravado no MongoDB; o relatório sai do mesmo jeito.
A rotina diária coleta com ``--janela-horas 24``, que não lê a listagem inteira:
para esta conferência funcionar, rode antes uma coleta sem janela, por exemplo:

    .\\.venv\\Scripts\\python.exe scripts\\processar_lote.py --coletar --confirmar
        --catalogo config\\lote_10mil_triado\\catalogo_fontes.csv
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from observatorio_vagas.crawling.urls import normalizar_url_vaga
from observatorio_vagas.domain.elegibilidade import (
    IDADE_MAXIMA_PUBLICACAO_DIAS,
    STATUS_PUBLICAVEIS,
)
from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos, StatusAnuncio
from observatorio_vagas.domain.vagas_removidas import (
    AUSENCIAS_PARA_CONFERIR,
    SituacaoDetalhe,
    avaliar_coleta_da_fonte,
    classificar_detalhe,
    proporcao_vista_suficiente,
)

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
DIRETORIO_COBERTURA = RAIZ_PROJETO / "outputs" / "cobertura"
DIRETORIO_ESTADO = RAIZ_PROJETO / "config" / "lote_10mil_triado" / ".cache" / "lote"
DIRETORIO_SAIDA = RAIZ_PROJETO / "outputs" / "verificacao" / "vagas_removidas"
COLECAO_PRESENCA = "presenca_anuncios"

# ENCERRADO entra para que uma vaga encerrada que segue na listagem seja reaberta.
# Publicações que podem estar no Empregos (indeterminada: na dúvida, conferir).
SITUACOES_PUBLICADAS = (
    SituacaoPublicacaoEmpregos.SUCESSO,
    SituacaoPublicacaoEmpregos.INDETERMINADA,
)
STATUS_CONFERIDOS = frozenset({*STATUS_PUBLICAVEIS, StatusAnuncio.AUSENTE, StatusAnuncio.ENCERRADO})
INTERVALO_POR_SITE_S = 2.0
TIMEOUT_S = 15
LIMITE_BYTES_PAGINA = 2_000_000

# (status_http, url_final, texto visível) de uma página; None no status = sem resposta.
RespostaPagina = tuple[int | None, str | None, str]


@dataclass
class DecisaoAnuncio:
    """O que a conferência concluiu sobre um anúncio."""

    anuncio_id: Any
    alvo_id: str
    url: str
    status_atual: str
    acao: str  # vista | reaberta | ausente | suspeita | encerrada | ativa | indeterminada
    ausencias_seguidas: int
    motivo: str = ""
    # Última vez que a página foi aberta (ISO); "" = nunca. Ordena a conferência.
    verificado_em: str = ""
    # Já está no Empregos: conferida toda semana e antes das outras.
    publicada: bool = False


@dataclass
class ResultadoConferencia:
    """Resumo por fonte e decisões por anúncio."""

    fontes: list[dict[str, Any]] = field(default_factory=list)
    decisoes: list[DecisaoAnuncio] = field(default_factory=list)

    def contar(self) -> Counter[str]:
        return Counter(decisao.acao for decisao in self.decisoes)


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compara as vagas do MongoDB com os links vistos na última coleta completa "
            "de cada fonte e marca as que saíram do site. Sem --confirmar, só mostra."
        )
    )
    parser.add_argument(
        "--data",
        type=date.fromisoformat,
        default=None,
        help="dia da coleta a comparar, AAAA-MM-DD (padrão: hoje)",
    )
    parser.add_argument(
        "--cobertura",
        type=Path,
        nargs="*",
        default=None,
        help="relatórios de cobertura (padrão: outputs/cobertura/coleta_<data>*.json)",
    )
    parser.add_argument(
        "--estado",
        type=Path,
        default=DIRETORIO_ESTADO,
        help="pasta com fila_*.json do estado incremental (padrão: catálogo triado)",
    )
    parser.add_argument(
        "--verificar",
        action="store_true",
        help=f"abre a página das vagas com {AUSENCIAS_PARA_CONFERIR}+ ausências para confirmar",
    )
    parser.add_argument(
        "--verificar-publicadas",
        action="store_true",
        help=(
            "abre também a página de TODA vaga já publicada no Empregos, mesmo sem "
            "ausência na listagem (vêm antes das outras na cota de páginas)"
        ),
    )
    parser.add_argument(
        "--todas-as-idades",
        action="store_true",
        help=(
            f"confere também anúncios publicados há mais de {IDADE_MAXIMA_PUBLICACAO_DIAS} "
            "dias (padrão: só os que ainda podem ser publicados, mais os sem data)"
        ),
    )
    parser.add_argument(
        "--max-verificacoes",
        type=int,
        default=200,
        help="máximo de páginas abertas por execução (padrão: 200)",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="grava status e ausências no MongoDB",
    )
    parser.add_argument("--saida", type=Path, default=None, help="relatório JSON")
    return parser


def filtro_de_idade(dia: date, *, todas: bool = False) -> dict[str, Any]:
    """Só anúncios que ainda podem ser publicados: até 30 dias, ou sem data.

    Mesmo prazo da regra de publicação (``IDADE_MAXIMA_PUBLICACAO_DIAS``). Vaga
    já publicada no Empregos entra por ``--verificar-publicadas`` qualquer que
    seja a idade, porque continua no site até ser removida.
    """

    if todas:
        return {}
    limite = datetime.combine(
        dia - timedelta(days=IDADE_MAXIMA_PUBLICACAO_DIAS), datetime.min.time()
    )
    return {"$or": [{"publicado_em": {"$gte": limite}}, {"publicado_em": None}]}


def arquivos_de_cobertura(diretorio: Path, dia: date) -> list[Path]:
    """Relatórios cujo nome começa pelo dia da coleta (o spider usa o instante UTC)."""

    return sorted(diretorio.glob(f"coleta_{dia:%Y%m%d}T*.json"))


def carregar_coberturas(arquivos: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Junta as fontes de vários relatórios; o relatório mais recente vence."""

    fontes: dict[str, dict[str, Any]] = {}
    for arquivo in sorted(arquivos, key=lambda caminho: caminho.name):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        for fonte in dados.get("fontes", []):
            alvo_id = fonte.get("alvo_id")
            if isinstance(alvo_id, str):
                # O motivo de fim do spider vale para todas as fontes do relatório.
                fontes[alvo_id] = {**fonte, "encerramento_coleta": dados.get("encerramento")}
    return fontes


def carregar_vistos(diretorio: Path) -> dict[str, dict[str, str]]:
    """Última data em que cada link apareceu numa listagem, por fonte."""

    vistos: dict[str, dict[str, str]] = {}
    for arquivo in sorted(diretorio.glob("fila_*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for alvo_id, urls in (dados.get("vistos") or {}).items():
            destino = vistos.setdefault(alvo_id, {})
            for url, data in urls.items():
                if data > destino.get(url, ""):
                    destino[url] = data
    return vistos


def avaliar(
    *,
    coberturas: Mapping[str, Mapping[str, Any]],
    vistos: Mapping[str, Mapping[str, str]],
    anuncios_por_alvo: Mapping[str, Sequence[Mapping[str, Any]]],
    presencas: Mapping[Any, Mapping[str, Any]],
    dia: date,
) -> ResultadoConferencia:
    """Decide, sem gravar nada, o que aconteceu com cada vaga das fontes completas."""

    resultado = ResultadoConferencia()
    hoje = dia.isoformat()
    for alvo_id, fonte in sorted(coberturas.items()):
        vistos_alvo = vistos.get(alvo_id, {})
        # Só vale comparar a vaga cujo link já apareceu alguma vez numa listagem
        # da fonte. Anúncios lidos da própria listagem (JSON-LD, JSON, CKAN) ou
        # com link que nunca é listado ficariam "ausentes" toda semana.
        listaveis = [a for a in anuncios_por_alvo.get(alvo_id, ()) if _url(a) in vistos_alvo]
        encerrados = [a for a in listaveis if a["status"] == StatusAnuncio.ENCERRADO.value]
        anuncios = [a for a in listaveis if a["status"] != StatusAnuncio.ENCERRADO.value]
        completa, motivo = avaliar_coleta_da_fonte(fonte)
        # Coleta que passa da meia-noite grava o dia seguinte: vale "desde o dia".
        vistos_hoje = {url for url, data in vistos_alvo.items() if data >= hoje}
        presentes = [a for a in anuncios if _url(a) in vistos_hoje]
        if completa and not proporcao_vista_suficiente(vistas=len(presentes), ativas=len(anuncios)):
            completa, motivo = False, "queda_brusca_de_vagas_vistas"
        resultado.fontes.append(
            {
                "alvo_id": alvo_id,
                "completa": completa,
                "motivo": motivo,
                "vagas_ativas": len(anuncios),
                "vagas_vistas": len(presentes),
            }
        )
        if not completa:
            continue

        for anuncio in encerrados:
            # Encerrada (por engano ou por validade) que continua na listagem.
            if _url(anuncio) in vistos_hoje:
                resultado.decisoes.append(
                    _decisao(anuncio, alvo_id, "reaberta", 0, "voltou_a_aparecer")
                )

        ids_presentes = {a["_id"] for a in presentes}
        for anuncio in anuncios:
            presenca = presencas.get(anuncio["_id"], {})
            anteriores = int(presenca.get("ausencias_seguidas", 0))
            if anuncio["_id"] in ids_presentes:
                if anteriores or anuncio["status"] == StatusAnuncio.AUSENTE.value:
                    resultado.decisoes.append(
                        _decisao(anuncio, alvo_id, "vista", 0, "voltou_a_aparecer")
                    )
                continue
            # Rodar duas vezes no mesmo dia não conta duas ausências.
            ausencias = anteriores if presenca.get("ultima_ausencia_em") == hoje else anteriores + 1
            acao = "suspeita" if ausencias >= AUSENCIAS_PARA_CONFERIR else "ausente"
            decisao = _decisao(anuncio, alvo_id, acao, ausencias, "nao_apareceu_na_listagem")
            decisao.verificado_em = str(presenca.get("verificado_em") or "")
            resultado.decisoes.append(decisao)
    return resultado


def incluir_publicadas(
    resultado: ResultadoConferencia,
    anuncios_publicados: Iterable[Mapping[str, Any]],
) -> int:
    """Põe toda vaga publicada na conferência da página, sumida ou não da listagem.

    Vaga no Empregos não pode esperar 2 coletas completas: a fonte pode nem ser
    comparável (listagem incompleta, JavaScript) e a vaga ficaria no ar à toa.
    """

    por_id = {decisao.anuncio_id: decisao for decisao in resultado.decisoes}
    incluidas = 0
    for anuncio in anuncios_publicados:
        if anuncio.get("status") == StatusAnuncio.ENCERRADO.value or not anuncio.get("url"):
            continue
        decisao = por_id.get(anuncio["_id"])
        if decisao is None:
            decisao = _decisao(
                anuncio, str(anuncio.get("alvo_id") or ""), "suspeita", 0, "conferencia_semanal"
            )
            resultado.decisoes.append(decisao)
            por_id[decisao.anuncio_id] = decisao
        elif decisao.acao in {"vista", "ausente"}:
            decisao.acao = "suspeita"
            decisao.motivo = "conferencia_semanal"
        decisao.publicada = True
        incluidas += 1
    return incluidas


def verificar_suspeitas(
    decisoes: Sequence[DecisaoAnuncio],
    *,
    buscar: Callable[[str], RespostaPagina],
    maximo: int,
) -> int:
    """Abre a página das suspeitas e troca a ação pela conclusão. Devolve quantas abriu."""

    abertas = 0
    # Nunca conferidas primeiro, depois as conferidas há mais tempo: a cota não
    # fica presa nas mesmas páginas a cada execução.
    suspeitas = sorted(
        (decisao for decisao in decisoes if decisao.acao == "suspeita"),
        key=lambda decisao: (not decisao.publicada, decisao.verificado_em),
    )
    for decisao in suspeitas:
        if abertas >= maximo:
            break
        abertas += 1
        status, url_final, texto = buscar(decisao.url)
        situacao, motivo = classificar_detalhe(
            status_http=status, url_pedida=decisao.url, url_final=url_final, texto=texto
        )
        decisao.motivo = motivo
        if situacao is SituacaoDetalhe.ENCERRADA:
            decisao.acao = "encerrada"
        elif situacao is SituacaoDetalhe.ATIVA:
            # A página existe, só não estava na listagem: não é encerramento.
            decisao.acao = "ativa"
            decisao.ausencias_seguidas = 0
        else:
            decisao.acao = "indeterminada"
    return abertas


class BuscadorPaginas:
    """GET educado: respeita robots.txt e espera entre pedidos ao mesmo site."""

    def __init__(self, user_agent: str) -> None:
        import requests

        self._sessao = requests.Session()
        self._sessao.headers["User-Agent"] = user_agent
        self._user_agent = user_agent
        self._robots: dict[str, RobotFileParser | None] = {}
        self._ultimo_pedido: dict[str, float] = {}

    def __call__(self, url: str) -> RespostaPagina:
        partes = urlsplit(url)
        origem = f"{partes.scheme}://{partes.netloc}"
        robots = self._carregar_robots(origem)
        if robots is None or not robots.can_fetch(self._user_agent, url):
            return None, None, ""
        espera = INTERVALO_POR_SITE_S - (time.monotonic() - self._ultimo_pedido.get(origem, 0))
        if espera > 0:
            time.sleep(espera)
        self._ultimo_pedido[origem] = time.monotonic()
        try:
            resposta = self._sessao.get(url, timeout=TIMEOUT_S, allow_redirects=True, stream=True)
            corpo = resposta.raw.read(LIMITE_BYTES_PAGINA, decode_content=True)
        except Exception:  # noqa: BLE001 - qualquer falha de rede é "sem resposta"
            return None, None, ""
        finally:
            if "resposta" in locals():
                resposta.close()
        tipo = resposta.headers.get("Content-Type", "")
        # Sem charset no cabeçalho o requests supõe ISO-8859-1 e estraga os acentos
        # ("disponível" deixaria de casar com o aviso de encerramento).
        codificacao = resposta.encoding if "charset" in tipo.casefold() else None
        texto = _texto_visivel(corpo, codificacao) if "html" in tipo else ""
        return resposta.status_code, resposta.url, texto

    def _carregar_robots(self, origem: str) -> RobotFileParser | None:
        """None quando o robots.txt não pôde ser lido: na dúvida, não se pede a página."""

        if origem not in self._robots:
            leitor = RobotFileParser()
            try:
                resposta = self._sessao.get(f"{origem}/robots.txt", timeout=TIMEOUT_S)
            except Exception:  # noqa: BLE001
                self._robots[origem] = None
                return None
            if resposta.status_code >= 500:
                self._robots[origem] = None
                return None
            if resposta.status_code >= 400:
                leitor.allow_all = True
            else:
                leitor.parse(resposta.text.splitlines())
            self._robots[origem] = leitor
        return self._robots[origem]


def _texto_visivel(corpo: bytes, codificacao: str | None) -> str:
    from parsel import Selector

    if codificacao:
        html = corpo.decode(codificacao, errors="replace")
    else:
        try:
            html = corpo.decode("utf-8")
        except UnicodeDecodeError:
            html = corpo.decode("cp1252", errors="replace")
    # Menu, cabeçalho, rodapé e barras laterais costumam listar OUTRAS vagas
    # ("Vaga encerrada"); só o corpo da página fala desta.
    partes = Selector(text=html).xpath(
        "//title//text() | //body//text()[not(ancestor::script) and not(ancestor::style)"
        " and not(ancestor::noscript) and not(ancestor::nav) and not(ancestor::aside)"
        " and not(ancestor::footer) and not(ancestor::header)]"
    )
    return " ".join(" ".join(partes.getall()).split())


def _url(anuncio: Mapping[str, Any]) -> str:
    return normalizar_url_vaga(str(anuncio.get("url") or ""))


def _decisao(
    anuncio: Mapping[str, Any], alvo_id: str, acao: str, ausencias: int, motivo: str
) -> DecisaoAnuncio:
    return DecisaoAnuncio(
        anuncio_id=anuncio["_id"],
        alvo_id=alvo_id,
        url=anuncio["url"],
        status_atual=anuncio["status"],
        acao=acao,
        ausencias_seguidas=ausencias,
        motivo=motivo,
    )


# Só a página confirmada muda o status. Sumir da listagem fica só no histórico
# (presenca_anuncios); ``vista`` só desfaz o AUSENTE de execuções antigas.
NOVO_STATUS: dict[str, StatusAnuncio | None] = {
    "vista": None,
    "reaberta": StatusAnuncio.REABERTO,
    "ativa": None,
    "ausente": None,
    "suspeita": None,
    "indeterminada": None,
    "encerrada": StatusAnuncio.ENCERRADO,
}
ACOES_COM_PAGINA_ABERTA = frozenset({"encerrada", "ativa", "indeterminada"})


def gravar(banco: Any, decisoes: Sequence[DecisaoAnuncio], dia: date) -> int:
    """Atualiza status do anúncio e o histórico de presença. Nada é apagado."""

    from pymongo import UpdateOne

    agora = datetime.now(UTC)
    anuncios: list[UpdateOne] = []
    presencas: list[UpdateOne] = []
    for decisao in decisoes:
        novo = NOVO_STATUS[decisao.acao]
        if novo is None and decisao.status_atual == StatusAnuncio.AUSENTE.value:
            novo = StatusAnuncio.ATIVO
        if novo is not None and novo.value != decisao.status_atual:
            anuncios.append(
                UpdateOne({"_id": decisao.anuncio_id}, {"$set": {"status": novo.value}})
            )
        campos: dict[str, Any] = {
            "alvo_id": decisao.alvo_id,
            "url": decisao.url,
            "ausencias_seguidas": decisao.ausencias_seguidas,
            "ultima_acao": decisao.acao,
            "motivo": decisao.motivo,
            "atualizado_em": agora,
        }
        if decisao.acao in {"ausente", "suspeita", "indeterminada"}:
            campos["ultima_ausencia_em"] = dia.isoformat()
        if decisao.acao in ACOES_COM_PAGINA_ABERTA:
            campos["verificado_em"] = agora.isoformat()
        if decisao.acao == "encerrada":
            campos["encerrado_em"] = agora
        presencas.append(UpdateOne({"_id": decisao.anuncio_id}, {"$set": campos}, upsert=True))
    if anuncios:
        banco["anuncios"].bulk_write(anuncios, ordered=False)
    if presencas:
        banco[COLECAO_PRESENCA].bulk_write(presencas, ordered=False)
    return len(anuncios)


def fila_remocao_api(banco: Any, anuncio_ids: Sequence[Any]) -> list[dict[str, Any]]:
    """Encerradas que já estão no ar no Empregos: precisam sair pela API."""

    if not anuncio_ids:
        return []
    cursor = banco["publicacoes_empregos"].find(
        {
            "anuncio_id": {"$in": list(anuncio_ids)},
            "situacao": SituacaoPublicacaoEmpregos.SUCESSO.value,
        },
        {"anuncio_id": 1, "vaga_id": 1, "chave_idempotencia": 1},
    )
    return [{chave: str(valor) for chave, valor in documento.items()} for documento in cursor]


def executar(argumentos: list[str] | None = None) -> int:
    opcoes = criar_parser().parse_args(argumentos)
    dia = opcoes.data or date.today()
    arquivos = (
        opcoes.cobertura if opcoes.cobertura else arquivos_de_cobertura(DIRETORIO_COBERTURA, dia)
    )
    if not arquivos:
        print(f"ERRO: nenhum relatório de cobertura da coleta de {dia} em {DIRETORIO_COBERTURA}")
        return 1

    from observatorio_vagas.config import get_settings
    from observatorio_vagas.crawling.settings import USER_AGENT
    from observatorio_vagas.storage.mongodb import ConexaoMongoDB

    coberturas = carregar_coberturas(arquivos)
    vistos = carregar_vistos(opcoes.estado)
    status = [s.value for s in STATUS_CONFERIDOS]
    with ConexaoMongoDB(get_settings()) as conexao:
        banco = conexao.banco
        anuncios_por_alvo: dict[str, list[dict[str, Any]]] = {}
        for documento in banco["anuncios"].find(
            {
                "alvo_id": {"$in": list(coberturas)},
                "status": {"$in": status},
                **filtro_de_idade(dia, todas=opcoes.todas_as_idades),
            },
            {"alvo_id": 1, "url": 1, "status": 1},
        ):
            anuncios_por_alvo.setdefault(documento["alvo_id"], []).append(documento)
        ids = [a["_id"] for lista in anuncios_por_alvo.values() for a in lista]
        presencas = {
            documento["_id"]: documento
            for documento in banco[COLECAO_PRESENCA].find({"_id": {"$in": ids}})
        }
        resultado = avaliar(
            coberturas=coberturas,
            vistos=vistos,
            anuncios_por_alvo=anuncios_por_alvo,
            presencas=presencas,
            dia=dia,
        )
        if opcoes.verificar_publicadas:
            publicadas_ids = banco["publicacoes_empregos"].distinct(
                "anuncio_id",
                {"situacao": {"$in": [s.value for s in SITUACOES_PUBLICADAS]}},
            )
            incluir_publicadas(
                resultado,
                banco["anuncios"].find(
                    {"_id": {"$in": publicadas_ids}}, {"alvo_id": 1, "url": 1, "status": 1}
                ),
            )
        abertas = 0
        if opcoes.verificar:
            abertas = verificar_suspeitas(
                resultado.decisoes,
                buscar=BuscadorPaginas(USER_AGENT),
                maximo=opcoes.max_verificacoes,
            )
        alterados = gravar(banco, resultado.decisoes, dia) if opcoes.confirmar else 0
        encerradas = [d.anuncio_id for d in resultado.decisoes if d.acao == "encerrada"]
        remocao = fila_remocao_api(banco, encerradas)

    contagem = resultado.contar()
    completas = sum(1 for fonte in resultado.fontes if fonte["completa"])
    motivos = Counter(fonte["motivo"] for fonte in resultado.fontes if not fonte["completa"])
    saida = opcoes.saida or DIRETORIO_SAIDA / f"{dia.isoformat()}.json"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(
        json.dumps(
            {
                "dia": dia.isoformat(),
                "modo": "gravado" if opcoes.confirmar else "previa",
                "relatorios_cobertura": [str(arquivo) for arquivo in arquivos],
                "fontes_avaliadas": len(resultado.fontes),
                "fontes_completas": completas,
                "fontes_ignoradas_por_motivo": dict(motivos.most_common()),
                "decisoes": dict(contagem),
                "paginas_abertas": abertas,
                "fila_remocao_api": remocao,
                "fontes": resultado.fontes,
                "anuncios": [
                    {
                        "anuncio_id": str(d.anuncio_id),
                        "alvo_id": d.alvo_id,
                        "url": d.url,
                        "acao": d.acao,
                        "ausencias_seguidas": d.ausencias_seguidas,
                        "motivo": d.motivo,
                    }
                    for d in resultado.decisoes
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("# CONFERÊNCIA DE VAGAS REMOVIDAS DA ORIGEM")
    print(f"Modo: {'GRAVAÇÃO CONFIRMADA' if opcoes.confirmar else 'SOMENTE PRÉVIA'}")
    print(f"Coleta de {dia}: {len(arquivos)} relatório(s), {len(resultado.fontes)} fontes")
    print(f"Fontes com listagem completa (comparáveis): {completas}")
    for motivo, quantidade in motivos.most_common(6):
        print(f"  ignorada ({motivo}): {quantidade}")
    print(
        "Vagas: "
        f"{contagem['ausente']} ausentes pela 1ª vez, "
        f"{contagem['suspeita']} suspeitas sem conferir, "
        f"{contagem['vista']} voltaram a aparecer, "
        f"{contagem['reaberta']} encerradas reabertas"
    )
    if opcoes.verificar:
        print(
            f"Páginas abertas: {abertas} → {contagem['encerrada']} encerradas, "
            f"{contagem['ativa']} no ar, {contagem['indeterminada']} sem conclusão"
        )
    print(f"Precisam sair do Empregos pela API: {len(remocao)}")
    if opcoes.confirmar:
        print(f"Status alterados no MongoDB: {alterados}")
    else:
        print("Nada foi gravado. Para aplicar, rode de novo com --confirmar.")
    print(f"Relatório: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
