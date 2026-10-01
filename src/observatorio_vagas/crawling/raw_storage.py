"""Armazenamento local das respostas brutas coletadas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from observatorio_vagas.crawling.contracts import RespostaBruta


class ErroArmazenamentoBruto(RuntimeError):
    """Erro seguro durante o armazenamento de uma resposta bruta."""


@dataclass(frozen=True, slots=True)
class ResultadoArmazenamentoBruto:
    """Resultado produzido depois de armazenar uma resposta."""

    # Referência relativa que poderá ser salva no AnuncioVaga.
    referencia: str

    # Hash do corpo original.
    hash_conteudo: str

    # Caminhos completos utilizados durante o desenvolvimento.
    caminho_corpo: Path
    caminho_metadados: Path

    # True significa que esse conteúdo ainda não existia.
    corpo_novo: bool


class ArmazenamentoBrutoLocal:
    """Armazena corpos e metadados no sistema de arquivos."""

    def __init__(
        self,
        diretorio_base: Path,
        caminho_caderno: Path | None = None,
    ) -> None:
        """Recebe o diretório onde os arquivos serão guardados.

        ``caminho_caderno`` é opcional. Quando informado, cada resposta salva
        também ganha uma linha num único arquivo JSONL. O lote lê esse caderno
        em vez de abrir um JSON de metadados por resposta.
        """

        # Guardamos o caminho absoluto para evitar ambiguidades.
        self._diretorio_base = diretorio_base.resolve()
        # O diretório-base pode ser um ``tmp_path`` recém-criado. Garantir a
        # raiz aqui evita que a primeira escrita dependa da ordem entre corpo
        # e metadados ou do comportamento do sistema de arquivos.
        self._diretorio_base.mkdir(parents=True, exist_ok=True)
        self._caminho_caderno = caminho_caderno
        self._caderno: BinaryIO | None = None

    def fechar(self) -> None:
        """Fecha o caderno, se houver um aberto."""

        if self._caderno is not None:
            self._caderno.close()
            self._caderno = None

    def _anotar_no_caderno(
        self,
        referencia_metadados: str,
        metadados: dict[str, object],
    ) -> None:
        """Acrescenta a resposta ao caderno, uma linha JSON por resposta."""

        if self._caminho_caderno is None:
            return

        if self._caderno is None:
            self._caminho_caderno.parent.mkdir(parents=True, exist_ok=True)
            self._caderno = self._caminho_caderno.open("ab")

        linha = json.dumps(
            {"referencia": referencia_metadados, "metadados": metadados},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        # Uma linha completa por escrita: se o processo cair, só a última
        # linha pode ficar cortada, e a leitura a descarta.
        self._caderno.write(linha + b"\n")
        self._caderno.flush()

    def salvar(
        self,
        resposta: RespostaBruta,
    ) -> ResultadoArmazenamentoBruto:
        """Salva o corpo e os metadados de uma resposta."""

        # O SHA-256 identifica o conteúdo recebido.
        hash_conteudo = resposta.hash_conteudo

        # Os dois primeiros caracteres distribuem os arquivos
        # entre várias pastas.
        #
        # Isso evita colocar milhões de arquivos no mesmo diretório.
        prefixo_hash = hash_conteudo[:2]

        # Um corpo idêntico sempre aponta para o mesmo caminho.
        caminho_corpo = self._diretorio_base / "corpos" / prefixo_hash / f"{hash_conteudo}.bin"

        # Todas as datas são organizadas em UTC.
        instante_utc = resposta.coletado_em.astimezone(UTC)
        # Este texto representa um evento específico de coleta.
        componentes_identidade = [
            resposta.fonte.value,
            # O tipo e o número impedem que duas páginas com contextos
            # diferentes compartilhem acidentalmente o mesmo evento.
            resposta.tipo_pagina.value,
            str(resposta.numero_pagina),
            resposta.url_solicitada,
            resposta.url_final,
            instante_utc.isoformat(),
            hash_conteudo,
        ]
        # Quando existe um alvo do catálogo, ele participa da identidade.
        #
        # Assim, duas empresas que apontem para a mesma URL não perdem
        # seus eventos individuais de coleta.
        if resposta.alvo_id is not None:
            componentes_identidade.insert(
                1,
                resposta.alvo_id,
            )

        identidade_resposta = "|".join(componentes_identidade)

        # Um segundo hash identifica os metadados da coleta.
        id_resposta = sha256(identidade_resposta.encode("utf-8")).hexdigest()

        caminho_metadados = (
            self._diretorio_base
            / "respostas"
            / resposta.fonte.value
            / f"{instante_utc.year:04d}"
            / f"{instante_utc.month:02d}"
            / f"{instante_utc.day:02d}"
            / f"{id_resposta}.json"
        )

        # O JSON guarda um caminho relativo.
        #
        # Assim, a pasta data/raw poderá ser movida para outro
        # computador sem invalidar as referências.
        referencia_corpo = caminho_corpo.relative_to(self._diretorio_base).as_posix()

        referencia_metadados = caminho_metadados.relative_to(self._diretorio_base).as_posix()

        metadados = {
            # A versão 3 acrescenta o contexto da página coletada.
            #
            # Os arquivos antigos não são alterados e continuam
            # identificados como versões 1 ou 2.
            "versao_schema": 3,
            # Identificação da empresa e do alvo que originaram a coleta.
            #
            # Coletas avulsas antigas poderão guardar None.
            "alvo_id": resposta.alvo_id,
            "empresa_nome": resposta.empresa_nome,
            # Contexto da página dentro da coleta.
            "numero_pagina": resposta.numero_pagina,
            "tipo_pagina": resposta.tipo_pagina.value,
            "fonte": resposta.fonte.value,
            "url_solicitada": resposta.url_solicitada,
            "url_final": resposta.url_final,
            "status_http": resposta.status_http,
            "tipo_conteudo": resposta.tipo_conteudo,
            "codificacao": resposta.codificacao,
            "cabecalhos": list(resposta.cabecalhos),
            "coletado_em": instante_utc.isoformat(),
            "hash_conteudo": hash_conteudo,
            "tamanho_bytes": resposta.tamanho_bytes,
            "caminho_corpo": referencia_corpo,
        }
        # Convertemos os metadados para bytes UTF-8.
        conteudo_metadados = json.dumps(
            metadados,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        try:
            # O corpo é criado somente se ainda não existir.
            corpo_novo = self._escrever_se_ausente(
                caminho_corpo,
                resposta.corpo,
            )

            # Cada evento de coleta recebe seu JSON de metadados.
            self._escrever_se_ausente(
                caminho_metadados,
                conteudo_metadados,
            )

            self._anotar_no_caderno(referencia_metadados, metadados)

        except OSError as erro_original:
            raise ErroArmazenamentoBruto(
                "não foi possível armazenar a resposta bruta"
            ) from erro_original

        return ResultadoArmazenamentoBruto(
            referencia=referencia_metadados,
            hash_conteudo=hash_conteudo,
            caminho_corpo=caminho_corpo,
            caminho_metadados=caminho_metadados,
            corpo_novo=corpo_novo,
        )

    @staticmethod
    def _escrever_se_ausente(
        caminho: Path,
        conteudo: bytes,
    ) -> bool:
        """Cria um arquivo sem substituir um arquivo existente."""

        # Cria todas as pastas necessárias.
        caminho.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            # O modo "xb" cria o arquivo exclusivamente.
            #
            # Se ele já existir, o Python gera FileExistsError.
            with caminho.open("xb") as arquivo:
                arquivo.write(conteudo)

            return True

        except FileExistsError:
            # Um arquivo existente não é sobrescrito.
            return False

        except OSError:
            # Se a escrita falhar depois da criação,
            # removemos somente o arquivo incompleto.
            caminho.unlink(missing_ok=True)
            raise
