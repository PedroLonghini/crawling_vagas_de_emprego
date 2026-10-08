"""Regras finais que decidem se uma vaga pode seguir para publicação."""

from datetime import UTC, date, datetime
from enum import StrEnum
from urllib.parse import urlsplit

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.common import (
    ModeloDominio,
    agora_utc,
)
from observatorio_vagas.domain.enums import (
    Fonte,
    StatusAnuncio,
)
from observatorio_vagas.domain.localizacao import localizacao_eh_brasileira
from observatorio_vagas.domain.politica_fonte import (
    PoliticaFonte,
    encontrar_restricao_dominio,
)
from observatorio_vagas.domain.prontidao import (
    RelatorioProntidao,
)
from observatorio_vagas.domain.vaga import VagaCanonica


class CodigoBloqueioPublicacao(StrEnum):
    """Motivos controlados que impedem uma publicação."""

    FONTE_EMPREGOS = "fonte_empregos"

    FONTE_SEM_PERMISSAO = "fonte_sem_permissao"

    DOMINIO_DIVERGENTE = "dominio_divergente"

    STATUS_NAO_PUBLICAVEL = "status_nao_publicavel"

    VAGA_EXPIRADA = "vaga_expirada"

    VAGA_ANTIGA = "vaga_antiga"

    LOCALIZACAO_FORA_DO_BRASIL = "localizacao_fora_do_brasil"

    CAMPO_API_OBRIGATORIO = "campo_api_obrigatorio"


class BloqueioPublicacao(ModeloDominio):
    """Explica uma condição que impede o envio."""

    codigo: CodigoBloqueioPublicacao
    mensagem: str

    # Será preenchido quando o bloqueio estiver
    # relacionado a um campo obrigatório da API.
    campo: str | None = None


class ResultadoElegibilidadePublicacao(ModeloDominio):
    """Resultado independente do percentual preenchido."""

    bloqueios: tuple[
        BloqueioPublicacao,
        ...,
    ] = ()

    @property
    def elegivel(self) -> bool:
        """Informa se a vaga pode seguir para publicação."""

        return not self.bloqueios

    @property
    def campos_api_bloqueadores(
        self,
    ) -> tuple[str, ...]:
        """Lista somente campos obrigatórios bloqueadores."""

        return tuple(bloqueio.campo for bloqueio in self.bloqueios if bloqueio.campo is not None)


# Estes status representam anúncios que ainda podem
# participar do fluxo de publicação.
STATUS_PUBLICAVEIS = frozenset(
    {
        StatusAnuncio.DESCOBERTO,
        StatusAnuncio.ATIVO,
        StatusAnuncio.ALTERADO,
        StatusAnuncio.REABERTO,
    }
)

# Algumas fontes abertas entregam o conteúdo em um domínio de armazenamento
# diferente do domínio da API. As relações aceitas precisam ser explícitas:
# nenhum CDN arbitrário herda a autorização da fonte.
DOMINIOS_DERIVADOS_APROVADOS = {
    # A Abler separa a vitrine (ats.abler.com.br) da página individual onde a
    # candidatura acontece ({empresa}.abler.com.br). Ambas são partes do
    # mesmo ATS público e a URL específica continua preservada no anúncio.
    Fonte.PAGINA_CARREIRAS: {
        "ats.abler.com.br": frozenset({"*.abler.com.br"}),
    },
    Fonte.QUERIDO_DIARIO: {
        "api.queridodiario.org.br": frozenset(
            {
                "data.queridodiario.ok.org.br",
            }
        ),
    },
}


def _dominio_corresponde(
    anuncio: AnuncioVaga,
    politica: PoliticaFonte,
) -> bool:
    """Confere se a URL pertence ao domínio autorizado."""

    hostname = urlsplit(str(anuncio.url)).hostname

    if hostname is None:
        return False

    dominio_url = hostname.casefold().removeprefix("www.")

    dominio_politica = politica.dominio.removeprefix("www.")

    if dominio_url == dominio_politica or dominio_url.endswith(f".{dominio_politica}"):
        return True

    derivados_da_fonte = DOMINIOS_DERIVADOS_APROVADOS.get(
        anuncio.fonte,
        {},
    )

    for dominio_derivado in derivados_da_fonte.get(dominio_politica, frozenset()):
        if dominio_derivado.startswith("*."):
            sufixo = dominio_derivado[2:]
            if dominio_url.endswith(f".{sufixo}"):
                return True
        elif dominio_url == dominio_derivado:
            return True
    return False


def _esta_expirada(
    expira_em: date | datetime | None,
    *,
    momento_referencia: datetime,
) -> bool:
    """Compara datas e horários com segurança."""

    # A ausência de uma data de expiração não bloqueia,
    # pois expireAt é opcional.
    if expira_em is None:
        return False

    # datetime precisa ser tratado antes de date,
    # porque datetime também é uma subclasse de date.
    if isinstance(
        expira_em,
        datetime,
    ):
        data_hora = expira_em

        # Datas sem fuso serão interpretadas como UTC.
        if data_hora.tzinfo is None:
            data_hora = data_hora.replace(tzinfo=UTC)

        return data_hora <= momento_referencia

    # Uma data sem horário continua válida durante
    # aquele dia inteiro.
    return expira_em < momento_referencia.date()


# Decisão do usuário (07/10/2026): só vagas publicadas na fonte há até 30 dias
# seguem para o Empregos. Sem data de publicação a vaga continua elegível; a
# conferência de vagas removidas tira do ar as que sumirem da origem.
IDADE_MAXIMA_PUBLICACAO_DIAS = 30


def _publicada_ha_mais_de_limite(
    publicado_em: date | datetime | None,
    *,
    momento_referencia: datetime,
) -> bool:
    """Compara só os dias, com datas sem fuso lidas como UTC."""

    if publicado_em is None:
        return False
    if isinstance(publicado_em, datetime):
        data_hora = publicado_em
        if data_hora.tzinfo is None:
            data_hora = data_hora.replace(tzinfo=UTC)
        publicado_em = data_hora.astimezone(momento_referencia.tzinfo).date()
    return (momento_referencia.date() - publicado_em).days > IDADE_MAXIMA_PUBLICACAO_DIAS


def _esta_no_brasil(anuncio: AnuncioVaga, vaga: VagaCanonica) -> bool:
    """Exige país Brasil e endereço que confirme a localização.

    Isso também protege registros antigos: antes da correção, uma localização
    sem país recebia BR por padrão. O texto bruto do anúncio continua sendo a
    evidência que decide se a vaga pode ser enviada ao Empregos.
    """

    return localizacao_eh_brasileira(
        vaga.pais,
        anuncio.localidade_original,
        anuncio.endereco_original,
        vaga.cidade,
        vaga.estado,
        vaga.endereco,
        vaga.cep,
    )


def avaliar_elegibilidade_publicacao(
    *,
    anuncio: AnuncioVaga,
    vaga: VagaCanonica,
    politica_fonte: PoliticaFonte,
    relatorio_prontidao: RelatorioProntidao,
    momento_referencia: datetime | None = None,
) -> ResultadoElegibilidadePublicacao:
    """Avalia somente condições que realmente bloqueiam."""

    referencia = momento_referencia or agora_utc()

    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=UTC)

    bloqueios: list[BloqueioPublicacao] = []

    # O Empregos será o destino dos dados.
    # Ele nunca poderá ser utilizado como fonte.
    if anuncio.fonte is Fonte.EMPREGOS:
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.FONTE_EMPREGOS),
                mensagem=(
                    "O Empregos é o destino da "
                    "publicação e não pode ser usado "
                    "como fonte de aquisição."
                ),
            )
        )

    # Domínio na lista de restrições (fonte não autorizada): nem anúncio antigo,
    # gravado antes da restrição, pode ser publicado.
    restricao = encontrar_restricao_dominio(
        (urlsplit(str(anuncio.url)).hostname or "").removeprefix("www.")
    )
    if restricao is not None:
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO),
                mensagem=f"Fonte não autorizada ({restricao.nome}): {restricao.motivo}.",
            )
        )

    # Blacklist indireta (decisão do usuário, 07/10/2026): agregador que manda o
    # candidato para um domínio bloqueado (Gupy, Vagas.com, Indeed...) está
    # republicando uma vaga daquele domínio, e ela também não pode seguir.
    if anuncio.url_candidatura is not None and restricao is None:
        restricao_candidatura = encontrar_restricao_dominio(
            (urlsplit(str(anuncio.url_candidatura)).hostname or "").removeprefix("www.")
        )
        if restricao_candidatura is not None:
            bloqueios.append(
                BloqueioPublicacao(
                    codigo=(CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO),
                    mensagem=(
                        "A candidatura leva a uma fonte não autorizada "
                        f"({restricao_candidatura.nome}): {restricao_candidatura.motivo}."
                    ),
                )
            )

    # Uma fonte pode permitir coleta para análise,
    # mas proibir republicação.
    if not politica_fonte.permite_publicacao:
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO),
                mensagem=("A política da fonte não autoriza a republicação desta vaga."),
            )
        )

    # Uma política aprovada para um domínio não pode
    # ser reutilizada para autorizar outro domínio.
    if not _dominio_corresponde(
        anuncio,
        politica_fonte,
    ):
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.DOMINIO_DIVERGENTE),
                mensagem=(
                    "O domínio do anúncio não corresponde ao domínio aprovado na política da fonte."
                ),
            )
        )

    # Vagas encerradas, inválidas, ausentes ou com
    # status desconhecido não seguem para publicação.
    if anuncio.status not in STATUS_PUBLICAVEIS:
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.STATUS_NAO_PUBLICAVEL),
                mensagem=(f"O status {anuncio.status.value} não permite publicação."),
            )
        )

    # O catálogo do Empregos desta operação é brasileiro. Uma vaga em outro
    # país nunca pode gerar payload, ainda que a fonte e os campos estejam OK.
    if not _esta_no_brasil(anuncio, vaga):
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.LOCALIZACAO_FORA_DO_BRASIL),
                campo="location.address",
                mensagem=("A vaga está localizada fora do Brasil e não pode ser publicada."),
            )
        )

    # expireAt é opcional.
    #
    # Entretanto, quando a fonte fornece uma data
    # e ela já passou, a vaga está vencida.
    if _esta_expirada(
        anuncio.expira_em,
        momento_referencia=referencia,
    ):
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.VAGA_EXPIRADA),
                mensagem=("A data de expiração da vaga já passou."),
            )
        )

    if _publicada_ha_mais_de_limite(
        anuncio.publicado_em,
        momento_referencia=referencia,
    ):
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.VAGA_ANTIGA),
                mensagem=(
                    f"A vaga foi publicada na fonte há mais de {IDADE_MAXIMA_PUBLICACAO_DIAS} dias."
                ),
            )
        )

    # Os erros já identificados pelo relatório são
    # organizados pelo nome do campo.
    mensagens_erros = {problema.campo: problema.mensagem for problema in relatorio_prontidao.erros}

    # Selecionamos somente campos obrigatórios ausentes.
    #
    # Campos opcionais ausentes não entram aqui.
    campos_obrigatorios = {
        campo.campo for campo in (relatorio_prontidao.campos_obrigatorios_ausentes)
    }

    campos_bloqueadores = campos_obrigatorios | set(mensagens_erros)

    # Cada obrigatório inválido recebe um bloqueio
    # identificando exatamente o campo.
    for campo in sorted(campos_bloqueadores):
        bloqueios.append(
            BloqueioPublicacao(
                codigo=(CodigoBloqueioPublicacao.CAMPO_API_OBRIGATORIO),
                campo=campo,
                mensagem=(
                    mensagens_erros.get(
                        campo,
                        (f"O campo obrigatório {campo} não possui valor utilizável."),
                    )
                ),
            )
        )

    return ResultadoElegibilidadePublicacao(
        bloqueios=tuple(bloqueios),
    )
