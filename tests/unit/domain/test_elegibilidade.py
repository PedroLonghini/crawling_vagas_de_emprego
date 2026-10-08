"""Testes das regras finais de elegibilidade para publicação."""

from datetime import (
    UTC,
    date,
    datetime,
    timedelta,
)

from observatorio_vagas.domain.anuncio import (
    AnuncioVaga,
)
from observatorio_vagas.domain.elegibilidade import (
    CodigoBloqueioPublicacao,
    ResultadoElegibilidadePublicacao,
    avaliar_elegibilidade_publicacao,
)
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    Fonte,
    StatusAnuncio,
    StatusPoliticaFonte,
)
from observatorio_vagas.domain.politica_fonte import (
    PoliticaFonte,
)
from observatorio_vagas.domain.prontidao import (
    RelatorioProntidao,
    avaliar_prontidao_empregos,
)
from observatorio_vagas.domain.vaga import (
    VagaCanonica,
)

AGORA = datetime(
    2026,
    8,
    25,
    12,
    0,
    tzinfo=UTC,
)

DOMINIO = "carreiras.empresa.example"


def criar_cenario(
    *,
    fonte: Fonte = Fonte.PAGINA_CARREIRAS,
    status: StatusAnuncio = StatusAnuncio.ATIVO,
    expira_em: date | datetime | None = None,
    com_cnpj: bool = True,
    com_url_candidatura: bool = True,
    pais: str = "BR",
) -> tuple[
    AnuncioVaga,
    VagaCanonica,
    RelatorioProntidao,
]:
    """Cria uma vaga com poucos campos opcionais."""

    empresa = Empresa(
        razao_social="Empresa Exemplo S.A.",
        cnpj=("12.345.678/0001-90" if com_cnpj else None),
        descricao=("Empresa brasileira especializada em soluções de tecnologia."),
    )

    anuncio = AnuncioVaga(
        fonte=fonte,
        id_externo="VAGA-001",
        url=(f"https://{DOMINIO}/vagas/001"),
        url_candidatura=(f"https://{DOMINIO}/candidatura/001" if com_url_candidatura else None),
        empresa_id=empresa.id,
        titulo_original="Analista de Sistemas",
        descricao_original=("Descrição original da oportunidade."),
        endereco_original="São Paulo, SP",
        status=status,
        expira_em=expira_em,
        hash_conteudo="a" * 64,
        referencia_bruta=("corpos/vaga-001.bin"),
    )

    vaga = VagaCanonica(
        empresa_id=empresa.id,
        titulo_normalizado=("Analista de Sistemas"),
        descricao_normalizada=(
            "Buscamos uma pessoa para analisar "
            "e desenvolver sistemas corporativos "
            "com qualidade, segurança, testes "
            "automatizados, documentação técnica "
            "e colaboração com diferentes equipes."
        ),
        endereco="São Paulo, SP",
        pais=pais,
    )

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=None,
        anuncio=anuncio,
        vaga=vaga,
    )

    return anuncio, vaga, relatorio


def politica(
    status: StatusPoliticaFonte = (StatusPoliticaFonte.APROVADA),
    *,
    dominio: str = DOMINIO,
) -> PoliticaFonte:
    """Cria uma política explícita para a fonte."""

    return PoliticaFonte(
        dominio=dominio,
        status=status,
        licenca_nome=("CC BY 4.0" if status is StatusPoliticaFonte.APROVADA else ""),
        licenca_url=(
            "https://creativecommons.org/licenses/by/4.0/deed.pt-br"
            if status is StatusPoliticaFonte.APROVADA
            else ""
        ),
        republicacao_permitida=(status is StatusPoliticaFonte.APROVADA),
    )


def codigos(
    resultado: ResultadoElegibilidadePublicacao,
) -> set[CodigoBloqueioPublicacao]:
    """Obtém os códigos encontrados no resultado."""

    return {bloqueio.codigo for bloqueio in resultado.bloqueios}


def test_opcionais_ausentes_nao_bloqueiam_publicacao() -> None:
    """Ter menos de 24 campos não impede o envio."""

    anuncio, vaga, relatorio = criar_cenario(expira_em=(AGORA + timedelta(days=10)))

    assert relatorio.total_campos == 24

    assert len(relatorio.campos_preenchidos) < 24

    # Existem opcionais ausentes.
    assert relatorio.alertas

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert resultado.elegivel is True
    assert resultado.bloqueios == ()


def test_fonte_somente_coleta_bloqueia_republicacao() -> None:
    """Permissão de coleta não permite republicação."""

    anuncio, vaga, relatorio = criar_cenario()

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(StatusPoliticaFonte.SOMENTE_COLETA),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO in codigos(resultado)


def test_dominio_diferente_da_politica_bloqueia() -> None:
    """Uma autorização não pode servir para outro domínio."""

    anuncio, vaga, relatorio = criar_cenario()

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(dominio=("outra-fonte.example")),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.DOMINIO_DIVERGENTE in codigos(resultado)


def test_querido_diario_aceita_dominio_oficial_de_armazenamento() -> None:
    """O PDF oficial pode vir do armazenamento declarado da própria fonte."""

    anuncio, vaga, relatorio = criar_cenario(fonte=Fonte.QUERIDO_DIARIO)
    anuncio = anuncio.model_copy(
        update={
            "url": ("https://data.queridodiario.ok.org.br/3550308/2026-09-02/edicao.pdf"),
        }
    )

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(dominio="api.queridodiario.org.br"),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.DOMINIO_DIVERGENTE not in codigos(resultado)


def test_vaga_encerrada_bloqueia() -> None:
    """Uma vaga encerrada não pode ser publicada."""

    anuncio, vaga, relatorio = criar_cenario(status=StatusAnuncio.ENCERRADO)

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.STATUS_NAO_PUBLICAVEL in codigos(resultado)


def test_vaga_expirada_bloqueia() -> None:
    """Uma data vencida impede a publicação."""

    anuncio, vaga, relatorio = criar_cenario(expira_em=(AGORA - timedelta(minutes=1)))

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.VAGA_EXPIRADA in codigos(resultado)


def test_candidatura_em_dominio_bloqueado_bloqueia() -> None:
    """Blacklist indireta: agregador que leva à Gupy ou ao Vagas.com não passa."""

    def avaliar(url_candidatura):
        anuncio, vaga, relatorio = criar_cenario()
        anuncio = anuncio.model_copy(update={"url_candidatura": url_candidatura})
        return avaliar_elegibilidade_publicacao(
            anuncio=anuncio,
            vaga=vaga,
            politica_fonte=politica(),
            relatorio_prontidao=relatorio,
            momento_referencia=AGORA,
        )

    for bloqueada in (
        "https://empresa.gupy.io/jobs/123",
        "https://www.vagas.com.br/vagas/v2826231?fnt=19",
    ):
        resultado = avaliar(bloqueada)
        assert CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO in codigos(resultado)
        assert any("candidatura" in b.mensagem for b in resultado.bloqueios)

    for permitida in ("https://empresa.solides.jobs/vaga/1", None):
        assert CodigoBloqueioPublicacao.FONTE_SEM_PERMISSAO not in codigos(avaliar(permitida))


def test_vaga_publicada_ha_mais_de_30_dias_bloqueia() -> None:
    """Regra de idade: só vagas publicadas na fonte há até 30 dias."""

    def avaliar(publicado_em):
        anuncio, vaga, relatorio = criar_cenario()
        anuncio = anuncio.model_copy(update={"publicado_em": publicado_em})
        return codigos(
            avaliar_elegibilidade_publicacao(
                anuncio=anuncio,
                vaga=vaga,
                politica_fonte=politica(),
                relatorio_prontidao=relatorio,
                momento_referencia=AGORA,
            )
        )

    assert CodigoBloqueioPublicacao.VAGA_ANTIGA in avaliar(date(2026, 7, 25))
    assert CodigoBloqueioPublicacao.VAGA_ANTIGA in avaliar(AGORA - timedelta(days=31))
    assert CodigoBloqueioPublicacao.VAGA_ANTIGA not in avaliar(date(2026, 7, 26))
    assert CodigoBloqueioPublicacao.VAGA_ANTIGA not in avaliar(AGORA - timedelta(days=2))
    # Sem data de publicação: decisão do usuário é manter elegível.
    assert CodigoBloqueioPublicacao.VAGA_ANTIGA not in avaliar(None)


def test_url_de_candidatura_ausente_usa_url_da_fonte() -> None:
    """A URL da vaga supre a ausência de link direto de candidatura."""

    anuncio, vaga, relatorio = criar_cenario(com_url_candidatura=False)

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert resultado.elegivel is True
    assert "company.applyUrl" not in resultado.campos_api_bloqueadores


def test_cnpj_ausente_nao_bloqueia_quando_ha_url_da_fonte() -> None:
    """CNPJ é opcional quando a vaga possui URL de origem."""

    anuncio, vaga, relatorio = criar_cenario(com_cnpj=False)

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert resultado.elegivel is True
    assert "company.nationalRegister" not in resultado.campos_api_bloqueadores


def test_subdominio_de_candidatura_abler_e_aceito() -> None:
    """A vitrine e a candidatura Abler usam hosts públicos diferentes."""

    anuncio, vaga, relatorio = criar_cenario()
    anuncio_abler = anuncio.model_copy(
        update={
            "url": "https://goldenrh.abler.com.br/vagas/analista",
            "fonte": Fonte.PAGINA_CARREIRAS,
        }
    )

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio_abler,
        vaga=vaga,
        politica_fonte=politica(dominio="ats.abler.com.br"),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.DOMINIO_DIVERGENTE not in codigos(resultado)


def test_empregos_nunca_pode_ser_fonte() -> None:
    """O destino não pode ser usado como origem."""

    anuncio, vaga, relatorio = criar_cenario(fonte=Fonte.EMPREGOS)

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert CodigoBloqueioPublicacao.FONTE_EMPREGOS in codigos(resultado)


def test_vaga_fora_do_brasil_nao_gera_payload() -> None:
    """A origem aprovada não autoriza publicar uma vaga internacional."""

    anuncio, vaga, relatorio = criar_cenario(pais="US")

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert resultado.elegivel is False
    assert CodigoBloqueioPublicacao.LOCALIZACAO_FORA_DO_BRASIL in codigos(resultado)


def test_localizacao_estrangeira_legada_com_pais_br_nao_gera_payload() -> None:
    """O endereço bruto impede que o padrão BR antigo libere Buenos Aires."""

    anuncio, vaga, relatorio = criar_cenario()
    anuncio_estrangeiro = anuncio.model_copy(
        update={
            "localidade_original": "Buenos Aires",
            "endereco_original": "Buenos Aires",
        }
    )
    vaga_estrangeira = vaga.model_copy(
        update={"cidade": "Buenos Aires", "estado": None, "endereco": "Buenos Aires"}
    )

    resultado = avaliar_elegibilidade_publicacao(
        anuncio=anuncio_estrangeiro,
        vaga=vaga_estrangeira,
        politica_fonte=politica(),
        relatorio_prontidao=relatorio,
        momento_referencia=AGORA,
    )

    assert resultado.elegivel is False
    assert CodigoBloqueioPublicacao.LOCALIZACAO_FORA_DO_BRASIL in codigos(resultado)
