"""Regras para decidir se uma vaga saiu do site de origem.

Os sinais vão do mais barato ao mais caro:

1. a coleta de uma fonte foi completa e saudável, e o link da vaga não apareceu
   em nenhuma listagem dela (comparação pelo link normalizado);
2. o sumiço se repete em ``AUSENCIAS_PARA_CONFERIR`` coletas seguidas;
3. só então a página da vaga é aberta para confirmar (404/410,
   redirecionamento para a listagem ou aviso de vaga encerrada).

Uma coleta incompleta (com janela de horas, limite atingido, listagens não
visitadas ou erro de acesso) nunca conta como ausência: "não vi hoje" não
prova que a vaga saiu. Nada aqui acessa banco ou rede.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

# Coletas completas seguidas em que a vaga precisa faltar antes de abrir a página.
AUSENCIAS_PARA_CONFERIR = 2

# Abaixo desta fração de vagas vistas, a listagem provavelmente quebrou
# (site mudou de estrutura, paginação parou) e o dia não conta como ausência.
PROPORCAO_MINIMA_VISTA = 0.5
VAGAS_MINIMAS_PARA_PROPORCAO = 4

_STATUS_DE_ACESSO_RESTRITO = frozenset({401, 403, 429})

# Frases que falam DESTA vaga. Rótulos soltos ("Vaga encerrada", "Inscrições
# encerradas em 30/11") aparecem em listas laterais de outras vagas e em prazos
# futuros, e gerariam falso positivo. O texto já chega sem acento.
_AVISOS_DE_ENCERRAMENTO = tuple(
    re.compile(padrao)
    for padrao in (
        r"\besta (vaga|oportunidade) (ja )?(foi )?"
        r"(encerrada|expirada|preenchida|finalizada|cancelada)\b",
        r"\b(esta )?(vaga|oportunidade) nao esta mais (disponivel|ativa|aberta)\b",
        r"\besta (vaga|oportunidade) nao (esta mais|existe mais|recebe mais)\b",
        r"\b(as )?(inscricoes|candidaturas) (para esta vaga )?(foram|estao) encerradas\b",
        r"\b(este )?processo seletivo (ja )?foi (encerrado|finalizado)\b",
        r"\bthis (job|position|vacancy) is no longer available\b",
        r"\bthis (job|position|vacancy) has (been filled|expired|been closed)\b",
        r"\bno longer accepting applications\b",
        r"\bthis job has expired\b",
        r"\besta vacante (ya )?(esta cerrada|no esta disponible|ha expirado)\b",
    )
)


# "404 disfarçado": o site responde 200, mas mostra uma página de "não
# encontrada" no lugar da vaga. Frases de página de erro, não de vaga; o menu,
# o cabeçalho, o rodapé e as barras laterais já foram tirados do texto.
_AVISOS_DE_PAGINA_INEXISTENTE = tuple(
    re.compile(padrao)
    for padrao in (
        r"\bpagina nao (foi )?(encontrada|localizada)\b",
        r"\bpagina (inexistente|nao existe)\b",
        r"\b(a )?pagina que voce (procura|esta procurando|tentou acessar) nao\b",
        r"\b(esta |a )?(vaga|oportunidade|publicacao|anuncio) (nao (foi )?(encontrada|localizada)|"
        r"inexistente|nao existe)\b",
        r"\bconteudo nao (foi )?(encontrado|localizado)\b",
        r"\bnao (foi possivel|conseguimos) encontrar (a|esta) (pagina|vaga)\b",
        r"\berro 404\b",
        r"\b404 (not found|nao encontrad[ao])\b",
        r"\b(page|job|position) not found\b",
        r"\bthis page (does not|doesn.?t|could not be found)\b",
        r"\bpagina no encontrada\b",
    )
)


class SituacaoDetalhe(StrEnum):
    """O que a página da vaga mostrou na conferência."""

    ENCERRADA = "encerrada"
    ATIVA = "ativa"
    INDETERMINADA = "indeterminada"


def classificar_detalhe(
    *,
    status_http: int | None,
    url_pedida: str,
    url_final: str | None,
    texto: str,
) -> tuple[SituacaoDetalhe, str]:
    """Classifica a resposta da página da vaga e devolve o motivo."""

    if status_http is None:
        return SituacaoDetalhe.INDETERMINADA, "sem_resposta"
    if status_http in {404, 410}:
        return SituacaoDetalhe.ENCERRADA, f"http_{status_http}"
    if status_http in _STATUS_DE_ACESSO_RESTRITO:
        # Bloqueio não diz nada sobre a vaga; e não se insiste.
        return SituacaoDetalhe.INDETERMINADA, f"acesso_restrito_{status_http}"
    if not 200 <= status_http < 300:
        return SituacaoDetalhe.INDETERMINADA, f"http_{status_http}"

    if url_final and _redirecionou_para_listagem(url_pedida, url_final):
        return SituacaoDetalhe.ENCERRADA, "redirecionou_para_listagem"

    normalizado = _normalizar_texto(texto)
    for padrao in _AVISOS_DE_ENCERRAMENTO:
        if padrao.search(normalizado):
            return SituacaoDetalhe.ENCERRADA, "aviso_de_encerramento"
    for padrao in _AVISOS_DE_PAGINA_INEXISTENTE:
        if padrao.search(normalizado):
            return SituacaoDetalhe.ENCERRADA, "pagina_nao_encontrada"

    return SituacaoDetalhe.ATIVA, "pagina_disponivel"


def avaliar_coleta_da_fonte(fonte: Mapping[str, Any]) -> tuple[bool, str]:
    """Diz se a coleta de uma fonte viu a listagem inteira, sem erro.

    ``fonte`` é uma entrada de ``fontes`` no relatório de cobertura do spider,
    com ``encerramento_coleta`` (o motivo de fim do spider, que fica no topo do
    relatório) copiado para ela. Relatórios antigos, sem os campos de
    completude, nunca são considerados completos.
    """

    if "listagens_sem_resposta" not in fonte:
        return False, "relatorio_sem_dados_de_completude"
    if fonte.get("encerramento_coleta") != "finished":
        # closespider_timeout/pagecount: a fila foi abandonada no meio.
        return False, "coleta_interrompida"
    if fonte.get("listagens_inalteradas"):
        # O crawler para na listagem igual à anterior e não vê o resto.
        return False, "listagem_inalterada"
    if fonte.get("listagens_sem_resposta") or fonte.get("listagens_descartadas"):
        return False, "listagem_sem_resposta_ou_descartada"
    if fonte.get("janela_horas"):
        return False, "coleta_com_janela_de_horas"
    if fonte.get("janela_encerrada"):
        return False, "fonte_encerrada_antes_do_fim"
    if "limite_atingido" in (fonte.get("motivos_fim_navegacao") or ()):
        return False, "limite_de_paginas_atingido"
    if fonte.get("listagens_nao_visitadas"):
        return False, "listagens_nao_visitadas"

    listagens = [
        resultado
        for resultado in (fonte.get("resultados") or {}).values()
        if isinstance(resultado, Mapping) and resultado.get("tipo_pagina") == "inicial"
    ]
    if not listagens:
        return False, "sem_listagem"
    for resultado in listagens:
        status = resultado.get("status_http")
        if resultado.get("erro") or status is None:
            return False, "falha_de_rede_na_listagem"
        # 404 é o fim normal da paginação; qualquer outro erro esconde vagas.
        if status >= 400 and status != 404:
            return False, f"erro_http_{status}_na_listagem"
    if not any(_listagem_ok(resultado.get("status_http")) for resultado in listagens):
        return False, "nenhuma_listagem_respondeu"
    return True, "completa"


def proporcao_vista_suficiente(*, vistas: int, ativas: int) -> bool:
    """Protege contra listagem quebrada: sumiço em massa não é encerramento em massa."""

    if ativas <= 0:
        return True
    if vistas == 0:
        return False
    if ativas < VAGAS_MINIMAS_PARA_PROPORCAO:
        return True
    return vistas / ativas >= PROPORCAO_MINIMA_VISTA


def _listagem_ok(status: object) -> bool:
    return isinstance(status, int) and (200 <= status < 300 or status == 304)


def _redirecionou_para_listagem(url_pedida: str, url_final: str) -> bool:
    """Vaga que manda para a home ou para um caminho "pai" saiu do ar."""

    pedida = urlsplit(url_pedida)
    final = urlsplit(url_final)
    if (pedida.hostname or "").removeprefix("www.") != (final.hostname or "").removeprefix("www."):
        # Outro domínio (ATS, login) não prova encerramento.
        return False
    caminho_pedido = pedida.path.rstrip("/")
    caminho_final = final.path.rstrip("/")
    if caminho_final == caminho_pedido:
        return False
    if not caminho_final:
        return True
    # Só um caminho "pai" genérico (/vagas, /carreiras) prova a saída. Com número
    # (/vagas/123) é o endereço canônico da própria vaga, sem o slug.
    return (
        caminho_pedido.startswith(f"{caminho_final}/")
        and caminho_final.count("/") <= 1
        and not any(caractere.isdigit() for caractere in caminho_final)
    )


def _normalizar_texto(texto: str) -> str:
    """Minúsculas, sem acento e com espaços simples, para comparar frases."""

    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.casefold().split())
