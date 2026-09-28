"""Catálogo dos endereços que poderão ser coletados pelo crawler."""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

from observatorio_vagas.domain.enums import Fonte, StatusPoliticaFonte
from observatorio_vagas.domain.politica_fonte import (
    PoliticaFonte,
    encontrar_restricao_dominio,
)

# Formato recomendado: uma única URL por linha. O sistema infere os demais
# campos necessários à coleta; o formato detalhado abaixo continua aceito só
# para compatibilidade com catálogos históricos e testes.
COLUNA_URL_SIMPLES = "url"
# Fontes de alto volume, como a Randstad, podem publicar centenas de páginas.
# O limite técnico evita apenas uma paginação infinita; não corta mais a coleta
# normal depois de dez páginas.
LIMITE_PAGINAS_PADRAO = 10_000
ARQUIVO_AUTORIZACOES_SIMPLES = "fontes_autorizadas.csv"
REFERENCIA_AUTORIZACAO_SIMPLES = "Autorização escrita registrada pelo titular do projeto"

# Colunas que precisam existir no arquivo CSV.
COLUNAS_OBRIGATORIAS = frozenset(
    {
        "alvo_id",
        "empresa_nome",
        "fonte",
        "url_inicial",
        "ativa",
        "limite_paginas",
    }
)

# Catálogos antigos podem não possuir estas colunas. Nesse caso, a fonte
# continua carregando, mas nunca fica habilitada para republicação.
COLUNAS_LICENCA = frozenset(
    {
        "licenca_nome",
        "licenca_url",
        "atribuicao_obrigatoria",
        "republicacao_permitida",
    }
)

# Formas aceitas para representar valores booleanos no CSV.
VALORES_VERDADEIROS = frozenset(
    {
        "1",
        "true",
        "sim",
        "yes",
        "ativo",
    }
)

VALORES_FALSOS = frozenset(
    {
        "0",
        "false",
        "nao",
        "não",
        "no",
        "inativo",
    }
)


class ErroCatalogoFontes(ValueError):
    """Erro encontrado durante a leitura do catálogo de fontes."""


@dataclass(frozen=True, slots=True)
class FalhaLinhaCatalogo:
    """Linha inválida que foi isolada durante um carregamento tolerante."""

    numero_linha: int
    alvo_id: str
    mensagem: str


@dataclass(frozen=True, slots=True)
class ResultadoCarregamentoCatalogo:
    """Alvos válidos e linhas rejeitadas de um catálogo."""

    alvos: tuple[AlvoColeta, ...]
    falhas: tuple[FalhaLinhaCatalogo, ...]


@dataclass(frozen=True, slots=True)
class AlvoColeta:
    """Empresa e endereço inicial que o crawler poderá visitar."""

    # Identificador interno da configuração.
    alvo_id: str

    # Nome que aparecerá futuramente no dashboard.
    empresa_nome: str

    # Tipo de fonte conhecido pelo sistema.
    fonte: Fonte

    # Primeira página que o crawler deverá visitar.
    url_inicial: str

    # Permite desativar a coleta sem remover a configuração.
    ativa: bool

    # Proteção contra coleta ilimitada.
    limite_paginas: int

    # Regras que decidem se a fonte pode ser coletada e publicada.
    politica: PoliticaFonte

    def __post_init__(self) -> None:
        """Normaliza e valida os dados do alvo."""

        # Remove espaços adicionados acidentalmente no CSV.
        alvo_id = self.alvo_id.strip()
        empresa_nome = self.empresa_nome.strip()
        url_inicial = self.url_inicial.strip()

        if not alvo_id:
            raise ValueError("alvo_id não pode ser vazio")

        if not empresa_nome:
            raise ValueError("empresa_nome não pode ser vazio")

        # Divide a URL em protocolo, domínio, caminho e parâmetros.
        endereco = urlsplit(url_inicial)

        if endereco.scheme not in {"http", "https"} or not endereco.netloc:
            raise ValueError("url_inicial deve ser uma URL HTTP ou HTTPS completa")

        if not isinstance(self.fonte, Fonte):
            raise TypeError("fonte precisa ser um valor do enum Fonte")

        if not isinstance(self.politica, PoliticaFonte):
            raise TypeError("politica precisa ser uma PoliticaFonte")

        # A política precisa pertencer exatamente ao domínio da URL.
        # Isso impede usar a aprovação de uma empresa em outro site.
        dominio_url = (endereco.hostname or "").casefold()

        if self.politica.dominio != dominio_url:
            raise ValueError("o domínio da política não corresponde à URL inicial")

        if not isinstance(self.ativa, bool):
            raise TypeError("ativa precisa ser um valor booleano")

        # Em Python, bool também é considerado um tipo de int.
        # Por isso verificamos bool separadamente.
        if isinstance(self.limite_paginas, bool) or not isinstance(
            self.limite_paginas,
            int,
        ):
            raise TypeError("limite_paginas precisa ser um número inteiro")

        if self.limite_paginas < 1:
            raise ValueError("limite_paginas precisa ser pelo menos 1")

        # Como a classe é congelada, usamos object.__setattr__
        # somente durante a validação inicial.
        object.__setattr__(
            self,
            "alvo_id",
            alvo_id,
        )

        object.__setattr__(
            self,
            "empresa_nome",
            empresa_nome,
        )

        object.__setattr__(
            self,
            "url_inicial",
            url_inicial,
        )

    @property
    def dominio(self) -> str:
        """Retorna somente o domínio permitido para esse alvo."""

        return urlsplit(self.url_inicial).hostname or ""

    @property
    def habilitado_para_coleta(self) -> bool:
        """Combina o botão operacional com a autorização da fonte."""

        # A coleta somente acontece quando:
        # 1. o alvo está operacionalmente ativo;
        # 2. a política permite a coleta.
        return self.ativa and self.politica.permite_coleta

    @property
    def habilitado_para_publicacao(self) -> bool:
        """Informa se as vagas da fonte podem ser republicadas."""

        return self.politica.permite_publicacao


def _ler_booleano(
    valor: str,
) -> bool:
    """Converte o texto do CSV em verdadeiro ou falso."""

    normalizado = valor.strip().casefold()

    if normalizado in VALORES_VERDADEIROS:
        return True

    if normalizado in VALORES_FALSOS:
        return False

    raise ValueError("ativa deve usar true/false, sim/não ou 1/0")


def _ler_booleano_opcional(
    linha: dict[str, str | None],
    nome_campo: str,
) -> bool:
    """Lê um booleano opcional usando falso como padrão seguro."""

    texto = _ler_texto_da_linha(
        linha,
        nome_campo,
    )

    if not texto:
        return False

    try:
        return _ler_booleano(texto)

    except ValueError as erro:
        raise ValueError(f"{nome_campo} deve usar true/false, sim/não ou 1/0") from erro


def _ler_status_politica(valor: str) -> StatusPoliticaFonte:
    """Converte a coluna do CSV em um status seguro."""

    normalizado = valor.strip().casefold()

    # Catálogos antigos não possuem a coluna.
    # A ausência nunca pode liberar uma fonte automaticamente.
    if not normalizado:
        return StatusPoliticaFonte.PENDENTE

    try:
        return StatusPoliticaFonte(normalizado)

    except ValueError as erro:
        # Exibimos todas as opções válidas para facilitar
        # a correção do CSV no VS Code.
        opcoes = ", ".join(status.value for status in StatusPoliticaFonte)

        raise ValueError(f"status_politica inválido; use uma destas opções: {opcoes}") from erro


def _ler_texto_da_linha(
    linha: dict[str, str | None],
    nome_campo: str,
) -> str:
    """Lê um campo do CSV e remove espaços laterais."""

    valor = linha.get(nome_campo)

    return "" if valor is None else valor.strip()


def _normalizar_url_simples(valor: str) -> str:
    """Padroniza a URL sem exigir metadados extras no CSV simples."""

    endereco = urlsplit(valor.strip())

    if endereco.scheme.casefold() not in {"http", "https"} or not endereco.hostname:
        raise ValueError("url deve ser uma URL HTTP ou HTTPS completa")

    esquema = endereco.scheme.casefold()
    dominio = endereco.hostname.casefold()
    caminho = re.sub(r"/{2,}", "/", endereco.path or "/")
    porta = endereco.port
    porta_padrao = (esquema == "http" and porta == 80) or (esquema == "https" and porta == 443)
    autoridade = dominio if porta is None or porta_padrao else f"{dominio}:{porta}"

    return urlunsplit(
        SplitResult(esquema, autoridade, caminho, endereco.query, "")
    )


def _slug(texto: str) -> str:
    """Converte um texto em parte estável de identificador interno."""

    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"[^a-zA-Z0-9]+", "_", sem_acentos).strip("_").casefold() or "empresa"


def _identificador_empresa_simples(url: str) -> str:
    """Obtém um nome provisório do domínio ou do tenant da página de vagas."""

    endereco = urlsplit(url)
    dominio = endereco.hostname or "empresa"
    segmentos = [segmento for segmento in endereco.path.split("/") if segmento]
    partes_dominio = dominio.split(".")
    primeiro_dominio = partes_dominio[0]

    if primeiro_dominio == "www" and len(partes_dominio) > 2:
        partes_dominio = partes_dominio[1:]
        primeiro_dominio = partes_dominio[0]

    if primeiro_dominio in {"jobs", "job-boards"} and segmentos:
        return segmentos[0]

    if primeiro_dominio in {
        "www",
        "news",
        "careers",
        "vagas",
        "rh",
        "trabalheconosco",
    } and len(partes_dominio) > 1:
        return partes_dominio[1]

    return primeiro_dominio


def _carregar_urls_autorizadas(caminho_catalogo: Path) -> frozenset[str]:
    """Lê a lista opcional, também simples, das URLs liberadas para publicar.

    O catálogo diário continua tendo só a coluna ``url``. A separação evita
    que uma URL recém-adicionada vire publicável por acidente: ela só é
    promovida quando também constar na lista de autorizações registrada pelo
    titular do projeto.
    """

    caminho = caminho_catalogo.with_name(ARQUIVO_AUTORIZACOES_SIMPLES)

    if not caminho.is_file():
        return frozenset()

    try:
        with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
            leitor = csv.DictReader(arquivo)
            cabecalhos = {nome.strip() for nome in (leitor.fieldnames or []) if nome}

            if cabecalhos != {COLUNA_URL_SIMPLES}:
                raise ErroCatalogoFontes(
                    f"a lista de autorizações deve conter apenas a coluna {COLUNA_URL_SIMPLES}"
                )

            urls: set[str] = set()

            for numero_linha, linha in enumerate(leitor, start=2):
                valor = _ler_texto_da_linha(linha, COLUNA_URL_SIMPLES)

                if not valor:
                    continue

                try:
                    urls.add(_normalizar_url_simples(valor))
                except ValueError as erro:
                    raise ErroCatalogoFontes(
                        f"linha {numero_linha} da lista de autorizações: {erro}"
                    ) from erro

            return frozenset(urls)

    except ErroCatalogoFontes:
        raise
    except (OSError, csv.Error) as erro:
        raise ErroCatalogoFontes(
            f"não foi possível ler a lista de autorizações: {caminho}"
        ) from erro


def _criar_alvo_simples(
    url: str,
    *,
    urls_autorizadas: frozenset[str],
) -> AlvoColeta:
    """Cria uma fonte coletável a partir de apenas uma URL de carreira."""

    url_normalizada = _normalizar_url_simples(url)
    endereco = urlsplit(url_normalizada)
    dominio = endereco.hostname or ""
    restricao = encontrar_restricao_dominio(dominio)

    if dominio == "gupy.io" or dominio.endswith(".gupy.io"):
        raise ValueError("Gupy: a fonte não entra na política atual de páginas de carreira")

    if restricao is not None:
        raise ValueError(f"{restricao.nome}: {restricao.motivo}")

    identificador = _identificador_empresa_simples(url_normalizada)
    resumo = hashlib.sha256(url_normalizada.encode()).hexdigest()[:10]
    empresa_nome = " ".join(
        parte.capitalize() for parte in re.split(r"[-_]", identificador) if parte
    )
    if identificador == "preventwork":
        empresa_nome = "Prevent Work"
    autorizada = url_normalizada in urls_autorizadas

    politica = PoliticaFonte(
        dominio=dominio,
        status=(
            StatusPoliticaFonte.APROVADA
            if autorizada
            else StatusPoliticaFonte.SOMENTE_COLETA
        ),
        nome_atribuicao=empresa_nome,
        republicacao_permitida=autorizada,
        autorizacao_escrita=autorizada,
        referencia_autorizacao=(REFERENCIA_AUTORIZACAO_SIMPLES if autorizada else ""),
    )

    return AlvoColeta(
        alvo_id=f"{_slug(identificador)}_{resumo}",
        empresa_nome=empresa_nome or "Empresa não identificada",
        fonte=Fonte.PAGINA_CARREIRAS,
        url_inicial=url_normalizada,
        ativa=True,
        limite_paginas=LIMITE_PAGINAS_PADRAO,
        politica=politica,
    )


def _carregar_catalogo_csv(
    caminho: Path,
    *,
    tolerar_linhas_invalidas: bool,
) -> ResultadoCarregamentoCatalogo:
    """Carrega o CSV no modo estrito ou com isolamento por linha."""

    try:
        # utf-8-sig também aceita arquivos salvos pelo Excel
        # contendo a marca BOM no início.
        with caminho.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as arquivo:
            leitor = csv.DictReader(arquivo)

            # Descobre quais colunas realmente existem no CSV.
            cabecalhos = {nome.strip() for nome in (leitor.fieldnames or []) if nome is not None}
            formato_simples = cabecalhos == {COLUNA_URL_SIMPLES}
            urls_autorizadas = (
                _carregar_urls_autorizadas(caminho) if formato_simples else frozenset()
            )

            colunas_ausentes = (
                set()
                if formato_simples
                else COLUNAS_OBRIGATORIAS - cabecalhos
            )

            if colunas_ausentes:
                nomes = ", ".join(sorted(colunas_ausentes))

                raise ErroCatalogoFontes(f"colunas obrigatórias ausentes: {nomes}")

            alvos: list[AlvoColeta] = []

            falhas: list[FalhaLinhaCatalogo] = []

            # Este conjunto será usado para detectar IDs repetidos.
            ids_encontrados: set[str] = set()

            # A primeira linha contém os cabeçalhos.
            # Portanto, os dados começam na linha 2.
            for numero_linha, linha in enumerate(
                leitor,
                start=2,
            ):
                # Ignora linhas completamente vazias.
                if not any(_ler_texto_da_linha(linha, coluna) for coluna in cabecalhos):
                    continue

                try:
                    if formato_simples:
                        alvo = _criar_alvo_simples(
                            _ler_texto_da_linha(linha, COLUNA_URL_SIMPLES),
                            urls_autorizadas=urls_autorizadas,
                        )
                    else:
                        url_inicial = _ler_texto_da_linha(
                            linha,
                            "url_inicial",
                        )
                        endereco = urlsplit(url_inicial)

                        if endereco.scheme not in {"http", "https"} or not endereco.netloc:
                            raise ValueError("url_inicial deve ser uma URL HTTP ou HTTPS completa")

                        alvo = AlvoColeta(
                            alvo_id=_ler_texto_da_linha(
                                linha,
                                "alvo_id",
                            ),
                            empresa_nome=_ler_texto_da_linha(
                                linha,
                                "empresa_nome",
                            ),
                            fonte=Fonte(
                                _ler_texto_da_linha(
                                    linha,
                                    "fonte",
                                )
                            ),
                            url_inicial=url_inicial,
                            ativa=_ler_booleano(
                                _ler_texto_da_linha(
                                    linha,
                                    "ativa",
                                )
                            ),
                            limite_paginas=int(
                                _ler_texto_da_linha(
                                    linha,
                                    "limite_paginas",
                                )
                            ),
                            # A política utiliza o domínio retirado da URL
                            # e o status informado no catálogo.
                            politica=PoliticaFonte(
                                dominio=endereco.hostname or "",
                                status=_ler_status_politica(
                                    _ler_texto_da_linha(
                                        linha,
                                        "status_politica",
                                    )
                                ),
                                licenca_nome=_ler_texto_da_linha(
                                    linha,
                                    "licenca_nome",
                                ),
                                licenca_url=_ler_texto_da_linha(
                                    linha,
                                    "licenca_url",
                                ),
                                atribuicao_obrigatoria=_ler_booleano_opcional(
                                    linha,
                                    "atribuicao_obrigatoria",
                                ),
                                nome_atribuicao=_ler_texto_da_linha(
                                    linha,
                                    "empresa_nome",
                                ),
                                republicacao_permitida=_ler_booleano_opcional(
                                    linha,
                                    "republicacao_permitida",
                                ),
                                autorizacao_escrita=_ler_booleano_opcional(
                                    linha,
                                    "autorizacao_escrita",
                                ),
                                referencia_autorizacao=_ler_texto_da_linha(
                                    linha,
                                    "referencia_autorizacao",
                                ),
                            ),
                        )

                except (TypeError, ValueError) as erro:
                    mensagem = str(erro)

                    if tolerar_linhas_invalidas:
                        falhas.append(
                            FalhaLinhaCatalogo(
                                numero_linha=numero_linha,
                                alvo_id=(
                                    _ler_texto_da_linha(
                                        linha,
                                        "alvo_id" if not formato_simples else COLUNA_URL_SIMPLES,
                                    )
                                    or "não informado"
                                ),
                                mensagem=mensagem,
                            )
                        )
                        continue

                    # Acrescenta o número da linha para facilitar
                    # a correção do CSV no VS Code.
                    raise ErroCatalogoFontes(f"linha {numero_linha}: {mensagem}") from erro

                # IDs são comparados ignorando letras maiúsculas.
                chave_id = alvo.alvo_id.casefold()

                if chave_id in ids_encontrados:
                    mensagem = f"alvo_id duplicado: {alvo.alvo_id}"

                    if tolerar_linhas_invalidas:
                        falhas.append(
                            FalhaLinhaCatalogo(
                                numero_linha=numero_linha,
                                alvo_id=alvo.alvo_id,
                                mensagem=mensagem,
                            )
                        )
                        continue

                    raise ErroCatalogoFontes(f"linha {numero_linha}: {mensagem}")

                ids_encontrados.add(chave_id)
                alvos.append(alvo)

            # Tuplas impedem alterações acidentais depois da validação.
            return ResultadoCarregamentoCatalogo(
                alvos=tuple(alvos),
                falhas=tuple(falhas),
            )

    except ErroCatalogoFontes:
        # Erros de validação já possuem uma mensagem clara.
        raise

    except (OSError, csv.Error) as erro:
        # Converte erros técnicos de arquivo em uma mensagem
        # compreensível para quem estiver executando o crawler.
        raise ErroCatalogoFontes(f"não foi possível ler o catálogo: {caminho}") from erro


def carregar_alvos_csv(
    caminho: Path,
) -> tuple[AlvoColeta, ...]:
    """Carrega o catálogo e rejeita o arquivo na primeira linha inválida."""

    return _carregar_catalogo_csv(
        caminho,
        tolerar_linhas_invalidas=False,
    ).alvos


def carregar_alvos_csv_tolerante(
    caminho: Path,
) -> ResultadoCarregamentoCatalogo:
    """Carrega alvos válidos e relata linhas inválidas sem interromper o lote."""

    return _carregar_catalogo_csv(
        caminho,
        tolerar_linhas_invalidas=True,
    )
