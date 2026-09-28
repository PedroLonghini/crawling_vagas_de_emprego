"""Regras de autorização aplicadas às fontes externas."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from observatorio_vagas.domain.enums import StatusPoliticaFonte

# O site do Empregos será destino de publicação, não fonte do crawler.
DOMINIO_EMPREGOS = "empregos.com.br"


def licenca_permite_republicacao(nome: str) -> bool:
    """Aceita apenas licenças abertas compatíveis com o uso do projeto.

    O catálogo não pode transformar uma frase como "permissão com crédito" em
    licença. Também excluímos cláusulas ``NC`` e ``ND``: a publicação comercial
    e a normalização/atribuição do anúncio são partes do fluxo do Observatório.
    """

    if not isinstance(nome, str):
        return False

    normalizada = " ".join(nome.casefold().split())

    if not normalizada or "permiss" in normalizada:
        return False

    if "noncommercial" in normalizada or "não comercial" in normalizada:
        return False

    if "no derivatives" in normalizada or "sem deriva" in normalizada:
        return False

    if "-nc" in normalizada or "-nd" in normalizada:
        return False

    return (
        normalizada.startswith("cc0")
        or normalizada.startswith("cc by")
        or normalizada.startswith("creative commons attribution")
        or normalizada.startswith("odbl")
        or normalizada.startswith("open data commons open database license")
    )


def _normalizar_dominio(dominio: str) -> str:
    """Valida e padroniza o domínio informado na política."""

    if not isinstance(dominio, str):
        raise TypeError("dominio precisa ser texto")

    # Ignoramos espaços, diferença entre maiúsculas e minúsculas
    # e um ponto final que possa ter sido adicionado ao domínio.
    normalizado = dominio.strip().casefold().rstrip(".")

    if not normalizado:
        raise ValueError("dominio não pode ser vazio")

    # A política recebe somente o domínio.
    # Protocolo, porta e caminho pertencem à URL de coleta.
    if "://" in normalizado or "/" in normalizado or ":" in normalizado:
        raise ValueError("dominio deve conter somente o nome do domínio")

    if "." not in normalizado:
        raise ValueError("dominio precisa ser um nome completo")

    return normalizado


@dataclass(frozen=True, slots=True)
class RestricaoDominioColeta:
    """Motivo auditável para impedir o crawler de acessar um domínio."""

    dominio_raiz: str
    codigo: str
    nome: str
    motivo: str
    referencia: str


# Esta lista é uma barreira técnica independente do catálogo e dos adaptadores.
#
# Mesmo que uma linha seja marcada como aprovada por engano, estes domínios e
# todos os seus subdomínios continuam proibidos antes de qualquer download.
RESTRICOES_DOMINIOS_COLETA = (
    RestricaoDominioColeta(
        dominio_raiz=DOMINIO_EMPREGOS,
        codigo="dominio_empregos",
        nome="Empregos",
        motivo="o Empregos é destino de publicação e nunca fonte do crawler",
        referencia="https://partner.empregos.com.br/docs",
    ),
    RestricaoDominioColeta(
        dominio_raiz="indeed.com",
        codigo="dominio_indeed",
        nome="Indeed",
        motivo="a coleta automatizada exige autorização expressa por escrito",
        referencia="https://br.indeed.com/legal?hl=pt",
    ),
    RestricaoDominioColeta(
        dominio_raiz="indeed.com.br",
        codigo="dominio_indeed",
        nome="Indeed",
        motivo="a coleta automatizada exige autorização expressa por escrito",
        referencia="https://br.indeed.com/legal?hl=pt",
    ),
    RestricaoDominioColeta(
        dominio_raiz="infojobs.com.br",
        codigo="dominio_infojobs",
        nome="InfoJobs",
        motivo="os termos públicos proíbem o uso de robot ou crawler",
        referencia=("https://www.infojobs.com.br/legal/aviso-legal-para-empresas__15726.aspx"),
    ),
    RestricaoDominioColeta(
        dominio_raiz="catho.com.br",
        codigo="dominio_catho",
        nome="Catho",
        motivo="a fonte não autoriza coleta por crawler para este projeto",
        referencia="https://www.catho.com.br/termos-de-uso/",
    ),
    RestricaoDominioColeta(
        dominio_raiz="bluy.com",
        codigo="dominio_bluy",
        nome="Bluy",
        motivo="os termos exigem autorização prévia para utilizar o conteúdo",
        referencia="https://www.bluy.com/termos-de-uso/",
    ),
    RestricaoDominioColeta(
        dominio_raiz="ciadeestagios.com.br",
        codigo="dominio_cia_estagios",
        nome="Companhia de Estágios",
        motivo="os termos do grupo exigem autorização prévia para utilizar o conteúdo",
        referencia="https://www.ciadeestagios.com.br/en/terms-of-use/",
    ),
    RestricaoDominioColeta(
        dominio_raiz="vagas.com.br",
        codigo="dominio_vagas_com",
        nome="Vagas.com",
        motivo="os termos proíbem reprodução sem autorização expressa",
        referencia="https://www.vagas.com.br/candidatos/termos-de-uso",
    ),
    RestricaoDominioColeta(
        dominio_raiz="nic.br",
        codigo="dominio_nic_br",
        nome="NIC.br",
        motivo="os termos específicos restringem uso comercial do conteúdo",
        referencia="https://nic.br/politica-de-privacidade-e-termos-de-uso/",
    ),
)


def encontrar_restricao_dominio(
    dominio: str,
) -> RestricaoDominioColeta | None:
    """Localiza a restrição aplicável ao domínio ou a um subdomínio."""

    if not isinstance(dominio, str):
        raise TypeError("dominio precisa ser texto")

    normalizado = dominio.strip().casefold().rstrip(".")

    if not normalizado:
        return None

    for restricao in RESTRICOES_DOMINIOS_COLETA:
        raiz = restricao.dominio_raiz

        if normalizado == raiz or normalizado.endswith(f".{raiz}"):
            return restricao

    return None


@dataclass(frozen=True, slots=True)
class PoliticaFonte:
    """Decide se uma fonte externa pode ser coletada e republicada."""

    # Domínio externo ao qual a política será aplicada.
    # Exemplo: empresa.gupy.io
    dominio: str

    # Uma fonte nova começa pendente e, portanto, sem permissão.
    status: StatusPoliticaFonte = StatusPoliticaFonte.PENDENTE

    # Nome público da licença ou instrumento que permite a reutilização.
    # Exemplos: "CC BY 4.0" ou "Open Data Commons PDDL 1.0".
    licenca_nome: str = ""

    # Página oficial onde a licença pode ser conferida.
    licenca_url: str = ""

    # Informa se a publicação precisa citar a origem dos dados.
    atribuicao_obrigatoria: bool = False

    # Nome que deve aparecer no crédito quando a licença exigir atribuição.
    # O catálogo preenche este valor com o nome público da fonte; o domínio é
    # usado como alternativa segura para políticas criadas fora do catálogo.
    nome_atribuicao: str = ""

    # Confirmação explícita de que a licença permite republicação.
    # Este campo é independente do status operacional.
    republicacao_permitida: bool = False

    # Autorizações privadas são registradas sem serem apresentadas como
    # licença pública. A referência identifica o registro interno, não o
    # documento confidencial.
    autorizacao_escrita: bool = False
    referencia_autorizacao: str = ""

    def __post_init__(self) -> None:
        """Valida a política no momento em que ela é criada."""

        dominio_normalizado = _normalizar_dominio(self.dominio)

        if not isinstance(self.status, StatusPoliticaFonte):
            raise TypeError("status precisa ser um StatusPoliticaFonte")

        if not isinstance(self.licenca_nome, str):
            raise TypeError("licenca_nome precisa ser texto")

        if not isinstance(self.licenca_url, str):
            raise TypeError("licenca_url precisa ser texto")

        if not isinstance(self.atribuicao_obrigatoria, bool):
            raise TypeError("atribuicao_obrigatoria precisa ser um valor booleano")

        if not isinstance(self.nome_atribuicao, str):
            raise TypeError("nome_atribuicao precisa ser texto")

        if not isinstance(self.republicacao_permitida, bool):
            raise TypeError("republicacao_permitida precisa ser um valor booleano")

        if not isinstance(self.autorizacao_escrita, bool):
            raise TypeError("autorizacao_escrita precisa ser um valor booleano")

        if not isinstance(self.referencia_autorizacao, str):
            raise TypeError("referencia_autorizacao precisa ser texto")

        licenca_nome = self.licenca_nome.strip()
        licenca_url = self.licenca_url.strip()
        nome_atribuicao = self.nome_atribuicao.strip() or dominio_normalizado
        referencia_autorizacao = self.referencia_autorizacao.strip()

        if licenca_url:
            endereco_licenca = urlsplit(licenca_url)

            if endereco_licenca.scheme not in {"http", "https"} or not endereco_licenca.netloc:
                raise ValueError("licenca_url deve ser uma URL HTTP ou HTTPS completa")

        if self.republicacao_permitida and self.status is not StatusPoliticaFonte.APROVADA:
            raise ValueError("republicacao_permitida=true exige status_politica=aprovada")

        possui_licenca_aberta = bool(licenca_nome and licenca_url)
        possui_autorizacao_privada = bool(
            self.autorizacao_escrita and referencia_autorizacao
        )

        if self.autorizacao_escrita and self.status is not StatusPoliticaFonte.APROVADA:
            raise ValueError("autorizacao_escrita=true exige status_politica=aprovada")

        if self.autorizacao_escrita and not referencia_autorizacao:
            raise ValueError("autorizacao_escrita=true exige referencia_autorizacao")

        if self.republicacao_permitida and not (
            possui_licenca_aberta or possui_autorizacao_privada
        ):
            raise ValueError(
                "republicacao_permitida=true exige licença pública completa "
                "ou autorização escrita com referência"
            )

        # Uma configuração errada jamais deve liberar um domínio conhecido
        # como proibido. O registro ainda pode existir para documentar o
        # bloqueio, mas precisa usar explicitamente o estado BLOQUEADA.
        restricao = encontrar_restricao_dominio(dominio_normalizado)

        if restricao is not None and self.status is not StatusPoliticaFonte.BLOQUEADA:
            raise ValueError(f"{restricao.nome}: {restricao.motivo}; use status_politica=bloqueada")

        # A classe é congelada. Esta é a forma segura de guardar
        # o domínio normalizado durante a inicialização.
        object.__setattr__(
            self,
            "dominio",
            dominio_normalizado,
        )

        object.__setattr__(
            self,
            "licenca_nome",
            licenca_nome,
        )

        object.__setattr__(
            self,
            "licenca_url",
            licenca_url,
        )

        object.__setattr__(
            self,
            "nome_atribuicao",
            nome_atribuicao,
        )

        object.__setattr__(
            self,
            "referencia_autorizacao",
            referencia_autorizacao,
        )

    @property
    def permite_coleta(self) -> bool:
        """Informa se o crawler pode acessar a fonte."""

        return self.status in {
            StatusPoliticaFonte.APROVADA,
            StatusPoliticaFonte.SOMENTE_COLETA,
        }

    @property
    def permite_publicacao(self) -> bool:
        """Informa se uma vaga desta fonte pode ser republicada."""

        return (
            self.status is StatusPoliticaFonte.APROVADA
            and self.republicacao_permitida
            and (
                (
                    licenca_permite_republicacao(self.licenca_nome)
                    and bool(self.licenca_url)
                )
                or (self.autorizacao_escrita and bool(self.referencia_autorizacao))
            )
        )

    def montar_credito_publicacao(self, *, url_origem: str) -> str | None:
        """Monta o crédito que precisa acompanhar uma vaga republicada.

        A API do Empregos não possui um campo próprio para licença. Quando a
        fonte exigir atribuição, este texto é acrescentado à descrição da
        vaga, preservando a origem para quem estiver vendo o anúncio.
        """

        if not self.atribuicao_obrigatoria:
            return None

        permissao = self.licenca_nome or "Autorização escrita confirmada"
        return (
            f"Fonte: {self.nome_atribuicao}. "
            f"Licença/permissão: {permissao}. "
            f"Origem: {url_origem}"
        )
