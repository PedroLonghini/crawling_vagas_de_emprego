"""Testes do relatório de prontidão para futura publicação."""

from decimal import Decimal

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    Fonte,
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.domain.prontidao import (
    CAMPOS_API_EMPREGOS,
    CampoProntidao,
    RelatorioProntidao,
    SituacaoCampoProntidao,
    avaliar_prontidao_empregos,
)
from observatorio_vagas.domain.recrutador import Recrutador
from observatorio_vagas.domain.vaga import (
    SalarioNormalizado,
    VagaCanonica,
)


def criar_cenario_completo() -> tuple[
    Empresa,
    Recrutador,
    AnuncioVaga,
    VagaCanonica,
]:
    """Cria dados completos e reutilizáveis para os testes."""

    empresa = Empresa(
        razao_social="Tecnologia Atlas S.A.",
        nome_fantasia="Tecnologia Atlas",
        cnpj="12.345.678/0001-90",
        descricao=("Empresa fictícia especializada em tecnologia e desenvolvimento de sistemas."),
    )

    recrutador = Recrutador(
        empresa_id=empresa.id,
        id_externo="RECRUTADOR-001",
        nome="Maria da Silva",
        email="maria@tecnologia-atlas.example.com",
    )

    anuncio = AnuncioVaga(
        fonte=Fonte.PAGINA_CARREIRAS,
        id_externo="VAGA-001",
        url=("https://tecnologia-atlas.example.com/carreiras/vaga-001"),
        url_candidatura=("https://candidaturas.example.com/apply/vaga-001"),
        titulo_original="Desenvolvedor Python Pleno",
        hash_conteudo="c" * 64,
        referencia_bruta="pagina_carreiras/vaga-001.html",
    )

    salario = SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        periodo=PeriodoSalario.MES,
        minimo=Decimal("6500"),
        maximo=Decimal("8500"),
    )

    vaga = VagaCanonica(
        empresa_id=empresa.id,
        titulo_normalizado="Desenvolvedor Python Pleno",
        descricao_normalizada=(
            "Buscamos uma pessoa desenvolvedora para criar e "
            "manter APIs utilizando Python, bancos de dados "
            "relacionais, testes automatizados e boas práticas "
            "de desenvolvimento de software."
        ),
        cidade="São Paulo",
        estado="SP",
        endereco="São Paulo, SP",
        cep="01045-000",
        latitude=-23.5440722,
        longitude=-46.6450811,
        modalidade=ModalidadeTrabalho.HIBRIDO,
        regime=RegimeContratacao.CLT,
        senioridade=Senioridade.PLENO,
        salario=salario,
    )

    return empresa, recrutador, anuncio, vaga


def test_vaga_completa_esta_pronta_para_envio() -> None:
    """Uma vaga com os obrigatórios válidos deve estar pronta."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
    )

    assert relatorio.total_campos == 24
    assert relatorio.pronto_para_envio is True
    assert relatorio.campos_obrigatorios_ausentes == ()
    assert relatorio.erros == ()
    assert relatorio.alertas == ()


def test_relatorio_mapeia_valores_para_o_contrato_empregos() -> None:
    """Os valores normalizados devem usar a nomenclatura da API."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
    )

    campo_empresa = relatorio.buscar_campo("company.name")
    campo_id = relatorio.buscar_campo("externalJobPostingId")
    campo_operacao = relatorio.buscar_campo("jobPostingOperationType")
    campo_endereco = relatorio.buscar_campo("location.address")
    campo_cep = relatorio.buscar_campo("location.postalCode")
    campo_geolocalizacao = relatorio.buscar_campo("location.geolocation")
    campo_salario_minimo = relatorio.buscar_campo("salary.min")
    campo_salario_maximo = relatorio.buscar_campo("salary.max")
    campo_modalidade = relatorio.buscar_campo("workplaceTypes")
    campo_regime = relatorio.buscar_campo("employmentStatus")
    campo_senioridade = relatorio.buscar_campo("experienceLevel")

    assert campo_empresa is not None
    assert campo_empresa.valor == "Tecnologia Atlas"

    assert campo_id is not None
    assert campo_id.valor == "VAGA-001"

    assert campo_operacao is not None
    assert campo_operacao.valor == "CREATE"

    assert campo_endereco is not None
    assert campo_endereco.valor == "São Paulo, SP"

    assert campo_cep is not None
    assert campo_cep.valor == "01045-000"

    assert campo_geolocalizacao is not None
    assert campo_geolocalizacao.valor == ("-23.5440722,-46.6450811")

    assert campo_salario_minimo is not None
    assert campo_salario_minimo.valor == 6500

    assert campo_salario_maximo is not None
    assert campo_salario_maximo.valor == 8500

    assert campo_modalidade is not None
    assert campo_modalidade.valor == "Hybrid"

    assert campo_regime is not None
    assert campo_regime.valor == "FULL_TIME"

    assert campo_senioridade is not None
    assert campo_senioridade.valor == "MID_SENIOR_LEVEL"


def test_vaga_incompleta_lista_campos_ausentes() -> None:
    """Uma vaga incompleta deve mostrar os bloqueios principais."""

    empresa = Empresa(
        razao_social="Empresa Incompleta",
    )

    anuncio = AnuncioVaga(
        fonte=Fonte.OUTRA,
        id_externo="X" * 201,
        url="https://empresa-incompleta.example.com/vaga",
        titulo_original="Analista",
        hash_conteudo="d" * 64,
        referencia_bruta="outra/vaga.html",
    )

    vaga = VagaCanonica(
        empresa_id=empresa.id,
        titulo_normalizado="Analista",
        descricao_normalizada="Descrição curta.",
    )

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=None,
        anuncio=anuncio,
        vaga=vaga,
    )

    campos_com_erro = {problema.campo for problema in relatorio.erros}

    campos_obrigatorios_ausentes = {campo.campo for campo in relatorio.campos_obrigatorios_ausentes}

    assert relatorio.total_campos == 24
    assert relatorio.pronto_para_envio is False

    assert {
        "externalJobPostingId",
        "description",
        "location.address",
    }.issubset(campos_com_erro)

    assert {
        "externalJobPostingId",
        "description",
        "location.address",
    }.issubset(campos_obrigatorios_ausentes)

    # A ausência completa do recrutador não é um erro.
    assert "company.recruiter" not in campos_com_erro

    campos_com_alerta = {problema.campo for problema in relatorio.alertas}

    assert "workplaceTypes" in campos_com_alerta
    assert "employmentStatus" in campos_com_alerta
    assert "experienceLevel" in campos_com_alerta
    assert "salary" in campos_com_alerta


def test_catalogo_contem_os_24_campos_da_api() -> None:
    """O catálogo deve representar todo o JobRequest do Empregos."""

    nomes = [nome for nome, _obrigatorio in CAMPOS_API_EMPREGOS]

    assert len(nomes) == 24
    assert len(set(nomes)) == 24

    obrigatoriedade = dict(CAMPOS_API_EMPREGOS)

    campos_obrigatorios = {nome for nome, obrigatorio in obrigatoriedade.items() if obrigatorio}

    assert campos_obrigatorios == {
        "company.applyUrl",
        "company.name",
        "externalJobPostingId",
        "title",
        "description",
        "location.address",
    }


def test_descricao_da_empresa_ausente_nao_bloqueia_envio() -> None:
    """O bloco institucional do Empregos é permitido sem texto descritivo."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()
    empresa_sem_descricao = empresa.model_copy(update={"descricao": None})

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa_sem_descricao,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
    )

    campo_descricao = relatorio.buscar_campo("company.description")

    assert relatorio.pronto_para_envio is True
    assert campo_descricao is not None
    assert campo_descricao.obrigatorio is False
    assert campo_descricao.preenchido is False


def test_relatorio_calcula_preenchimento_e_campos_ausentes() -> None:
    """O relatório deve resumir a situação dos campos."""

    relatorio = RelatorioProntidao(
        campos=(
            CampoProntidao(
                campo="title",
                valor="Pessoa Desenvolvedora Python",
                obrigatorio=True,
                situacao=SituacaoCampoProntidao.EXTRAIDO,
            ),
            CampoProntidao(
                campo="company.applyUrl",
                valor=None,
                obrigatorio=True,
                situacao=SituacaoCampoProntidao.AUSENTE,
                mensagem="URL direta de candidatura não encontrada.",
            ),
            CampoProntidao(
                campo="jobPostingOperationType",
                valor="CREATE",
                obrigatorio=False,
                situacao=SituacaoCampoProntidao.GERADO,
            ),
        )
    )

    assert relatorio.total_campos == 3
    assert len(relatorio.campos_preenchidos) == 2
    assert relatorio.percentual_preenchimento == 66.67

    assert [campo.campo for campo in relatorio.campos_obrigatorios_ausentes] == [
        "company.applyUrl",
    ]

    assert relatorio.pronto_para_envio is False

    campo_titulo = relatorio.buscar_campo("title")

    assert campo_titulo is not None
    assert campo_titulo.valor == "Pessoa Desenvolvedora Python"
    assert campo_titulo.preenchido is True

    assert relatorio.buscar_campo("campo.inexistente") is None


def test_situacao_pode_ser_informada_pela_extracao() -> None:
    """A extração pode informar com precisão a origem do valor."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        situacoes_campos={
            "externalJobPostingId": (SituacaoCampoProntidao.GERADO),
        },
    )

    campo = relatorio.buscar_campo("externalJobPostingId")

    assert campo is not None
    assert campo.situacao == SituacaoCampoProntidao.GERADO


def test_status_emprego_oficial_e_preservado() -> None:
    """FULL_TIME vindo da fonte deve ser enviado sem virar CLT."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    # Simula exatamente o valor encontrado na vaga real.
    anuncio = anuncio.model_copy(
        update={
            "regime_original": "FULL_TIME",
        }
    )

    # A normalização interna usa OUTRO porque FULL_TIME não
    # significa necessariamente um contrato CLT.
    vaga = vaga.model_copy(
        update={
            "regime": RegimeContratacao.OUTRO,
        }
    )

    relatorio = avaliar_prontidao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
    )

    campo = relatorio.buscar_campo("employmentStatus")

    assert campo is not None
    assert campo.valor == "FULL_TIME"
    assert campo.situacao == SituacaoCampoProntidao.EXTRAIDO

    # Como o campo foi encontrado, não deve existir alerta sobre ele.
    campos_com_alerta = {problema.campo for problema in relatorio.alertas}

    assert "employmentStatus" not in campos_com_alerta
