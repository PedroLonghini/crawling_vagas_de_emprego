"""Coordena prontidão, elegibilidade e geração do payload do Empregos.

Este módulo concentra três decisões:

1. verifica os 24 campos esperados pela API;
2. aplica as regras finais de elegibilidade;
3. gera o payload somente quando tudo estiver permitido.

Nenhuma chamada HTTP é realizada aqui.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.elegibilidade import (
    BloqueioPublicacao,
    ResultadoElegibilidadePublicacao,
    avaliar_elegibilidade_publicacao,
)
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.politica_fonte import PoliticaFonte
from observatorio_vagas.domain.prontidao import (
    RelatorioProntidao,
    SituacaoCampoProntidao,
    avaliar_prontidao_empregos,
)
from observatorio_vagas.domain.recrutador import Recrutador
from observatorio_vagas.domain.vaga import VagaCanonica
from observatorio_vagas.integrations.empregos.payload import (
    gerar_payload_empregos,
)


class PublicacaoEmpregosBloqueada(RuntimeError):
    """Informa que alguma regra impediu a publicação."""

    def __init__(
        self,
        bloqueios: tuple[BloqueioPublicacao, ...],
    ) -> None:
        """Guarda todos os bloqueios e cria uma mensagem segura."""

        # Guardamos os objetos completos para que:
        #
        # - os logs possam mostrar os códigos;
        # - o dashboard possa apresentar os motivos;
        # - os testes consigam verificar cada bloqueio;
        # - o futuro cliente HTTP saiba por que não deve enviar.
        self.bloqueios = bloqueios

        mensagens = "; ".join(bloqueio.mensagem for bloqueio in bloqueios)

        super().__init__(f"publicação bloqueada: {mensagens}")


@dataclass(frozen=True, slots=True)
class ProvenienciaFontePublicacao:
    """Evidência de origem usada para preparar uma publicação."""

    nome_fonte: str
    dominio: str
    licenca_nome: str
    licenca_url: str
    atribuicao_obrigatoria: bool
    url_origem: str
    credito_aplicado: str | None

    def para_documento(self) -> dict[str, str | bool | None]:
        """Converte a evidência em um documento JSON explícito."""

        return {
            "nome_fonte": self.nome_fonte,
            "dominio": self.dominio,
            "licenca_nome": self.licenca_nome,
            "licenca_url": self.licenca_url,
            "atribuicao_obrigatoria": self.atribuicao_obrigatoria,
            "url_origem": self.url_origem,
            "credito_aplicado": self.credito_aplicado,
        }


@dataclass(
    frozen=True,
    slots=True,
)
class ResultadoPreparacaoEmpregos:
    """Resultado completo e seguro da preparação de uma vaga."""

    # Mostra a situação dos 24 campos.
    relatorio: RelatorioProntidao

    # Mostra os bloqueios de campos, política, validade e origem.
    elegibilidade: ResultadoElegibilidadePublicacao

    # Só existe quando todas as regras foram aprovadas.
    payload: dict[str, Any] | None

    # Texto de crédito incorporado à descrição quando a política exige
    # atribuição. Fica exposto para diagnóstico e auditoria.
    credito_fonte: str | None = None

    # Evidência da política e URL usadas nesta preparação.
    proveniencia_fonte: ProvenienciaFontePublicacao | None = None

    @property
    def pronto_para_envio(self) -> bool:
        """Informa se o futuro cliente HTTP poderá enviar a vaga."""

        # As três condições precisam ser verdadeiras:
        #
        # 1. o payload foi criado;
        # 2. os campos obrigatórios estão preenchidos;
        # 3. as regras de elegibilidade foram aprovadas.
        return (
            self.payload is not None
            and self.relatorio.pronto_para_envio
            and self.elegibilidade.elegivel
        )

    @property
    def campos_bloqueadores(self) -> tuple[str, ...]:
        """Lista somente os campos obrigatórios problemáticos."""

        return self.elegibilidade.campos_api_bloqueadores

    @property
    def motivos_bloqueio(
        self,
    ) -> tuple[BloqueioPublicacao, ...]:
        """Lista todos os motivos que impedem o envio."""

        return self.elegibilidade.bloqueios


def preparar_publicacao_empregos(
    *,
    empresa: Empresa,
    recrutador: Recrutador | None,
    anuncio: AnuncioVaga,
    vaga: VagaCanonica,
    # A política é obrigatória.
    #
    # Isso impede que outro script prepare uma publicação sem
    # verificar se a fonte permite republicação.
    politica_fonte: PoliticaFonte,
    company_id_empregos: str | None = None,
    tracking_pixel_url: str | None = None,
    situacoes_campos: Mapping[
        str,
        SituacaoCampoProntidao,
    ]
    | None = None,
    # Este campo permite que os testes escolham uma data fixa.
    #
    # Na execução normal ele permanece None e o horário atual é usado.
    momento_referencia: datetime | None = None,
    bloquear_se_incompleta: bool = False,
) -> ResultadoPreparacaoEmpregos:
    """Avalia todos os campos e regras antes de liberar o payload."""

    # Primeira etapa: avaliar os 24 campos da API.
    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        company_id_empregos=company_id_empregos,
        tracking_pixel_url=tracking_pixel_url,
        situacoes_campos=situacoes_campos,
    )

    # Segunda etapa: avaliar regras de publicação.
    #
    # Aqui verificamos:
    #
    # - política da fonte;
    # - domínio autorizado;
    # - status do anúncio;
    # - expiração;
    # - uso indevido do Empregos como fonte;
    # - campos obrigatórios.
    elegibilidade = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica_fonte,
        relatorio_prontidao=relatorio,
        momento_referencia=momento_referencia,
    )
    credito_fonte = politica_fonte.montar_credito_publicacao(
        url_origem=str(anuncio.url),
    )
    proveniencia_fonte = ProvenienciaFontePublicacao(
        nome_fonte=politica_fonte.nome_atribuicao,
        dominio=politica_fonte.dominio,
        licenca_nome=politica_fonte.licenca_nome,
        licenca_url=politica_fonte.licenca_url,
        atribuicao_obrigatoria=politica_fonte.atribuicao_obrigatoria,
        url_origem=str(anuncio.url),
        credito_aplicado=credito_fonte,
    )

    # Qualquer bloqueio impede a criação do payload.
    if not elegibilidade.elegivel:
        if bloquear_se_incompleta:
            # Quando existem campos obrigatórios ausentes,
            # preservamos a exceção específica do gerador de payload.
            if not relatorio.pronto_para_envio:
                gerar_payload_empregos(relatorio)

            # Se os campos estão corretos, o problema pertence a uma
            # regra como política, domínio, status ou expiração.
            raise PublicacaoEmpregosBloqueada(elegibilidade.bloqueios)

        # No modo de diagnóstico não levantamos exceção.
        #
        # Devolvemos todos os detalhes para serem mostrados na tela.
        return ResultadoPreparacaoEmpregos(
            relatorio=relatorio,
            elegibilidade=elegibilidade,
            payload=None,
            credito_fonte=credito_fonte,
            proveniencia_fonte=proveniencia_fonte,
        )

    # Só chegamos aqui se os campos e as regras forem aprovados.
    payload = gerar_payload_empregos(relatorio)
    if credito_fonte is not None:
        descricao = payload.get("description")

        if not isinstance(descricao, str):
            raise ValueError("o payload aprovado não possui description em texto")

        # A descrição é a única parte pública do contrato da API adequada
        # para exibir a atribuição. Usamos um separador estável e só fazemos
        # isto depois de todas as validações de elegibilidade.
        payload["description"] = f"{descricao.rstrip()}\n\n{credito_fonte}"

    return ResultadoPreparacaoEmpregos(
        relatorio=relatorio,
        elegibilidade=elegibilidade,
        payload=payload,
        credito_fonte=credito_fonte,
        proveniencia_fonte=proveniencia_fonte,
    )
