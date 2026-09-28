"""Contratos utilizados entre o crawler e o restante do sistema."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit

from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta


@dataclass(frozen=True, slots=True)
class RespostaBruta:
    """Resposta HTTP preservada antes da extração dos campos."""

    # Fonte responsável pela requisição.
    fonte: Fonte

    # Endereço inicialmente solicitado pelo crawler.
    url_solicitada: str

    # Endereço final depois de possíveis redirecionamentos.
    url_final: str

    # Código devolvido pelo servidor.
    #
    # Exemplos:
    # 200 = sucesso;
    # 404 = página não encontrada;
    # 429 = excesso de requisições;
    # 500 = erro do servidor.
    status_http: int

    # Conteúdo original devolvido pelo servidor.
    #
    # Usamos bytes para não alterar o HTML ou JSON antes
    # de armazená-lo.
    corpo: bytes

    # Identificador do alvo existente no catálogo de fontes.
    #
    # Ele permite relacionar a resposta ao cadastro que
    # originou a requisição.
    alvo_id: str | None = None

    # Nome da empresa no momento da coleta.
    empresa_nome: str | None = None

    # Número da página dentro da coleta atual.
    #
    # Exemplos:
    # 1 = página inicial;
    # 2 = primeira página de vaga;
    # 3 = segunda página de vaga.
    numero_pagina: int = 1

    # Papel da página dentro da coleta.
    #
    # Pode ser:
    # - inicial;
    # - detalhe_vaga;
    # - avulsa.
    tipo_pagina: TipoPaginaColeta = TipoPaginaColeta.AVULSA

    # Tipo de conteúdo informado pelo servidor.
    #
    # Exemplos:
    # text/html;
    # application/json.
    tipo_conteudo: str | None = None

    # Codificação informada ou detectada.
    #
    # Exemplo: utf-8.
    codificacao: str | None = None

    # Cabeçalhos importantes da resposta HTTP.
    #
    # Uma tupla é utilizada para manter o objeto imutável.
    cabecalhos: tuple[tuple[str, str], ...] = ()

    # Momento em que a resposta foi recebida.
    coletado_em: datetime = field(default_factory=agora_utc)

    def __post_init__(self) -> None:
        """Valida se a resposta possui uma estrutura possível."""

        self._validar_url(
            self.url_solicitada,
            "url_solicitada",
        )

        self._validar_url(
            self.url_final,
            "url_final",
        )

        if self.status_http < 100 or self.status_http > 599:
            raise ValueError("status_http deve estar entre 100 e 599")

        if not isinstance(self.corpo, bytes):
            raise TypeError("corpo deve ser armazenado como bytes")

        # Contexto do catálogo é opcional para manter compatibilidade
        # com coletas avulsas, como o spider pagina_unica.
        for nome_campo, valor in (
            ("alvo_id", self.alvo_id),
            ("empresa_nome", self.empresa_nome),
        ):
            # None significa que a resposta veio de uma coleta avulsa.
            if valor is None:
                continue

            # Se estiver presente, o valor obrigatoriamente deve ser texto.
            if not isinstance(valor, str):
                raise TypeError(f"{nome_campo} deve ser texto")

            # Um texto contendo somente espaços não identifica nada.
            if not valor.strip():
                raise ValueError(f"{nome_campo} não pode ser vazio")

        # bool precisa ser rejeitado separadamente.
        #
        # Em Python, True também é considerado um número inteiro.
        # Sem esta verificação, True poderia virar a página número 1.
        if isinstance(self.numero_pagina, bool) or not isinstance(
            self.numero_pagina,
            int,
        ):
            raise TypeError("numero_pagina precisa ser um número inteiro")

        # Não existe página zero ou negativa.
        if self.numero_pagina < 1:
            raise ValueError("numero_pagina precisa ser pelo menos 1")

        # Exigimos o Enum para evitar valores escritos de formas diferentes.
        #
        # Por exemplo:
        # "Detalhe", "vaga", "detalhes" ou "pagina_vaga".
        if not isinstance(self.tipo_pagina, TipoPaginaColeta):
            raise TypeError("tipo_pagina precisa ser um TipoPaginaColeta")

        if self.coletado_em.tzinfo is None or self.coletado_em.utcoffset() is None:
            raise ValueError("coletado_em precisa possuir fuso horário")

        if self.tipo_conteudo is not None and not self.tipo_conteudo.strip():
            raise ValueError("tipo_conteudo não pode ser vazio")

        if self.codificacao is not None and not self.codificacao.strip():
            raise ValueError("codificacao não pode ser vazia")

        for nome, valor in self.cabecalhos:
            if not nome.strip():
                raise ValueError("nome de cabeçalho não pode ser vazio")

            if not isinstance(valor, str):
                raise TypeError("valor de cabeçalho deve ser texto")

    @staticmethod
    def _validar_url(
        valor: str,
        nome_campo: str,
    ) -> None:
        """Confirma que uma URL utiliza HTTP ou HTTPS."""

        endereco = urlsplit(valor)

        if endereco.scheme not in {"http", "https"}:
            raise ValueError(f"{nome_campo} deve utilizar HTTP ou HTTPS")

        if not endereco.netloc:
            raise ValueError(f"{nome_campo} precisa possuir um domínio")

    @property
    def hash_conteudo(self) -> str:
        """Calcula o SHA-256 do conteúdo original."""

        return sha256(self.corpo).hexdigest()

    @property
    def tamanho_bytes(self) -> int:
        """Retorna o tamanho exato do corpo recebido."""

        return len(self.corpo)

    @property
    def sucesso(self) -> bool:
        """Informa se o código HTTP representa sucesso."""

        return 200 <= self.status_http < 300

    def buscar_cabecalho(
        self,
        nome_procurado: str,
    ) -> str | None:
        """Procura um cabeçalho ignorando letras maiúsculas."""

        nome_normalizado = nome_procurado.casefold()

        for nome, valor in self.cabecalhos:
            if nome.casefold() == nome_normalizado:
                return valor

        return None
