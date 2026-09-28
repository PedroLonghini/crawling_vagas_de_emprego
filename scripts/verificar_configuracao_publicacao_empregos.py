"""Valida, sem rede nem banco, se o ambiente permite uma publicação-teste.

O comando não lê nem mostra o valor da chave. Ele existe para que a primeira
publicação não dependa de tentativa e erro: depois de preencher ``.env``,
execute-o antes da simulação e do único POST de teste.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from observatorio_vagas.config import Settings, get_settings
from observatorio_vagas.integrations.empregos import ClienteEmpregos
from observatorio_vagas.integrations.empregos.client import (
    ConfiguracaoClienteEmpregosInvalida,
)


def _resumir_url(configuracoes: Settings) -> str:
    """Exibe apenas protocolo e host, nunca parâmetros ou credenciais."""

    if configuracoes.empregos_api_base_url is None:
        return "não configurada"

    partes = urlsplit(str(configuracoes.empregos_api_base_url))

    if not partes.scheme or not partes.hostname:
        return "inválida"

    return f"{partes.scheme}://{partes.hostname}"


def _configurado(valor: object | None) -> str:
    """Converte presença de configuração em texto sem revelar conteúdo."""

    if valor is None:
        return "NÃO"

    if hasattr(valor, "get_secret_value"):
        return "SIM" if valor.get_secret_value() else "NÃO"

    return "SIM" if str(valor).strip() else "NÃO"


def verificar(configuracoes: Settings) -> int:
    """Imprime a prévia segura e retorna 0 apenas quando o POST é permitido."""

    print("# VERIFICAÇÃO DA PUBLICAÇÃO-TESTE NO EMPREGOS")
    print()
    print("Esta verificação não acessa MongoDB nem realiza chamada HTTP.")
    print(f"Ambiente: {configuracoes.environment}")
    print(f"Endpoint base: {_resumir_url(configuracoes)}")
    print(
        "Caminho de publicação configurado: "
        f"{_configurado(configuracoes.empregos_api_publication_path)}"
    )
    print(
        "Cabeçalho de autenticação configurado: "
        f"{_configurado(configuracoes.empregos_api_auth_header)}"
    )
    print(f"Chave da API configurada: {_configurado(configuracoes.empregos_api_key)}")
    print(
        "Kill switch de publicação: "
        f"{'HABILITADO' if configuracoes.empregos_publicacao_habilitada else 'DESABILITADO'}"
    )

    try:
        ClienteEmpregos(configuracoes).validar_configuracao_publicacao()
    except ConfiguracaoClienteEmpregosInvalida as erro:
        print()
        print(f"STATUS: AINDA NÃO PRONTO — {erro}")
        return 2

    print()
    print("STATUS: PRONTO PARA UMA PUBLICAÇÃO-TESTE DE UMA VAGA")
    print("Próximo comando: publicar_lote_empregos.py --maximo-envios 1")
    return 0


def main() -> None:
    """Carrega o ambiente local e encerra com um código apropriado."""

    raise SystemExit(verificar(get_settings()))


if __name__ == "__main__":
    main()
