"""Testes da preparação completa de uma vaga para o Empregos."""

from decimal import Decimal

import pytest

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.elegibilidade import CodigoBloqueioPublicacao
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    Fonte,
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
    StatusAnuncio,
    StatusPoliticaFonte,
)
from observatorio_vagas.domain.politica_fonte import PoliticaFonte
from observatorio_vagas.domain.recrutador import Recrutador
from observatorio_vagas.domain.vaga import SalarioNormalizado, VagaCanonica
from observatorio_vagas.integrations.empregos import (
    PayloadEmpregosInvalido,
    PublicacaoEmpregosBloqueada,
    preparar_publicacao_empregos,
)

# Esta política fictícia representa uma fonte que:
#
# - pode ser coletada;
# - pode ter suas vagas republicadas;
# - pertence ao domínio utilizado nos testes.
#
# Usamos uma constante porque praticamente todos os testes precisam
# partir de uma fonte aprovada.
POLITICA_APROVADA = PoliticaFonte(
    dominio="tecnologia-atlas.example.com",
    status=StatusPoliticaFonte.APROVADA,
    licenca_nome="CC BY 4.0",
    licenca_url="https://creativecommons.org/licenses/by/4.0/deed.pt-br",
    republicacao_permitida=True,
)


def criar_cenario_completo() -> tuple[
    Empresa,
    Recrutador,
    AnuncioVaga,
    VagaCanonica,
]:
    """Cria uma vaga fictícia com os obrigatórios preenchidos."""

    # A descrição institucional é obrigatória. CNPJ é opcional, enquanto a
    # URL de candidatura obrigatória pertence ao anúncio abaixo.
    empresa = Empresa(
        razao_social="Tecnologia Atlas S.A.",
        nome_fantasia="Tecnologia Atlas",
        cnpj="12.345.678/0001-90",
        descricao=("Empresa fictícia especializada em soluções de tecnologia."),
        setor="Tecnologia",
    )

    # Os campos do recrutador são opcionais, mas criamos um recrutador
    # completo para testar se eles chegam ao payload.
    recrutador = Recrutador(
        empresa_id=empresa.id,
        id_externo="RECRUTADOR-001",
        nome="Maria da Silva",
        email="maria@tecnologia-atlas.example.com",
    )

    # O anúncio representa os dados exatamente como foram publicados
    # na fonte externa.
    anuncio = AnuncioVaga(
        # Liga o anúncio ao alvo correto do catálogo.
        alvo_id="tecnologia_atlas",
        # Esta é uma página externa de carreiras.
        fonte=Fonte.PAGINA_CARREIRAS,
        id_externo="VAGA-001",
        # O domínio desta URL precisa corresponder ao domínio
        # presente em POLITICA_APROVADA.
        url=("https://tecnologia-atlas.example.com/carreiras/vaga-001"),
        url_candidatura=("https://candidaturas.example.com/vaga-001"),
        titulo_original="Pessoa Desenvolvedora Python",
        # ATIVO é um dos status que permitem publicação.
        status=StatusAnuncio.ATIVO,
        hash_conteudo="a" * 64,
        referencia_bruta="corpos/vaga-001.bin",
    )

    # O salário é opcional na API, mas está presente neste cenário.
    salario = SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        periodo=PeriodoSalario.MES,
        minimo=Decimal("6500"),
        maximo=Decimal("8500"),
    )

    # A vaga canônica contém os dados já normalizados.
    vaga = VagaCanonica(
        empresa_id=empresa.id,
        titulo_normalizado=("Pessoa Desenvolvedora Python"),
        descricao_normalizada=(
            "Buscamos uma pessoa desenvolvedora para construir "
            "aplicações Python, criar testes automatizados, "
            "revisar código e colaborar com o time de produto."
        ),
        endereco="São Paulo, SP",
        cidade="São Paulo",
        estado="SP",
        cep="01045-000",
        modalidade=ModalidadeTrabalho.HIBRIDO,
        regime=RegimeContratacao.CLT,
        senioridade=Senioridade.PLENO,
        salario=salario,
    )

    return empresa, recrutador, anuncio, vaga


def test_prepara_relatorio_e_payload_em_uma_operacao() -> None:
    """Uma vaga completa e autorizada deve produzir o payload."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    resultado = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=POLITICA_APROVADA,
    )

    # O resultado precisa passar pelas duas verificações:
    #
    # 1. campos obrigatórios;
    # 2. elegibilidade.
    assert resultado.pronto_para_envio is True
    assert resultado.relatorio.total_campos == 24
    assert resultado.elegibilidade.elegivel is True

    # Como tudo foi aprovado, o payload foi criado.
    assert resultado.payload is not None

    assert resultado.payload["company"]["name"] == "Tecnologia Atlas"

    assert resultado.payload["title"] == "Pessoa Desenvolvedora Python"


def test_acrescenta_credito_quando_a_fonte_exige_atribuicao() -> None:
    """Uma permissão com crédito precisa aparecer no conteúdo republicado."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()
    politica_com_atribuicao = PoliticaFonte(
        dominio="tecnologia-atlas.example.com",
        status=StatusPoliticaFonte.APROVADA,
        licenca_nome="CC BY 4.0",
        licenca_url="https://creativecommons.org/licenses/by/4.0/deed.pt-br",
        atribuicao_obrigatoria=True,
        nome_atribuicao="Tecnologia Atlas Carreiras",
        republicacao_permitida=True,
    )

    resultado = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica_com_atribuicao,
    )

    assert resultado.payload is not None
    assert resultado.credito_fonte == (
        "Fonte: Tecnologia Atlas Carreiras. Licença/permissão: CC BY 4.0. "
        "Origem: https://tecnologia-atlas.example.com/carreiras/vaga-001"
    )
    assert resultado.payload["description"].endswith(resultado.credito_fonte)
    assert resultado.proveniencia_fonte is not None
    assert resultado.proveniencia_fonte.para_documento() == {
        "nome_fonte": "Tecnologia Atlas Carreiras",
        "dominio": "tecnologia-atlas.example.com",
        "licenca_nome": "CC BY 4.0",
        "licenca_url": "https://creativecommons.org/licenses/by/4.0/deed.pt-br",
        "atribuicao_obrigatoria": True,
        "url_origem": "https://tecnologia-atlas.example.com/carreiras/vaga-001",
        "credito_aplicado": resultado.credito_fonte,
    }


def test_cnpj_ausente_continua_publicavel_com_url_de_candidatura() -> None:
    """CNPJ ausente não bloqueia uma vaga com candidatura direta."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    empresa_sem_cnpj = empresa.model_copy(
        update={
            "cnpj": None,
        }
    )

    resultado = preparar_publicacao_empregos(
        empresa=empresa_sem_cnpj,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=POLITICA_APROVADA,
    )

    assert resultado.pronto_para_envio is True
    assert resultado.elegibilidade.elegivel is True
    assert resultado.payload is not None
    assert "nationalRegister" not in resultado.payload["company"]


def test_url_de_candidatura_ausente_usa_url_de_origem_no_payload() -> None:
    """O dashboard usa a URL de origem se não houver link direto."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()
    anuncio_sem_url = anuncio.model_copy(update={"url_candidatura": None})

    resultado = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio_sem_url,
        vaga=vaga,
        politica_fonte=POLITICA_APROVADA,
    )

    assert resultado.pronto_para_envio is True
    assert resultado.elegibilidade.elegivel is True
    assert resultado.payload is not None
    assert resultado.payload["company"]["applyUrl"] == str(anuncio_sem_url.url)


def test_modo_bloqueante_levanta_erro_de_payload() -> None:
    """O publicador deve interromper uma vaga incompleta."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    # A descrição da vaga é obrigatória.
    vaga_sem_descricao = vaga.model_copy(
        update={
            "descricao_normalizada": None,
        }
    )

    # bloquear_se_incompleta=True representa o comportamento
    # que o futuro cliente HTTP utilizará antes de enviar.
    with pytest.raises(PayloadEmpregosInvalido) as captura:
        preparar_publicacao_empregos(
            empresa=empresa,
            recrutador=recrutador,
            anuncio=anuncio,
            vaga=vaga_sem_descricao,
            politica_fonte=POLITICA_APROVADA,
            bloquear_se_incompleta=True,
        )

    assert "description" in captura.value.campos


def test_repassa_campos_especificos_da_integracao() -> None:
    """Identificadores do Empregos devem chegar ao payload."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    resultado = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=POLITICA_APROVADA,
        company_id_empregos=("EMPRESA-EMPREGOS-123"),
        tracking_pixel_url=("https://tracking.example.com/vaga-001"),
    )

    assert resultado.payload is not None

    assert resultado.payload["company"]["companyId"] == "EMPRESA-EMPREGOS-123"

    assert resultado.payload["trackingPixelUrl"] == "https://tracking.example.com/vaga-001"


def test_fonte_somente_coleta_nao_libera_payload() -> None:
    """Uma fonte de análise interna não pode produzir payload."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    # O domínio é o mesmo e os dados estão completos.
    #
    # A única diferença é a política SOMENTE_COLETA.
    politica_somente_coleta = PoliticaFonte(
        dominio="tecnologia-atlas.example.com",
        status=StatusPoliticaFonte.SOMENTE_COLETA,
    )

    resultado = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga,
        politica_fonte=politica_somente_coleta,
    )

    # Os campos obrigatórios estão completos.
    assert resultado.relatorio.pronto_para_envio is True

    # Mesmo assim, a fonte não permite republicação.
    assert resultado.elegibilidade.elegivel is False
    assert resultado.pronto_para_envio is False
    assert resultado.payload is None

    assert any(
        bloqueio.codigo.value == "fonte_sem_permissao" for bloqueio in resultado.motivos_bloqueio
    )


def test_vaga_internacional_nao_gera_payload() -> None:
    """A preparação nunca exporta uma vaga localizada fora do Brasil."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()
    vaga_internacional = vaga.model_copy(update={"pais": "GB"})

    resultado = preparar_publicacao_empregos(
        empresa=empresa,
        recrutador=recrutador,
        anuncio=anuncio,
        vaga=vaga_internacional,
        politica_fonte=POLITICA_APROVADA,
    )

    assert resultado.pronto_para_envio is False
    assert resultado.payload is None
    assert any(
        bloqueio.codigo is CodigoBloqueioPublicacao.LOCALIZACAO_FORA_DO_BRASIL
        for bloqueio in resultado.motivos_bloqueio
    )


def test_modo_bloqueante_rejeita_politica_sem_permissao() -> None:
    """O modo de publicação deve levantar um erro de política."""

    empresa, recrutador, anuncio, vaga = criar_cenario_completo()

    politica_somente_coleta = PoliticaFonte(
        dominio="tecnologia-atlas.example.com",
        status=StatusPoliticaFonte.SOMENTE_COLETA,
    )

    # Agora os campos estão completos.
    #
    # Portanto, esperamos um erro de política e não um erro
    # relacionado ao formato do payload.
    with pytest.raises(PublicacaoEmpregosBloqueada) as captura:
        preparar_publicacao_empregos(
            empresa=empresa,
            recrutador=recrutador,
            anuncio=anuncio,
            vaga=vaga,
            politica_fonte=politica_somente_coleta,
            bloquear_se_incompleta=True,
        )

    assert any(
        bloqueio.codigo.value == "fonte_sem_permissao" for bloqueio in captura.value.bloqueios
    )
