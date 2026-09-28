"""Enriquece empresa a partir de uma página oficial revisada pela operação.

O comando nunca procura empresas na internet nem segue links de agregadores.
Ele recebe uma URL que a operação confirmou como pertencente à empresa, baixa
uma única página, preserva o HTML bruto com SHA-256 e só escreve no MongoDB
quando ``--confirmar`` é informado.
"""

from __future__ import annotations

import argparse
import ipaddress
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import requests
from parsel import Selector

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.raw_storage import ArmazenamentoBrutoLocal
from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.empresa import Empresa, EvidenciaCadastralEmpresa
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta
from observatorio_vagas.extraction.cnpj_documento import (
    CnpjDocumento,
    extrair_cnpjs_texto,
)
from observatorio_vagas.extraction.metadados_empresa import (
    MetadadosEmpresaHtml,
    extrair_metadados_site_institucional_html,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    ErroRepositorioEmpresasMongoDB,
    RepositorioAnunciosMongoDB,
    RepositorioEmpresasMongoDB,
)

_TAMANHO_MAXIMO_BYTES = 5 * 1024 * 1024
_USER_AGENT = "ObservatorioVagas/0.1.0 (+enriquecimento-institucional)"


class ErroEnriquecimentoEmpresa(ValueError):
    """Interrompe uma tentativa insegura ou sem dados institucionais."""


def criar_parser() -> argparse.ArgumentParser:
    """Define a interface segura do comando."""

    parser = argparse.ArgumentParser(
        description=(
            "Extrai descrição institucional de uma URL oficial já revisada. "
            "Use --confirmar para salvar HTML e empresa."
        )
    )
    identificador = parser.add_mutually_exclusive_group(required=True)
    identificador.add_argument("--empresa-id", help="UUID da empresa no MongoDB")
    identificador.add_argument(
        "--anuncio-id",
        help="UUID de um anúncio já associado à empresa no MongoDB",
    )
    parser.add_argument(
        "--url-oficial",
        required=True,
        help="URL HTTP(S) do site oficial da própria empresa",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="autoriza salvar a evidência bruta e atualizar a empresa",
    )
    return parser


def converter_uuid(valor: str, *, nome: str) -> UUID:
    """Converte o argumento sem expor traceback técnico."""

    try:
        return UUID(valor)
    except ValueError as erro:
        raise ErroEnriquecimentoEmpresa(f"{nome} não é um UUID válido: {valor}") from erro


def _host_publico(url: str) -> str:
    """Aceita HTTP(S), rejeitando URLs locais, credenciais e IPs privados."""

    partes = urlsplit(url)
    host = partes.hostname

    if partes.scheme not in {"http", "https"} or host is None:
        raise ErroEnriquecimentoEmpresa("url-oficial precisa ser uma URL HTTP(S) absoluta")

    if partes.username is not None or partes.password is not None:
        raise ErroEnriquecimentoEmpresa("url-oficial não pode possuir usuário ou senha")

    normalizado = host.casefold().removeprefix("www.")

    if normalizado in {"localhost", "localhost.localdomain"}:
        raise ErroEnriquecimentoEmpresa("url-oficial não pode apontar para localhost")

    try:
        endereco_ip = ipaddress.ip_address(normalizado)
    except ValueError:
        return normalizado

    if not endereco_ip.is_global:
        raise ErroEnriquecimentoEmpresa("url-oficial não pode apontar para um endereço privado")

    return normalizado


def _dominios_compativeis(dominio_empresa: str, dominio_url: str) -> bool:
    """Aceita www e subdomínios do domínio já confirmado da empresa."""

    return dominio_url == dominio_empresa or dominio_url.endswith(f".{dominio_empresa}")


def baixar_pagina_oficial(url: str, *, timeout: float) -> tuple[bytes, str, str | None]:
    """Baixa uma página pequena e HTML, mantendo a cadeia de redirecionamentos segura."""

    _host_publico(url)

    try:
        with requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        ) as resposta:
            for redirecionamento in (*resposta.history, resposta):
                _host_publico(redirecionamento.url)

            resposta.raise_for_status()
            tipo_conteudo = resposta.headers.get("Content-Type")

            if tipo_conteudo is not None and not any(
                tipo in tipo_conteudo.casefold() for tipo in ("text/html", "application/xhtml+xml")
            ):
                raise ErroEnriquecimentoEmpresa(
                    "a URL oficial não devolveu uma página HTML institucional"
                )

            partes: list[bytes] = []
            tamanho = 0

            for parte in resposta.iter_content(chunk_size=64 * 1024):
                tamanho += len(parte)

                if tamanho > _TAMANHO_MAXIMO_BYTES:
                    raise ErroEnriquecimentoEmpresa(
                        "a página oficial excede o limite seguro de 5 MB"
                    )

                partes.append(parte)

            return b"".join(partes), resposta.url, tipo_conteudo

    except requests.RequestException as erro:
        raise ErroEnriquecimentoEmpresa(
            f"não foi possível obter a página oficial ({type(erro).__name__})"
        ) from erro


def extrair_cnpj_unico_html(corpo: bytes) -> CnpjDocumento | None:
    """Aceita um CNPJ somente quando a página oficial possui um único valor válido."""

    texto = " ".join(
        Selector(text=corpo.decode("utf-8", errors="replace")).xpath("//text()").getall()
    )
    encontrados = extrair_cnpjs_texto(texto)

    if len(encontrados) == 1:
        return encontrados[0]

    return None


def preparar_empresa_atualizada(
    *,
    empresa: Empresa,
    url_final: str,
    metadados: MetadadosEmpresaHtml,
    corpo: bytes,
    cnpj_encontrado: CnpjDocumento | None,
) -> tuple[Empresa, tuple[EvidenciaCadastralEmpresa, ...], bool]:
    """Preenche somente lacunas e prepara uma evidência auditável."""

    if metadados.descricao is None:
        raise ErroEnriquecimentoEmpresa(
            "não foi encontrada uma descrição institucional com pelo menos 40 caracteres"
        )

    dominio_url = _host_publico(url_final)

    if empresa.dominio is not None and not _dominios_compativeis(empresa.dominio, dominio_url):
        raise ErroEnriquecimentoEmpresa(
            "o domínio da URL não corresponde ao domínio já associado à empresa"
        )

    hash_conteudo = sha256(corpo).hexdigest()
    referencia_bruta = f"corpos/{hash_conteudo[:2]}/{hash_conteudo}.bin"
    trecho = metadados.descricao[:500]
    evidencia_descricao = EvidenciaCadastralEmpresa(
        campo="descricao_institucional",
        valor_extraido=metadados.descricao,
        url_fonte=url_final,
        referencia_bruta=referencia_bruta,
        hash_conteudo=hash_conteudo,
        trecho_evidencia=trecho,
        coletado_em=agora_utc(),
    )
    evidencias_novas = [evidencia_descricao]

    if cnpj_encontrado is not None:
        evidencias_novas.append(
            EvidenciaCadastralEmpresa(
                campo="cnpj",
                valor_extraido=cnpj_encontrado.cnpj,
                url_fonte=url_final,
                referencia_bruta=referencia_bruta,
                hash_conteudo=hash_conteudo,
                pagina=cnpj_encontrado.pagina,
                trecho_evidencia=cnpj_encontrado.trecho_evidencia,
                coletado_em=agora_utc(),
            )
        )
    dados = empresa.model_dump(mode="python")
    alterado = False

    if empresa.descricao is None:
        dados["descricao"] = metadados.descricao
        alterado = True

    if empresa.site is None:
        dados["site"] = metadados.site or url_final
        alterado = True

    if empresa.dominio is None:
        dados["dominio"] = dominio_url
        alterado = True

    if cnpj_encontrado is not None:
        if empresa.cnpj is not None and empresa.cnpj != cnpj_encontrado.cnpj:
            raise ErroEnriquecimentoEmpresa(
                "a página oficial possui um CNPJ diferente do CNPJ já associado à empresa"
            )

        if empresa.cnpj is None:
            dados["cnpj"] = cnpj_encontrado.cnpj
            alterado = True

    if empresa.logo_url is None and metadados.logo_url is not None:
        dados["logo_url"] = metadados.logo_url
        alterado = True

    evidencias_existentes = empresa.evidencias_cadastrais
    evidencias_a_adicionar = tuple(
        evidencia
        for evidencia in evidencias_novas
        if not any(
            existente.campo == evidencia.campo
            and existente.hash_conteudo == evidencia.hash_conteudo
            and existente.valor_extraido == evidencia.valor_extraido
            for existente in evidencias_existentes
        )
    )

    if evidencias_a_adicionar:
        dados["evidencias_cadastrais"] = (*evidencias_existentes, *evidencias_a_adicionar)
        alterado = True

    if not alterado:
        return empresa, tuple(evidencias_novas), False

    dados["atualizado_em"] = agora_utc()
    return Empresa.model_validate(dados), tuple(evidencias_novas), True


def mostrar_previa(
    *,
    empresa: Empresa,
    atualizada: Empresa,
    url_final: str,
    metadados: MetadadosEmpresaHtml,
    evidencias: tuple[EvidenciaCadastralEmpresa, ...],
    confirmar: bool,
    alterado: bool,
) -> None:
    """Explica o que será gravado antes de qualquer escrita."""

    print("\n# ENRIQUECIMENTO INSTITUCIONAL DA EMPRESA\n")
    print(f"Modo: {'GRAVAÇÃO CONFIRMADA' if confirmar else 'SOMENTE PRÉVIA'}")
    print(f"Empresa: {empresa.nome_exibicao}")
    print(f"Empresa ID: {empresa.id}")
    print(f"URL oficial final: {url_final}")
    print(f"Evidências de extração: {', '.join(metadados.evidencias)}")
    print(f"SHA-256 planejado: {evidencias[0].hash_conteudo}")
    print(f"Descrição atual: {'informada' if empresa.descricao else 'ausente'}")
    print(f"Descrição depois: {'informada' if atualizada.descricao else 'ausente'}")
    print(f"CNPJ atual: {empresa.cnpj or 'ausente'}")
    print(f"CNPJ depois: {atualizada.cnpj or 'não encontrado na página'}")
    print("\nTrecho encontrado:\n")
    print(evidencias[0].trecho_evidencia)

    if not alterado:
        print("\nA empresa já possui estes dados e esta evidência.")
    elif not confirmar:
        print("\nNenhum arquivo nem dado no MongoDB será alterado.")
        print("Revise a URL e execute novamente com --confirmar.")


def salvar_evidencia_bruta(
    *,
    diretorio_bruto: Path,
    empresa: Empresa,
    url_solicitada: str,
    url_final: str,
    tipo_conteudo: str | None,
    corpo: bytes,
) -> None:
    """Guarda exatamente o HTML que comprova o enriquecimento."""

    resposta = RespostaBruta(
        fonte=Fonte.PAGINA_CARREIRAS,
        alvo_id=f"empresa_site_{empresa.id}",
        empresa_nome=empresa.nome_exibicao,
        url_solicitada=url_solicitada,
        url_final=url_final,
        status_http=200,
        corpo=corpo,
        numero_pagina=1,
        tipo_pagina=TipoPaginaColeta.AVULSA,
        tipo_conteudo=tipo_conteudo,
    )
    ArmazenamentoBrutoLocal(diretorio_bruto).salvar(resposta)


def executar(argumentos: list[str] | None = None) -> int:
    """Executa prévia ou gravação confirmada do enriquecimento."""

    opcoes = criar_parser().parse_args(argumentos)

    try:
        configuracoes = get_settings()

        with ConexaoMongoDB(configuracoes) as conexao:
            if opcoes.empresa_id is not None:
                empresa_id = converter_uuid(opcoes.empresa_id, nome="empresa-id")
            else:
                anuncio_id = converter_uuid(opcoes.anuncio_id, nome="anuncio-id")
                anuncio = RepositorioAnunciosMongoDB(conexao.banco).buscar_por_id(anuncio_id)

                if anuncio is None:
                    raise ErroEnriquecimentoEmpresa(f"anúncio não encontrado: {anuncio_id}")

                if anuncio.empresa_id is None:
                    raise ErroEnriquecimentoEmpresa(
                        "o anúncio ainda não está associado a uma empresa; "
                        "execute primeiro resolver_empresas_anuncios.py"
                    )

                empresa_id = anuncio.empresa_id

            repositorio = RepositorioEmpresasMongoDB(conexao.banco)
            empresa = repositorio.buscar_por_id(empresa_id)

            if empresa is None:
                raise ErroEnriquecimentoEmpresa(f"empresa não encontrada: {empresa_id}")

            corpo, url_final, tipo_conteudo = baixar_pagina_oficial(
                opcoes.url_oficial,
                timeout=configuracoes.request_timeout_seconds,
            )
            metadados = extrair_metadados_site_institucional_html(corpo, url_base=url_final)

            if metadados is None:
                raise ErroEnriquecimentoEmpresa(
                    "a página oficial não contém dados institucionais reconhecíveis"
                )

            cnpj_encontrado = extrair_cnpj_unico_html(corpo)
            atualizada, evidencias, alterado = preparar_empresa_atualizada(
                empresa=empresa,
                url_final=url_final,
                metadados=metadados,
                corpo=corpo,
                cnpj_encontrado=cnpj_encontrado,
            )
            mostrar_previa(
                empresa=empresa,
                atualizada=atualizada,
                url_final=url_final,
                metadados=metadados,
                evidencias=evidencias,
                confirmar=opcoes.confirmar,
                alterado=alterado,
            )

            if opcoes.confirmar and alterado:
                salvar_evidencia_bruta(
                    diretorio_bruto=configuracoes.raw_storage_path,
                    empresa=empresa,
                    url_solicitada=opcoes.url_oficial,
                    url_final=url_final,
                    tipo_conteudo=tipo_conteudo,
                    corpo=corpo,
                )
                repositorio.salvar(atualizada)
                print("\nEmpresa e evidência bruta salvas com sucesso.")

        return 0

    except (
        ErroConexaoMongoDB,
        ErroEnriquecimentoEmpresa,
        ErroRepositorioAnunciosMongoDB,
        ErroRepositorioEmpresasMongoDB,
        OSError,
        ValueError,
    ) as erro:
        print(f"ERRO: {erro}")
        return 1


if __name__ == "__main__":
    raise SystemExit(executar())
