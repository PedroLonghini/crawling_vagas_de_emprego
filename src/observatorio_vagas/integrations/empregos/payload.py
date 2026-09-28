"""Gera o corpo JSON aceito pela API de vagas do Empregos.

Este arquivo ainda não acessa a internet e não envia vagas.

Sua responsabilidade é somente:

1. receber o relatório de prontidão;
2. verificar se todos os 24 campos foram avaliados;
3. impedir a geração de uma vaga com erros;
4. montar os objetos company, location e salary;
5. remover campos opcionais que estão ausentes;
6. produzir um dicionário ou uma string JSON.
"""

from __future__ import annotations

import json
from typing import Any

from observatorio_vagas.domain.prontidao import (
    CAMPOS_API_EMPREGOS,
    CampoProntidao,
    RelatorioProntidao,
)


class PayloadEmpregosInvalido(ValueError):
    """Erro levantado quando o payload não pode ser criado com segurança."""

    def __init__(
        self,
        mensagem: str,
        *,
        campos: tuple[str, ...] = (),
    ) -> None:
        """Guarda a mensagem e os campos relacionados ao bloqueio."""

        # Guardamos os campos separadamente porque o dashboard
        # poderá exibi-los em uma tabela.
        self.campos = campos

        # Acrescenta os nomes dos campos na mensagem do erro.
        detalhe = ""

        if campos:
            detalhe = f" Campos: {', '.join(campos)}."

        super().__init__(f"{mensagem}{detalhe}")


def _valor_ausente(valor: object) -> bool:
    """Indica se um valor não deve ser incluído no JSON."""

    # Retorna True quando:
    #
    # 1. o valor é None;
    # 2. o valor é uma string vazia;
    # 3. o valor contém somente espaços.
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _validar_catalogo_relatorio(
    relatorio: RelatorioProntidao,
) -> None:
    """Confere se cada campo da API aparece uma única vez."""

    # Nomes dos 24 campos que esperamos encontrar.
    nomes_esperados = tuple(nome for nome, _obrigatorio in CAMPOS_API_EMPREGOS)

    # Nomes recebidos no relatório atual.
    nomes_recebidos = tuple(campo.campo for campo in relatorio.campos)

    # Detecta campos que aparecem mais de uma vez.
    nomes_duplicados = tuple(
        nome for nome in dict.fromkeys(nomes_recebidos) if nomes_recebidos.count(nome) > 1
    )

    if nomes_duplicados:
        raise PayloadEmpregosInvalido(
            "O relatório possui campos repetidos.",
            campos=nomes_duplicados,
        )

    conjunto_esperado = set(nomes_esperados)
    conjunto_recebido = set(nomes_recebidos)

    # Detecta campos da API que não foram avaliados.
    campos_ausentes = tuple(nome for nome in nomes_esperados if nome not in conjunto_recebido)

    if campos_ausentes:
        raise PayloadEmpregosInvalido(
            "O relatório não contém todos os campos da API.",
            campos=campos_ausentes,
        )

    # Também bloqueamos campos que não pertencem ao contrato conhecido.
    #
    # Isso evita enviar acidentalmente uma informação que a API
    # não está esperando.
    campos_desconhecidos = tuple(nome for nome in nomes_recebidos if nome not in conjunto_esperado)

    if campos_desconhecidos:
        raise PayloadEmpregosInvalido(
            "O relatório contém campos desconhecidos.",
            campos=campos_desconhecidos,
        )


def _validar_prontidao(
    relatorio: RelatorioProntidao,
) -> None:
    """Impede a geração quando existem bloqueios ou inconsistências."""

    # Esta validação protege contra um erro lógico:
    #
    # o campo estar marcado como preenchido, mas seu valor ser None
    # ou uma string vazia.
    campos_inconsistentes = tuple(
        campo.campo
        for campo in relatorio.campos
        if campo.preenchido and _valor_ausente(campo.valor)
    )

    if campos_inconsistentes:
        raise PayloadEmpregosInvalido(
            "Existem campos marcados como preenchidos, mas sem valor.",
            campos=campos_inconsistentes,
        )

    # Quando o relatório está pronto, não precisamos procurar bloqueios.
    if relatorio.pronto_para_envio:
        return

    # Juntamos:
    #
    # 1. campos obrigatórios ausentes;
    # 2. campos relacionados aos erros encontrados.
    #
    # dict.fromkeys remove nomes repetidos e mantém a ordem.
    campos_bloqueadores = tuple(
        dict.fromkeys(
            [campo.campo for campo in relatorio.campos_obrigatorios_ausentes]
            + [problema.campo for problema in relatorio.erros]
        )
    )

    raise PayloadEmpregosInvalido(
        "A vaga ainda não está pronta para formar o payload.",
        campos=campos_bloqueadores,
    )


def _inserir_valor(
    payload: dict[str, Any],
    *,
    caminho: str,
    valor: Any,
) -> None:
    """Converte um caminho pontuado em objetos aninhados.

    Por exemplo:

    company.name

    será transformado em:

    {
        "company": {
            "name": "Nome da empresa"
        }
    }
    """

    # Divide company.name em ["company", "name"].
    partes = caminho.split(".")

    # Começamos no objeto principal do payload.
    objeto_atual = payload

    # Percorremos todas as partes, menos a última.
    #
    # A última parte é o nome da propriedade que receberá o valor.
    for parte in partes[:-1]:
        objeto_existente = objeto_atual.get(parte)

        # Se o objeto ainda não existe, criamos um dicionário vazio.
        if objeto_existente is None:
            novo_objeto: dict[str, Any] = {}

            objeto_atual[parte] = novo_objeto
            objeto_atual = novo_objeto

            continue

        # Se existe alguma coisa nesse caminho, mas não é um
        # dicionário, o payload possui uma estrutura inválida.
        if not isinstance(objeto_existente, dict):
            raise PayloadEmpregosInvalido(
                "Não foi possível montar a estrutura aninhada do payload.",
                campos=(caminho,),
            )

        objeto_atual = objeto_existente

    # Insere o valor na última parte do caminho.
    objeto_atual[partes[-1]] = valor


def _incluir_campo(
    campo: CampoProntidao,
) -> bool:
    """Decide se um campo pode aparecer no payload."""

    # Campos opcionais ausentes não devem aparecer como null.
    #
    # Campos em revisão também não devem ser enviados.
    return campo.preenchido and not _valor_ausente(campo.valor)


def gerar_payload_empregos(
    relatorio: RelatorioProntidao,
) -> dict[str, Any]:
    """Transforma um relatório aprovado em um dicionário da API."""

    # Primeiro garantimos que o relatório representa os 24 campos.
    _validar_catalogo_relatorio(relatorio)

    # Depois verificamos se a vaga está realmente pronta.
    _validar_prontidao(relatorio)

    # Este dicionário será o corpo da futura requisição HTTP.
    payload: dict[str, Any] = {}

    for campo in relatorio.campos:
        # Campos opcionais ausentes são ignorados.
        if not _incluir_campo(campo):
            continue

        # Monta automaticamente objetos como:
        #
        # company
        # location
        # salary
        _inserir_valor(
            payload,
            caminho=campo.campo,
            valor=campo.valor,
        )

    return payload


def gerar_json_empregos(
    relatorio: RelatorioProntidao,
    *,
    indentacao: int | None = 2,
) -> str:
    """Gera uma string JSON legível e compatível com UTF-8."""

    payload = gerar_payload_empregos(relatorio)

    try:
        return json.dumps(
            payload,
            # Mantém caracteres como ç, ã e é legíveis.
            ensure_ascii=False,
            # Duas posições deixam o JSON fácil de apresentar.
            indent=indentacao,
        )
    except (TypeError, ValueError) as erro:
        # Transforma o erro técnico do json.dumps em um erro
        # específico do nosso projeto.
        raise PayloadEmpregosInvalido(
            "O payload contém um valor que não pode ser convertido para JSON."
        ) from erro
