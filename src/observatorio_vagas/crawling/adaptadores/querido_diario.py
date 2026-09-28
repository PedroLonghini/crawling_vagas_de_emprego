"""Adaptador de descoberta para a API pública do Querido Diário."""

import json
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.descoberta import LinkCandidatoVaga


@dataclass(frozen=True, slots=True)
class AdaptadorQueridoDiario:
    """Pagina a API sem seguir arquivos hospedados em outros domínios."""

    nome: str = "querido_diario_api"

    def descobrir(
        self,
        resposta: Response,
    ) -> tuple[LinkCandidatoVaga, ...]:
        """Cria páginas adicionais de ``/gazettes`` no mesmo domínio."""

        if not isinstance(resposta, Response):
            raise TypeError("resposta precisa ser uma Response do Scrapy")

        if not isinstance(resposta, TextResponse):
            return ()

        try:
            dados = json.loads(resposta.text)
        except (json.JSONDecodeError, TypeError):
            return ()

        if not isinstance(dados, dict):
            return ()

        # Não insistir em páginas vazias, mesmo se o índice informar um total
        # desatualizado. O spider registra esse motivo no resumo da navegação.
        diarios = dados.get("gazettes")
        if not isinstance(diarios, list) or not diarios:
            return ()

        total = dados.get("total_gazettes")

        if isinstance(total, bool) or not isinstance(total, int) or total < 1:
            return ()

        endereco = urlsplit(resposta.url)
        # Uma busca pode repetir ``territory_ids`` para consultar vários
        # municípios. Uma conversão para dicionário descartaria todos os
        # valores anteriores ao último, portanto mantemos a lista de pares.
        parametros = parse_qsl(
            endereco.query,
            keep_blank_values=True,
        )
        tamanho = _inteiro_positivo(_ultimo_parametro(parametros, "size")) or 10
        deslocamento_atual = _inteiro_nao_negativo(_ultimo_parametro(parametros, "offset")) or 0
        limite_paginas = _limite_paginas(resposta)

        if limite_paginas <= 1:
            return ()

        candidatos: list[LinkCandidatoVaga] = []
        proximo_deslocamento = deslocamento_atual + tamanho

        # Uma página por vez: permite parar quando o servidor esgota resultados,
        # sem disparar antecipadamente dezenas de chamadas desnecessárias.
        if proximo_deslocamento < total:
            parametros_pagina = [(nome, valor) for nome, valor in parametros if nome != "offset"]
            parametros_pagina.append(
                ("offset", str(proximo_deslocamento)),
            )
            url = urlunsplit(
                (
                    endereco.scheme,
                    endereco.netloc,
                    endereco.path,
                    urlencode(parametros_pagina),
                    "",
                )
            )
            candidatos.append(
                LinkCandidatoVaga(
                    url=url,
                    texto=("Página adicional da busca pública do Querido Diário"),
                    evidencias=("paginacao_querido_diario",),
                )
            )

        # Os campos url/txt_url da resposta podem usar CDNs externas. Eles
        # não entram nesta lista e continuam protegidos pela trava de domínio.
        return tuple(candidatos)


def _ultimo_parametro(
    parametros: list[tuple[str, str]],
    nome_procurado: str,
) -> str | None:
    """Obtém o último valor sem eliminar parâmetros repetidos da URL."""

    return next(
        (valor for nome, valor in reversed(parametros) if nome == nome_procurado),
        None,
    )


def _inteiro_positivo(
    valor: str | None,
) -> int | None:
    """Lê um inteiro estritamente positivo de um parâmetro da URL."""

    if valor is None:
        return None

    try:
        resultado = int(valor)
    except ValueError:
        return None

    return resultado if resultado > 0 else None


def _inteiro_nao_negativo(
    valor: str | None,
) -> int | None:
    """Lê um deslocamento não negativo de um parâmetro da URL."""

    if valor is None:
        return None

    try:
        resultado = int(valor)
    except ValueError:
        return None

    return resultado if resultado >= 0 else None


def _limite_paginas(
    resposta: Response,
) -> int:
    """Obtém o limite validado que nasceu no catálogo."""

    try:
        limite = resposta.meta.get("observatorio_limite_paginas")
    except AttributeError:
        return 1

    if isinstance(limite, bool) or not isinstance(limite, int):
        return 1

    return max(limite, 1)
