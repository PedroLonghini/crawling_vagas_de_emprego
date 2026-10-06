"""Extrai, resolve e associa empresas aos anúncios coletados."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.storage.contracts import (
    RepositorioAnuncios,
    RepositorioEmpresas,
)


class ErroResolucaoEmpresa(RuntimeError):
    """Indica que uma empresa não pôde ser resolvida com segurança."""


class ConflitoResolucaoEmpresa(ErroResolucaoEmpresa):
    """Indica que as evidências apontam para empresas diferentes."""


@dataclass(frozen=True, slots=True)
class ResultadoResolucaoEmpresa:
    """Resume o resultado da resolução de uma empresa."""

    empresa: Empresa
    anuncio: AnuncioVaga
    empresa_criada: bool
    empresa_atualizada: bool
    anuncio_atualizado: bool


def _texto(valor: object) -> str | None:
    """Converte um valor simples em texto não vazio."""

    if valor is None or isinstance(valor, bool):
        return None

    if not isinstance(valor, (str, int, float)):
        return None

    texto = str(valor).strip()
    return texto or None


def _primeiro_objeto(
    valor: object,
) -> Mapping[str, object] | None:
    """Obtém o primeiro objeto de um campo simples ou de uma lista."""

    if isinstance(valor, Mapping):
        return valor

    if isinstance(valor, list):
        for item in valor:
            if isinstance(item, Mapping):
                return item

    return None


def _primeira_url_publica(
    valor: object,
) -> str | None:
    """Obtém a primeira URL HTTP ou HTTPS absoluta."""

    candidatos = valor if isinstance(valor, list) else [valor]

    for candidato in candidatos:
        texto = _texto(candidato)

        if texto is None:
            continue

        endereco = urlsplit(texto)

        if endereco.scheme in {"http", "https"} and endereco.hostname:
            return texto

    return None


def _extrair_dominio(
    site: str | None,
) -> str | None:
    """Extrai o domínio normalizado de um endereço institucional."""

    if site is None:
        return None

    hostname = urlsplit(site).hostname

    if hostname is None:
        return None

    return hostname.casefold().removeprefix("www.")


def _extrair_pais(
    documento: Mapping[str, object],
) -> str:
    """Obtém o país publicado no endereço da vaga."""

    local = _primeiro_objeto(documento.get("jobLocation"))

    if local is None:
        return "BR"

    endereco = _primeiro_objeto(local.get("address"))

    if endereco is None:
        return "BR"

    pais_bruto = endereco.get("addressCountry")

    if isinstance(pais_bruto, Mapping):
        pais = _texto(pais_bruto.get("name")) or _texto(pais_bruto.get("value"))
    else:
        pais = _texto(pais_bruto)

    return (pais or "BR").upper()


def _extrair_cnpj(
    organizacao: Mapping[str, object] | None,
    *,
    pais: str,
) -> str | None:
    """Aceita somente registros que realmente parecem um CNPJ."""

    # Identificadores de empresas estrangeiras não são CNPJ.
    #
    # Por exemplo, os números 140718 e 140935 encontrados
    # nas vagas britânicas não podem ser usados como CNPJ.
    if organizacao is None:
        return None

    if pais not in {"BR", "BRASIL", "BRAZIL"}:
        return None

    for nome_campo in (
        "taxID",
        "vatID",
        "nationalRegister",
    ):
        texto = _texto(organizacao.get(nome_campo))

        if texto is None:
            continue

        # Remove pontos, barras, hífens e espaços.
        normalizado = re.sub(
            r"[^A-Za-z0-9]",
            "",
            texto,
        ).upper()

        if len(normalizado) == 14 and normalizado[-2:].isdigit():
            return normalizado

    return None


def _normalizar_nome_para_chave(
    nome: str,
) -> str:
    """Produz uma representação estável do nome da empresa."""

    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize(
            "NFKD",
            nome,
        )
        if not unicodedata.combining(caractere)
    )

    palavras = re.findall(
        r"[a-z0-9]+",
        sem_acentos.casefold(),
    )

    return "-".join(palavras)


def _gerar_id_empresa(
    *,
    cnpj: str | None,
    dominio: str | None,
    nome: str,
    anuncio: AnuncioVaga,
) -> UUID:
    """Gera um UUID repetível para evitar empresas duplicadas."""

    if cnpj is not None:
        chave = f"cnpj:{cnpj}"

    elif dominio is not None:
        chave = f"dominio:{dominio}"

    else:
        nome_normalizado = _normalizar_nome_para_chave(nome)

        chave = f"fonte:{anuncio.fonte.value}:nome:{nome_normalizado}"

    return uuid5(
        NAMESPACE_URL,
        f"https://observatorio-vagas.local/empresas/{chave}",
    )


DESCRICAO_UTIL_MINIMA = 80


def anuncio_e_inutil(anuncio: AnuncioVaga) -> bool:
    """Sem nome de empresa e sem descrição que sirva: nunca poderá ser publicado."""

    organizacao = _primeiro_objeto(anuncio.campos_estruturados.get("hiringOrganization"))
    nome_organizacao = _texto(organizacao.get("name")) if organizacao is not None else None
    if (
        anuncio.empresa_original
        or nome_organizacao
        or _nome_da_leitura(anuncio.campos_estruturados)
    ):
        return False

    return len((anuncio.descricao_original or "").strip()) < DESCRICAO_UTIL_MINIMA


def _nome_da_leitura(documento: dict) -> str | None:
    leitura = documento.get("_leitura")
    campos = leitura.get("campos") if isinstance(leitura, dict) else None
    empresa = campos.get("company") if isinstance(campos, dict) else None
    return _texto(empresa.get("name")) if isinstance(empresa, dict) else None


def extrair_empresa_do_anuncio(
    anuncio: AnuncioVaga,
) -> Empresa:
    """Cria uma empresa usando as evidências públicas do anúncio."""

    documento = anuncio.campos_estruturados

    organizacao = _primeiro_objeto(documento.get("hiringOrganization"))
    nome_organizacao = _texto(organizacao.get("name")) if organizacao is not None else None

    # A leitura completa decide o nome (ela recusa, por exemplo, o nome do agregador
    # "Jobbrazil" e cai em "confidential"). Quando ela discorda do documento, o site e o
    # logo do documento são do portal, não da empresa, e ficam de fora.
    nome_lido = _nome_da_leitura(documento)
    if nome_lido and nome_organizacao and nome_lido.casefold() != nome_organizacao.casefold():
        organizacao = None
    nome = nome_lido or anuncio.empresa_original or nome_organizacao

    if nome is None:
        raise ErroResolucaoEmpresa(
            "o anúncio não possui nome de empresa em empresa_original ou hiringOrganization.name"
        )

    if organizacao is not None:
        site = _primeira_url_publica(organizacao.get("sameAs"))

        logo_url = _primeira_url_publica(organizacao.get("logo"))

        descricao = _texto(organizacao.get("description"))
    else:
        site = None
        logo_url = None
        descricao = None

    dominio = _extrair_dominio(site)

    setor = _texto(documento.get("industry"))

    pais = _extrair_pais(documento)

    cnpj = _extrair_cnpj(
        organizacao,
        pais=pais,
    )

    empresa_id = _gerar_id_empresa(
        cnpj=cnpj,
        dominio=dominio,
        nome=nome,
        anuncio=anuncio,
    )

    return Empresa(
        id=empresa_id,
        razao_social=nome,
        nome_fantasia=nome,
        cnpj=cnpj,
        dominio=dominio,
        site=site,
        logo_url=logo_url,
        descricao=descricao,
        setor=setor,
        pais=pais,
    )


def _empresa_com_dados_novos(
    existente: Empresa,
    extraida: Empresa,
) -> Empresa:
    """Preenche lacunas sem apagar informações confirmadas."""

    dados = existente.model_dump(mode="python")

    alterado = False

    for campo in (
        "nome_fantasia",
        "cnpj",
        "dominio",
        "site",
        "logo_url",
        "descricao",
        "setor",
    ):
        valor_atual = getattr(
            existente,
            campo,
        )

        valor_novo = getattr(
            extraida,
            campo,
        )

        # Somente preenche um campo vazio.
        #
        # Isso impede que uma nova coleta sobrescreva
        # uma informação que já foi confirmada.
        if valor_atual is None and valor_novo is not None:
            dados[campo] = valor_novo
            alterado = True

    nomes_alternativos = list(existente.nomes_alternativos)

    chaves_existentes = {nome.casefold() for nome in nomes_alternativos}

    chaves_existentes.add(existente.razao_social.casefold())

    if existente.nome_fantasia:
        chaves_existentes.add(existente.nome_fantasia.casefold())

    if extraida.nome_exibicao.casefold() not in chaves_existentes:
        nomes_alternativos.append(extraida.nome_exibicao)

        dados["nomes_alternativos"] = tuple(nomes_alternativos)

        alterado = True

    if not alterado:
        return existente

    dados["atualizado_em"] = agora_utc()

    return Empresa.model_validate(dados)


def _buscar_empresas_candidatas(
    *,
    extraida: Empresa,
    repositorio: RepositorioEmpresas,
) -> tuple[Empresa, ...]:
    """Procura empresa por CNPJ, domínio e UUID determinístico."""

    candidatas: dict[UUID, Empresa] = {}

    if extraida.cnpj is not None:
        por_cnpj = repositorio.buscar_por_cnpj(extraida.cnpj)

        if por_cnpj is not None:
            candidatas[por_cnpj.id] = por_cnpj

    if extraida.dominio is not None:
        por_dominio = repositorio.buscar_por_dominio(extraida.dominio)

        if por_dominio is not None:
            candidatas[por_dominio.id] = por_dominio

    por_id = repositorio.buscar_por_id(extraida.id)

    if por_id is not None:
        candidatas[por_id.id] = por_id

    return tuple(candidatas.values())


def _associar_anuncio(
    anuncio: AnuncioVaga,
    *,
    empresa_id: UUID,
) -> AnuncioVaga:
    """Cria uma cópia validada com a empresa associada."""

    if anuncio.empresa_id == empresa_id:
        return anuncio

    dados = anuncio.model_dump(mode="python")

    dados["empresa_id"] = empresa_id

    return AnuncioVaga.model_validate(dados)


@dataclass(frozen=True, slots=True)
class FalhaResolucaoEmLote:
    """Anúncio que não pôde ser associado a uma empresa."""

    anuncio_id: UUID
    mensagem: str


@dataclass(frozen=True, slots=True)
class ResultadoResolucaoEmLote:
    """Resumo da resolução de empresas de vários anúncios."""

    # Anúncios já associados, na ordem recebida.
    anuncios: tuple[AnuncioVaga, ...]
    falhas: tuple[FalhaResolucaoEmLote, ...]
    empresas_criadas: int
    empresas_atualizadas: int
    anuncios_atualizados: int


@dataclass(slots=True)
class CacheEmpresas:
    """Empresas já resolvidas neste lote, para não consultar o banco de novo.

    Só é seguro quando um único processo resolve empresas por vez, como no
    processamento em lote, que resolve as empresas no processo principal.
    """

    # Chave de resolução -> UUID da empresa resolvida.
    por_chave: dict[tuple[object, ...], UUID]
    # Estado mais recente de cada empresa, como foi gravado.
    por_id: dict[UUID, Empresa]

    @classmethod
    def vazio(cls) -> CacheEmpresas:
        return cls(por_chave={}, por_id={})


def _chave_resolucao(
    anuncio: AnuncioVaga,
    extraida: Empresa,
) -> tuple[object, ...]:
    """Anúncios com a mesma chave chegam sempre à mesma empresa."""

    if anuncio.empresa_id is not None:
        return ("associada", anuncio.empresa_id)

    return ("evidencias", extraida.cnpj, extraida.dominio, extraida.id)


def resolver_e_associar_empresas_em_lote(
    anuncios: Sequence[AnuncioVaga],
    *,
    repositorio_empresas: RepositorioEmpresas,
    repositorio_anuncios: RepositorioAnuncios,
    cache: CacheEmpresas | None = None,
) -> ResultadoResolucaoEmLote:
    """Faz o mesmo que ``resolver_e_associar_empresa`` para vários anúncios.

    Os anúncios são agrupados pelas evidências da empresa. Cada grupo é
    resolvido uma vez, com as mesmas consultas da versão individual, e os
    dados de todos os anúncios do grupo são incorporados na ordem recebida,
    como aconteceria processando um por um. As associações são gravadas
    numa única operação em lote.
    """

    cache = cache if cache is not None else CacheEmpresas.vazio()

    falhas: list[FalhaResolucaoEmLote] = []
    grupos: dict[tuple[object, ...], list[tuple[AnuncioVaga, Empresa]]] = {}

    for anuncio in anuncios:
        try:
            extraida = extrair_empresa_do_anuncio(anuncio)
        except ErroResolucaoEmpresa as erro:
            falhas.append(FalhaResolucaoEmLote(anuncio.id, str(erro)))
            continue

        grupos.setdefault(_chave_resolucao(anuncio, extraida), []).append((anuncio, extraida))

    empresas_criadas = 0
    empresas_atualizadas = 0
    empresa_por_anuncio: dict[UUID, UUID] = {}

    for chave, membros in grupos.items():
        try:
            empresa, criada, base = _resolver_grupo(
                chave,
                membros,
                repositorio_empresas=repositorio_empresas,
                cache=cache,
            )
        except ErroResolucaoEmpresa as erro:
            falhas.extend(FalhaResolucaoEmLote(anuncio.id, str(erro)) for anuncio, _ in membros)
            continue

        # Incorpora as evidências de cada anúncio, na ordem recebida.
        for _, extraida in membros:
            empresa = _empresa_com_dados_novos(empresa, extraida)

        if criada or empresa != base:
            empresa = repositorio_empresas.salvar(empresa)
            empresas_criadas += int(criada)
            empresas_atualizadas += int(not criada)

        cache.por_chave[chave] = empresa.id
        cache.por_id[empresa.id] = empresa

        for anuncio, _ in membros:
            empresa_por_anuncio[anuncio.id] = empresa.id

    associados: list[AnuncioVaga] = []
    alterados: list[AnuncioVaga] = []

    for anuncio in anuncios:
        empresa_id = empresa_por_anuncio.get(anuncio.id)

        if empresa_id is None:
            continue

        associado = _associar_anuncio(anuncio, empresa_id=empresa_id)
        associados.append(associado)

        if associado != anuncio:
            alterados.append(associado)

    if alterados:
        repositorio_anuncios.salvar_lote(alterados)

    return ResultadoResolucaoEmLote(
        anuncios=tuple(associados),
        falhas=tuple(falhas),
        empresas_criadas=empresas_criadas,
        empresas_atualizadas=empresas_atualizadas,
        anuncios_atualizados=len(alterados),
    )


def _resolver_grupo(
    chave: tuple[object, ...],
    membros: Sequence[tuple[AnuncioVaga, Empresa]],
    *,
    repositorio_empresas: RepositorioEmpresas,
    cache: CacheEmpresas,
) -> tuple[Empresa, bool, Empresa | None]:
    """Encontra ou cria a empresa do grupo.

    Devolve a empresa inicial, se ela é nova, e o estado de referência
    usado para saber se houve alteração.
    """

    empresa_id_cache = cache.por_chave.get(chave)

    if empresa_id_cache is not None:
        existente = cache.por_id[empresa_id_cache]
        return existente, False, existente

    primeiro_anuncio, primeira_extraida = membros[0]

    if primeiro_anuncio.empresa_id is not None:
        existente = cache.por_id.get(primeiro_anuncio.empresa_id) or (
            repositorio_empresas.buscar_por_id(primeiro_anuncio.empresa_id)
        )

        if existente is None:
            raise ErroResolucaoEmpresa(
                "o anúncio aponta para uma empresa que não existe no repositório"
            )

        return existente, False, existente

    candidatas = _buscar_empresas_candidatas(
        extraida=primeira_extraida,
        repositorio=repositorio_empresas,
    )

    if len(candidatas) > 1:
        raise ConflitoResolucaoEmpresa(
            "CNPJ, domínio e identificador apontam para empresas diferentes"
        )

    if candidatas:
        existente = cache.por_id.get(candidatas[0].id, candidatas[0])
        return existente, False, existente

    return primeira_extraida, True, None


def resolver_e_associar_empresa(
    anuncio: AnuncioVaga,
    *,
    repositorio_empresas: RepositorioEmpresas,
    repositorio_anuncios: RepositorioAnuncios,
) -> ResultadoResolucaoEmpresa:
    """Resolve, salva e associa a empresa de maneira idempotente."""

    extraida = extrair_empresa_do_anuncio(anuncio)

    empresa_criada = False
    empresa_atualizada = False

    # Caso o anúncio já tenha empresa_id, tentamos
    # utilizar exatamente aquela empresa.
    if anuncio.empresa_id is not None:
        existente = repositorio_empresas.buscar_por_id(anuncio.empresa_id)

        if existente is None:
            raise ErroResolucaoEmpresa(
                "o anúncio aponta para uma empresa que não existe no repositório"
            )

        empresa = _empresa_com_dados_novos(
            existente,
            extraida,
        )

        empresa_atualizada = empresa != existente

        if empresa_atualizada:
            empresa = repositorio_empresas.salvar(empresa)

    else:
        candidatas = _buscar_empresas_candidatas(
            extraida=extraida,
            repositorio=repositorio_empresas,
        )

        # Se sinais diferentes encontrarem empresas diferentes,
        # não devemos escolher uma delas automaticamente.
        if len(candidatas) > 1:
            raise ConflitoResolucaoEmpresa(
                "CNPJ, domínio e identificador apontam para empresas diferentes"
            )

        if candidatas:
            existente = candidatas[0]

            empresa = _empresa_com_dados_novos(
                existente,
                extraida,
            )

            empresa_atualizada = empresa != existente

            if empresa_atualizada:
                empresa = repositorio_empresas.salvar(empresa)

        else:
            empresa = repositorio_empresas.salvar(extraida)

            empresa_criada = True

    anuncio_associado = _associar_anuncio(
        anuncio,
        empresa_id=empresa.id,
    )

    anuncio_atualizado = anuncio_associado != anuncio

    if anuncio_atualizado:
        anuncio_associado = repositorio_anuncios.salvar(anuncio_associado)

    return ResultadoResolucaoEmpresa(
        empresa=empresa,
        anuncio=anuncio_associado,
        empresa_criada=empresa_criada,
        empresa_atualizada=empresa_atualizada,
        anuncio_atualizado=anuncio_atualizado,
    )
