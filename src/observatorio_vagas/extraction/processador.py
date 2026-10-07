"""Processamento em lote das páginas brutas coletadas."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from observatorio_vagas.crawling.adaptadores.agregadores import (
    AdaptadorAdzunaApi,
    AdaptadorJoobleApi,
)
from observatorio_vagas.crawling.filtro_conteudo import eh_conteudo_nao_empregaticio
from observatorio_vagas.crawling.inventario import (
    RegistroInventarioBruto,
    carregar_inventario_bruto,
)
from observatorio_vagas.crawling.raw_storage import ler_corpo_bruto
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import (
    Fonte,
    TipoPaginaColeta,
)
from observatorio_vagas.extraction.cache_paginas import CachePaginas
from observatorio_vagas.extraction.carreiras_empresas import extrair_vagas_carreiras_empresas
from observatorio_vagas.extraction.ckan import extrair_job_postings_ckan
from observatorio_vagas.extraction.data_publicacao import dia_publicacao
from observatorio_vagas.extraction.geolocalizacao_html import (
    enriquecer_job_posting_com_geolocalizacao,
    extrair_coordenadas_html,
)
from observatorio_vagas.extraction.html_generico import (
    TITULO_DE_LISTAGEM,
    extrair_job_posting_html_generico,
)
from observatorio_vagas.extraction.json_ld import (
    extrair_job_postings_json_ld,
)
from observatorio_vagas.extraction.json_publico import extrair_vagas_json_publico
from observatorio_vagas.extraction.leitura.aplicacao import CHAVE as CHAVE_LEITURA
from observatorio_vagas.extraction.leitura.aplicacao import ler_anuncio
from observatorio_vagas.extraction.leitura.camadas import montar_inventario
from observatorio_vagas.extraction.listagem_carreiras import (
    atribuir_empresa_do_site,
    extrair_vagas_listagem_carreiras,
)
from observatorio_vagas.extraction.mapeamento_json_ld import (
    converter_job_posting_em_anuncio,
)
from observatorio_vagas.extraction.metadados_empresa import (
    enriquecer_job_posting_com_empresa,
    extrair_metadados_empresa_html,
)
from observatorio_vagas.extraction.nic_br import extrair_job_posting_nic_br
from observatorio_vagas.extraction.noticias_vagas import (
    extrair_job_postings_noticia_itaqui,
    extrair_job_postings_noticia_palotina24h,
)
from observatorio_vagas.extraction.organizacoes import (
    extrair_vagas_carreiras_estaticas,
    extrair_vagas_jobconvo,
    extrair_vagas_organizacoes,
    extrair_vagas_teleperformance,
)
from observatorio_vagas.extraction.portal_publico import (
    extrair_job_postings_portal_publico,
)
from observatorio_vagas.extraction.querido_diario import (
    extrair_job_postings_querido_diario,
)
from observatorio_vagas.extraction.tor_project import extrair_job_posting_tor_project
from observatorio_vagas.extraction.url_candidatura import (
    extrair_url_candidatura_html,
)


@dataclass(frozen=True, slots=True)
class FalhaProcessamentoExtracao:
    """Problema encontrado durante o processamento de uma página."""

    referencia: str
    mensagem: str


@dataclass(frozen=True, slots=True)
class ResultadoProcessamentoExtracao:
    """Resumo completo de uma execução do extrator."""

    anuncios: tuple[AnuncioVaga, ...]
    paginas_analisadas: int
    paginas_ignoradas: int
    paginas_sem_json_ld: int
    documentos_encontrados: int
    anuncios_duplicados: int
    blocos_invalidos: int
    falhas: tuple[FalhaProcessamentoExtracao, ...]
    paginas_fora_filtro: int = 0
    paginas_incompativeis: int = 0
    anuncios_fora_data_publicacao: int = 0
    anuncios_sem_data_publicacao: int = 0
    paginas_do_cache: int = 0


def _pagina_pode_ser_processada(
    registro: RegistroInventarioBruto,
) -> bool:
    """Seleciona somente páginas adequadas para este extrator."""

    # A URL inicial pode ser uma listagem ou uma vaga direta.
    # Só tentamos extrair quando ela realmente contém JobPosting;
    # portanto, permitir a tentativa não transforma uma listagem em vaga.
    if registro.tipo_pagina not in {
        TipoPaginaColeta.INICIAL.value,
        TipoPaginaColeta.DETALHE_VAGA.value,
    }:
        return False

    # Respostas HTTP com erro não devem virar anúncios.
    if not 200 <= registro.status_http < 300:
        return False

    # Content-Type pode possuir parâmetros.
    #
    # Exemplo:
    #
    # text/html; charset=utf-8
    tipo_conteudo = (
        (registro.tipo_conteudo or "")
        .split(
            ";",
            maxsplit=1,
        )[0]
        .strip()
        .casefold()
    )

    if tipo_conteudo in {
        "text/html",
        "application/xhtml+xml",
    }:
        return True

    if registro.fonte in {
        Fonte.QUERIDO_DIARIO.value,
        Fonte.ADZUNA.value,
        Fonte.JOOBLE.value,
        Fonte.PAGINA_CARREIRAS.value,
    }:
        return tipo_conteudo == "application/json"

    # CKAN usa JSON somente como índice e CSV como conteúdo. Tipos
    # genéricos são aceitos apenas para essa fonte explicitamente tipada.
    if registro.fonte == Fonte.CKAN.value:
        return tipo_conteudo in {
            "application/csv",
            "application/json",
            "application/octet-stream",
            "text/csv",
            "text/plain",
        }

    return False


def _carregar_corpo_verificado(
    *,
    diretorio_base: Path,
    registro: RegistroInventarioBruto,
) -> bytes:
    """Lê o corpo e confirma sua integridade antes da extração."""

    caminho = (diretorio_base / registro.caminho_corpo).resolve()

    # Impede que um caminho adulterado saia da pasta bruta.
    if not caminho.is_relative_to(diretorio_base):
        raise ValueError("caminho do corpo está fora do diretório bruto")

    try:
        corpo = ler_corpo_bruto(caminho)

    except OSError as erro:
        raise ValueError("não foi possível ler o corpo armazenado") from erro

    if len(corpo) != registro.tamanho_bytes:
        raise ValueError("tamanho do corpo difere dos metadados")

    hash_calculado = sha256(corpo).hexdigest()

    if hash_calculado != registro.hash_conteudo:
        raise ValueError("SHA-256 do corpo difere dos metadados")

    return corpo


def _normalizar_alvo_id(
    alvo_id: str | None,
) -> str | None:
    """Normaliza o filtro opcional de alvo."""

    if alvo_id is None:
        return None

    alvo_normalizado = alvo_id.strip()

    if not alvo_normalizado:
        raise ValueError("alvo_id não pode ser vazio")

    return alvo_normalizado


def _validar_momento_inicial(
    coletado_desde: datetime | None,
) -> None:
    """Exige fuso horário no início opcional do lote."""

    if coletado_desde is None:
        return

    if coletado_desde.tzinfo is None or coletado_desde.utcoffset() is None:
        raise ValueError("coletado_desde precisa possuir fuso horário")


def _registro_passa_pelos_filtros(
    registro: RegistroInventarioBruto,
    *,
    alvo_id: str | None,
    coletado_desde: datetime | None,
) -> bool:
    """Aplica os limites de alvo e momento da coleta."""

    if coletado_desde is not None and registro.coletado_em < coletado_desde:
        return False

    return alvo_id is None or registro.alvo_id == alvo_id


@dataclass(slots=True)
class ParcialExtracao:
    """Resultado de um pedaço contíguo do inventário, ainda sem filtro de data.

    Pedaços de um mesmo alvo podem ser extraídos em processos diferentes e
    depois combinados por ``combinar_parciais`` com o mesmo resultado da
    extração sequencial.
    """

    # Ordem de inserção preservada: a primeira ocorrência é a mais recente.
    anuncios: dict[tuple[Fonte, str], AnuncioVaga]
    falhas: list[FalhaProcessamentoExtracao]
    paginas_analisadas: int = 0
    paginas_ignoradas: int = 0
    paginas_fora_filtro: int = 0
    paginas_incompativeis: int = 0
    paginas_sem_json_ld: int = 0
    documentos_encontrados: int = 0
    anuncios_duplicados: int = 0
    blocos_invalidos: int = 0
    paginas_do_cache: int = 0


def processar_respostas_brutas(
    diretorio_base: str | Path,
    *,
    alvo_id: str | None = None,
    coletado_desde: datetime | None = None,
    registros: tuple[RegistroInventarioBruto, ...] | None = None,
    publicado_em: date | None = None,
    cache_paginas: Path | None = None,
) -> ResultadoProcessamentoExtracao:
    """Extrai anúncios das respostas preservadas pelo crawler."""

    # Valida antes de qualquer leitura do disco.
    _normalizar_alvo_id(alvo_id)
    _validar_momento_inicial(coletado_desde)

    # O lote pode compartilhar um snapshot, sem reler todos os JSONs por alvo.
    # None carrega do disco; uma tupla vazia representa um inventário vazio.
    if registros is None:
        registros = carregar_inventario_bruto(Path(diretorio_base).resolve())

    return combinar_parciais(
        (
            extrair_parcial(
                diretorio_base,
                registros,
                alvo_id=alvo_id,
                coletado_desde=coletado_desde,
                cache_paginas=cache_paginas,
            ),
        ),
        publicado_em=publicado_em,
    )


def _normalizado(texto: str | None, limite: int | None = None) -> str:
    sem_acento = unicodedata.normalize("NFKD", (texto or "").casefold())
    letras = "".join(c for c in sem_acento if not unicodedata.combining(c))
    # Números saem: o site muda só o código/data ("-1040", "postado em 03/07").
    compacto = " ".join(re.sub(r"[^a-z]+", " ", letras).split())
    return compacto[:limite] if limite else compacto


TEXTO_MINIMO_PARA_COMPARAR = 200


def chave_de_conteudo(anuncio: AnuncioVaga) -> tuple[str, ...]:
    """Mesma vaga publicada várias vezes pelo site com endereços diferentes.

    O clickpetroleoegas publicou ~1.000 cópias de "Auxiliar Administrativo I - PCD"
    (mesmo título e texto, só o número no fim da URL muda). Endereço sem números,
    título, empresa, local e início do texto iguais = a mesma vaga.

    Texto curto não compara: vagas do SINE/CKAN têm o mesmo texto padrão e são
    vagas diferentes. Nesse caso a chave é o próprio anúncio (nunca repete).
    """

    texto = _normalizado(anuncio.descricao_original, 300)
    if len(texto) < TEXTO_MINIMO_PARA_COMPARAR:
        return (str(anuncio.fonte), anuncio.id_externo)
    partes = urlsplit(str(anuncio.url))
    return (
        (partes.hostname or "").removeprefix("www."),
        re.sub(r"\d+", "", partes.path),
        _normalizado(anuncio.titulo_original),
        _normalizado(anuncio.empresa_original),
        _normalizado(anuncio.localidade_original),
        texto,
    )


def combinar_parciais(
    parciais: Sequence[ParcialExtracao],
    *,
    publicado_em: date | None = None,
) -> ResultadoProcessamentoExtracao:
    """Junta pedaços na ordem do inventário e aplica o filtro de publicação.

    A deduplicação segue a mesma regra da extração sequencial: a primeira
    ocorrência de uma chave (a observação mais recente) é preservada.
    """

    anuncios_unicos: dict[tuple[Fonte, str], AnuncioVaga] = {}
    conteudos_vistos: set[tuple[str, ...]] = set()
    falhas: list[FalhaProcessamentoExtracao] = []
    duplicados_entre_pedacos = 0

    for parcial in parciais:
        falhas.extend(parcial.falhas)

        for chave, anuncio in parcial.anuncios.items():
            conteudo = chave_de_conteudo(anuncio)
            if chave in anuncios_unicos or conteudo in conteudos_vistos:
                duplicados_entre_pedacos += 1
                continue

            anuncios_unicos[chave] = anuncio
            conteudos_vistos.add(conteudo)

    selecionados = []
    fora_data = sem_data = 0
    for anuncio in anuncios_unicos.values():
        dia = dia_publicacao(anuncio.publicado_em)
        if publicado_em is not None and dia is None:
            sem_data += 1
        elif publicado_em is not None and dia != publicado_em:
            fora_data += 1
        else:
            selecionados.append(anuncio)

    def somar(campo: str) -> int:
        return sum(getattr(parcial, campo) for parcial in parciais)

    return ResultadoProcessamentoExtracao(
        anuncios=tuple(selecionados),
        anuncios_fora_data_publicacao=fora_data,
        anuncios_sem_data_publicacao=sem_data,
        paginas_analisadas=somar("paginas_analisadas"),
        paginas_ignoradas=somar("paginas_ignoradas"),
        paginas_sem_json_ld=somar("paginas_sem_json_ld"),
        documentos_encontrados=somar("documentos_encontrados"),
        anuncios_duplicados=somar("anuncios_duplicados") + duplicados_entre_pedacos,
        blocos_invalidos=somar("blocos_invalidos"),
        falhas=tuple(falhas),
        paginas_fora_filtro=somar("paginas_fora_filtro"),
        paginas_incompativeis=somar("paginas_incompativeis"),
        paginas_do_cache=somar("paginas_do_cache"),
    )


@dataclass(frozen=True, slots=True)
class AnalisePagina:
    """Resultado caro de uma página: o que os extratores encontraram nela.

    Depende só do conteúdo e do contexto da página. Por isso pode ser
    guardado em cache e reaproveitado quando a mesma página volta igual.
    """

    ignorada: bool
    fonte: Fonte | None = None
    documentos: tuple[Any, ...] = ()
    invalidos_pagina: int = 0
    resultado_url_candidatura: Any = None
    resultado_metadados_empresa: Any = None
    resultado_coordenadas: Any = None
    # Todas as camadas da página (só HTML com vagas), para a leitura completa.
    inventario: Any = None


def _analisar_pagina(
    base: Path,
    registro: RegistroInventarioBruto,
) -> AnalisePagina:
    """Lê o corpo verificado e aplica os extratores reconhecidos."""

    corpo = _carregar_corpo_verificado(
        diretorio_base=base,
        registro=registro,
    )

    if eh_conteudo_nao_empregaticio(
        url=registro.url_final,
        conteudo=corpo,
        codificacao=(registro.codificacao or "utf-8"),
    ):
        return AnalisePagina(ignorada=True)

    fonte = Fonte(registro.fonte)
    tipo_conteudo = (registro.tipo_conteudo or "").split(";", maxsplit=1)[0].strip().casefold()

    if fonte is Fonte.QUERIDO_DIARIO:
        resultado_querido_diario = extrair_job_postings_querido_diario(
            corpo,
            codificacao=(registro.codificacao or "utf-8"),
        )
        documentos = resultado_querido_diario.vagas
        invalidos_pagina = resultado_querido_diario.itens_invalidos
        resultado_url_candidatura = None
        resultado_metadados_empresa = None
        resultado_coordenadas = None
    elif fonte is Fonte.PAGINA_CARREIRAS and tipo_conteudo == "application/json":
        resultado_json_publico = extrair_vagas_json_publico(
            corpo, url=registro.url_final, codificacao=(registro.codificacao or "utf-8")
        )
        documentos = resultado_json_publico.vagas
        invalidos_pagina = 0
        resultado_url_candidatura = None
        resultado_metadados_empresa = None
        resultado_coordenadas = None
    elif fonte in {Fonte.ADZUNA, Fonte.JOOBLE}:
        try:
            dados_agregador = json.loads(corpo)
        except (UnicodeDecodeError, json.JSONDecodeError) as erro:
            raise ValueError("resposta da API agregadora não contém JSON válido") from erro
        adaptador_agregador = (
            AdaptadorAdzunaApi() if fonte is Fonte.ADZUNA else AdaptadorJoobleApi()
        )
        documentos = adaptador_agregador.extrair_job_postings(dados_agregador)
        invalidos_pagina = 0
        resultado_url_candidatura = None
        resultado_metadados_empresa = None
        resultado_coordenadas = None
    elif fonte is Fonte.CKAN:
        tipo_conteudo = (registro.tipo_conteudo or "").split(";", maxsplit=1)[0].strip().casefold()

        if tipo_conteudo in {"application/json", "text/html", "application/xhtml+xml"}:
            # Índices JSON e a interface pública HTML descobrem CSVs.
            documentos = ()
            invalidos_pagina = 0
        else:
            resultado_ckan = extrair_job_postings_ckan(
                corpo,
                url=registro.url_solicitada,
                codificacao=(registro.codificacao or "utf-8"),
                data_referencia=registro.coletado_em.date(),
            )
            documentos = resultado_ckan.vagas
            invalidos_pagina = resultado_ckan.linhas_invalidas

        resultado_url_candidatura = None
        resultado_metadados_empresa = None
        resultado_coordenadas = None
    else:
        # Extrai os objetos JobPosting do JSON-LD.
        resultado_json_ld = extrair_job_postings_json_ld(
            corpo,
            codificacao=(registro.codificacao or "utf-8"),
        )

        # Alguns portais públicos municipais oferecem editais em
        # HTML, mas não publicam Schema.org/JobPosting. O adaptador
        # converte somente páginas reconhecidas desse padrão.
        resultado_portal_publico = extrair_job_postings_portal_publico(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_noticia_palotina = extrair_job_postings_noticia_palotina24h(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_noticia_itaqui = extrair_job_postings_noticia_itaqui(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )

        resultado_url_candidatura = extrair_url_candidatura_html(
            corpo,
            url_base=registro.url_final,
        )
        resultado_metadados_empresa = extrair_metadados_empresa_html(
            corpo,
            url_base=registro.url_final,
        )
        resultado_coordenadas = extrair_coordenadas_html(corpo)
        invalidos_pagina = resultado_json_ld.blocos_invalidos
        resultado_organizacoes = extrair_vagas_organizacoes(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_carreiras_estaticas = extrair_vagas_carreiras_estaticas(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_jobconvo = extrair_vagas_jobconvo(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_teleperformance = extrair_vagas_teleperformance(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_nic_br = extrair_job_posting_nic_br(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_tor_project = extrair_job_posting_tor_project(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_empresas = extrair_vagas_carreiras_empresas(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        resultado_listagem_carreiras = extrair_vagas_listagem_carreiras(
            corpo,
            url=registro.url_final,
            codificacao=(registro.codificacao or "utf-8"),
        )
        documentos = (
            resultado_json_ld.vagas
            or resultado_portal_publico.vagas
            or resultado_noticia_palotina.vagas
            or resultado_noticia_itaqui.vagas
        )
        if resultado_organizacoes.dominio_reconhecido:
            documentos = resultado_json_ld.vagas or resultado_organizacoes.vagas
        if resultado_carreiras_estaticas.dominio_reconhecido:
            documentos = resultado_json_ld.vagas or resultado_carreiras_estaticas.vagas
        if resultado_jobconvo.dominio_reconhecido:
            # O JobConvo pode expor JSON-LD sintaticamente defeituoso
            # ou incompleto; o adaptador preserva o local e filtra o
            # empregador correto da página de carreira.
            documentos = resultado_jobconvo.vagas
        if resultado_teleperformance.dominio_reconhecido:
            documentos = resultado_teleperformance.vagas
        if resultado_nic_br.dominio_reconhecido:
            documentos = resultado_json_ld.vagas or resultado_nic_br.vagas
        if resultado_tor_project.dominio_reconhecido:
            documentos = resultado_json_ld.vagas or resultado_tor_project.vagas
        if resultado_empresas.dominio_reconhecido:
            documentos = resultado_empresas.vagas
        elif not documentos:
            documentos = atribuir_empresa_do_site(
                resultado_listagem_carreiras.vagas,
                url=registro.url_final,
                empresa_nome=registro.empresa_nome,
                corpo=corpo,
                codificacao=(registro.codificacao or "utf-8"),
            )

        # Páginas já descobertas como detalhes de vaga podem não
        # publicar JSON-LD. Nesse caso, usamos sinais HTML explícitos.
        if (
            not documentos
            and not resultado_organizacoes.dominio_reconhecido
            and not resultado_carreiras_estaticas.dominio_reconhecido
            and not resultado_jobconvo.dominio_reconhecido
            and not resultado_teleperformance.dominio_reconhecido
            and not resultado_nic_br.dominio_reconhecido
            and not resultado_tor_project.dominio_reconhecido
            and not resultado_empresas.dominio_reconhecido
            and registro.tipo_pagina == TipoPaginaColeta.DETALHE_VAGA.value
        ):
            resultado_html = extrair_job_posting_html_generico(
                corpo,
                url=registro.url_final,
                empresa_nome=registro.empresa_nome,
                codificacao=(registro.codificacao or "utf-8"),
            )
            documentos = resultado_html.vagas

    tipo_html = (registro.tipo_conteudo or "").split(";", 1)[0].strip().casefold() in {
        "text/html",
        "application/xhtml+xml",
    }

    return AnalisePagina(
        inventario=montar_inventario(
            corpo, url=registro.url_final, codificacao=registro.codificacao
        )
        if documentos and tipo_html
        else None,
        ignorada=False,
        fonte=fonte,
        documentos=tuple(documentos),
        invalidos_pagina=invalidos_pagina,
        resultado_url_candidatura=resultado_url_candidatura,
        resultado_metadados_empresa=resultado_metadados_empresa,
        resultado_coordenadas=resultado_coordenadas,
    )


def extrair_parcial(
    diretorio_base: str | Path,
    registros: Sequence[RegistroInventarioBruto],
    *,
    alvo_id: str | None = None,
    coletado_desde: datetime | None = None,
    cache_paginas: Path | None = None,
) -> ParcialExtracao:
    """Extrai anúncios de um pedaço do inventário, página por página.

    Com ``cache_paginas``, páginas que voltaram idênticas reaproveitam a
    análise guardada; a conversão em anúncio usa sempre a coleta atual.
    """

    cache = CachePaginas(cache_paginas) if cache_paginas is not None else None

    try:
        parcial = _extrair_parcial(
            diretorio_base,
            registros,
            alvo_id=alvo_id,
            coletado_desde=coletado_desde,
            cache=cache,
        )
    finally:
        if cache is not None:
            cache.fechar()

    if cache is not None:
        parcial.paginas_do_cache = cache.acertos

    return parcial


def _extrair_parcial(
    diretorio_base: str | Path,
    registros: Sequence[RegistroInventarioBruto],
    *,
    alvo_id: str | None,
    coletado_desde: datetime | None,
    cache: CachePaginas | None,
) -> ParcialExtracao:
    """Laço página por página de ``extrair_parcial``."""

    base = Path(diretorio_base).resolve()

    alvo_filtrado = _normalizar_alvo_id(alvo_id)

    _validar_momento_inicial(coletado_desde)

    # A combinação fonte + ID externo identifica um anúncio.
    anuncios_unicos: dict[
        tuple[Fonte, str],
        AnuncioVaga,
    ] = {}
    conteudos_vistos: set[tuple[str, ...]] = set()

    falhas: list[FalhaProcessamentoExtracao] = []

    paginas_analisadas = 0
    paginas_ignoradas = 0
    paginas_fora_filtro = 0
    paginas_incompativeis = 0
    paginas_sem_json_ld = 0
    documentos_encontrados = 0
    anuncios_duplicados = 0
    blocos_invalidos = 0

    for registro in registros:
        if not _registro_passa_pelos_filtros(
            registro,
            alvo_id=alvo_filtrado,
            coletado_desde=coletado_desde,
        ):
            paginas_ignoradas += 1
            paginas_fora_filtro += 1
            continue

        if not _pagina_pode_ser_processada(registro):
            paginas_ignoradas += 1
            paginas_incompativeis += 1
            continue

        paginas_analisadas += 1

        try:
            analise = cache.obter(registro) if cache is not None else None

            if analise is None:
                analise = _analisar_pagina(base, registro)

                if cache is not None:
                    cache.guardar(registro, analise)

        except Exception as erro:  # um extrator com defeito não derruba o alvo
            falhas.append(
                FalhaProcessamentoExtracao(
                    referencia=registro.referencia,
                    mensagem=str(erro),
                )
            )

            continue

        if analise.ignorada:
            paginas_ignoradas += 1
            continue

        fonte = analise.fonte
        documentos = analise.documentos
        invalidos_pagina = analise.invalidos_pagina
        resultado_url_candidatura = analise.resultado_url_candidatura
        resultado_metadados_empresa = analise.resultado_metadados_empresa
        resultado_coordenadas = analise.resultado_coordenadas

        blocos_invalidos += invalidos_pagina

        if not documentos:
            paginas_sem_json_ld += 1
            continue

        for indice, documento in enumerate(
            documentos,
            start=1,
        ):
            documentos_encontrados += 1

            try:
                if fonte in {Fonte.CKAN, Fonte.QUERIDO_DIARIO, Fonte.ADZUNA, Fonte.JOOBLE}:
                    documento_enriquecido = documento
                else:
                    documento_enriquecido = enriquecer_job_posting_com_empresa(
                        documento,
                        resultado_metadados_empresa,
                    )
                    documento_enriquecido = enriquecer_job_posting_com_geolocalizacao(
                        documento_enriquecido,
                        resultado_coordenadas,
                    )

                url_agregador = (
                    documento.get("url") if fonte in {Fonte.ADZUNA, Fonte.JOOBLE} else None
                )
                url_candidatura = (
                    documento.get("_observatorio_apply_url")
                    or url_agregador
                    or (
                        resultado_url_candidatura.url
                        if resultado_url_candidatura is not None
                        else documento.get("_observatorio_apply_url")
                    )
                )

                anuncio = converter_job_posting_em_anuncio(
                    documento_enriquecido,
                    alvo_id=registro.alvo_id,
                    fonte=fonte,
                    hash_conteudo=(registro.hash_conteudo),
                    referencia_bruta=(registro.referencia),
                    url_fallback=(registro.url_final),
                    # "javascript:alert(...)" e afins não são candidatura; antes
                    # derrubavam o anúncio e bloqueavam a gravação do alvo inteiro.
                    url_candidatura=(
                        url_candidatura
                        if isinstance(url_candidatura, str)
                        and urlsplit(url_candidatura).scheme in {"http", "https"}
                        else None
                    ),
                    observado_em=(registro.coletado_em),
                )

                # Leitura completa: todas as camadas, os 24 campos e o diagnóstico.
                anuncio = anuncio.model_copy(
                    update={
                        "campos_estruturados": {
                            **anuncio.campos_estruturados,
                            CHAVE_LEITURA: ler_anuncio(
                                anuncio,
                                documento=documento_enriquecido,
                                inventario=analise.inventario,
                                varias_vagas_na_pagina=len(documentos) > 1,
                                url_pagina=registro.url_final,
                                url_candidatura_html=(
                                    resultado_url_candidatura.url
                                    if resultado_url_candidatura is not None
                                    else None
                                ),
                            ),
                        }
                    }
                )

            except Exception as erro:  # um documento ruim vira falha registrada
                falhas.append(
                    FalhaProcessamentoExtracao(
                        referencia=registro.referencia,
                        mensagem=(f"JobPosting {indice} inválido: {erro}"),
                    )
                )

                continue

            # Título de lista ou chamada ("975 Vagas de Emprego para...", "341 vagas em
            # São Paulo/SP", "Bunge tem mais de 70 vagas, confira") não é uma vaga,
            # venha de qualquer extrator: ~2.000 na coleta de 06/10/2026.
            if TITULO_DE_LISTAGEM.search(anuncio.titulo_original):
                paginas_ignoradas += 1
                continue

            chave = anuncio.chave_fonte
            conteudo = chave_de_conteudo(anuncio)

            # O inventário apresenta primeiro os registros
            # mais recentes. Portanto, quando a chave já existe,
            # preservamos a observação mais nova. A mesma vaga republicada pelo site
            # com outro endereço (mesmo título, empresa, local e texto) também conta.
            if chave in anuncios_unicos or conteudo in conteudos_vistos:
                anuncios_duplicados += 1
                continue

            anuncios_unicos[chave] = anuncio
            conteudos_vistos.add(conteudo)

    return ParcialExtracao(
        anuncios=anuncios_unicos,
        falhas=falhas,
        paginas_analisadas=paginas_analisadas,
        paginas_ignoradas=paginas_ignoradas,
        paginas_fora_filtro=paginas_fora_filtro,
        paginas_incompativeis=paginas_incompativeis,
        paginas_sem_json_ld=paginas_sem_json_ld,
        documentos_encontrados=documentos_encontrados,
        anuncios_duplicados=anuncios_duplicados,
        blocos_invalidos=blocos_invalidos,
    )
