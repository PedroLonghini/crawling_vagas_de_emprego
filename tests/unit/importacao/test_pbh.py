"""Testes do importador das vagas abertas da PBH."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from observatorio_vagas.domain.enums import StatusAnuncio
from observatorio_vagas.importacao.pbh import (
    ALVO_ID_PBH,
    ATRIBUICAO_PBH,
    ErroArquivoVagasPBH,
    importar_vagas_pbh_csv,
)

FIXTURE = Path("tests/fixtures/pbh/vagas_ofertadas.csv")
OBSERVADO_EM = datetime(2026, 8, 31, 12, tzinfo=UTC)


def test_importa_linhas_validas_e_isola_problemas() -> None:
    """Uma linha ruim ou repetida não deve derrubar o lote inteiro."""

    resultado = importar_vagas_pbh_csv(
        FIXTURE.read_bytes(),
        referencia_bruta=FIXTURE.as_posix(),
        observado_em=OBSERVADO_EM,
    )

    assert resultado.linhas_lidas == 4
    assert len(resultado.anuncios) == 2
    assert len(resultado.falhas) == 1
    assert resultado.falhas[0].numero_linha == 4
    assert resultado.duplicados == 1
    assert resultado.encerrados == 1


def test_preserva_campos_atribuicao_e_trava_temporal() -> None:
    """O anúncio deve ser auditável e nunca inventar expiração oficial."""

    resultado = importar_vagas_pbh_csv(
        FIXTURE.read_bytes(),
        referencia_bruta=FIXTURE.as_posix(),
        observado_em=OBSERVADO_EM,
    )
    anuncio = resultado.anuncios[0]
    metadados = anuncio.campos_estruturados["_observatorio"]

    assert anuncio.alvo_id == ALVO_ID_PBH
    assert anuncio.id_externo == "pbh-sine:9000001"
    assert anuncio.titulo_original == "Técnico de suporte"
    assert anuncio.id_empresa_na_fonte == "12345678000190"
    assert anuncio.numero_vagas_original == 2
    assert anuncio.publicado_em == date(2026, 8, 26)
    assert anuncio.expira_em == date(2026, 9, 25)
    assert anuncio.status is StatusAnuncio.DESCOBERTO
    assert ATRIBUICAO_PBH in anuncio.descricao_original
    assert metadados["expiracao_inferida"] is True
    assert metadados["validade_operacional_dias"] == 30
    assert anuncio.referencia_bruta.endswith("#linha=2")


def test_estrutura_salario_local_e_requisitos_para_normalizacao() -> None:
    """Os campos do CSV devem seguir o formato consumido pelo normalizador."""

    resultado = importar_vagas_pbh_csv(
        FIXTURE.read_bytes(),
        referencia_bruta="vagas.csv",
        observado_em=OBSERVADO_EM,
    )
    anuncio = resultado.anuncios[0]
    documento = anuncio.campos_estruturados

    assert documento["baseSalary"]["currency"] == "BRL"
    assert documento["baseSalary"]["value"]["value"] == ("R$ 2.050,00 + benefícios")
    assert documento["jobLocation"]["address"]["addressLocality"] == "Centro/BH"
    assert documento["experienceRequirements"] == "6 meses"
    assert documento["educationRequirements"] == "Ensino Médio completo"
    assert "baseSalary" not in resultado.anuncios[1].campos_estruturados


def test_hash_e_identidade_sao_deterministicos() -> None:
    """Reprocessar o mesmo arquivo não pode criar anúncios diferentes."""

    argumentos = {
        "referencia_bruta": "vagas.csv",
        "observado_em": OBSERVADO_EM,
    }
    primeiro = importar_vagas_pbh_csv(FIXTURE.read_bytes(), **argumentos)
    segundo = importar_vagas_pbh_csv(FIXTURE.read_bytes(), **argumentos)

    assert primeiro.anuncios[0].id_externo == segundo.anuncios[0].id_externo
    assert primeiro.anuncios[0].hash_conteudo == segundo.anuncios[0].hash_conteudo


def test_rejeita_arquivo_sem_colunas_obrigatorias() -> None:
    """Um arquivo diferente não pode ser interpretado silenciosamente."""

    with pytest.raises(ErroArquivoVagasPBH, match="colunas obrigatórias ausentes"):
        importar_vagas_pbh_csv(
            b"DATA;CNPJ;OCUPACAO\n01/01/2026;123;Teste\n",
            referencia_bruta="invalido.csv",
            observado_em=OBSERVADO_EM,
        )


def test_aceita_csv_windows_1252() -> None:
    """Planilhas salvas pelo Excel não devem perder acentos."""

    conteudo = (
        "DATA;CNPJ;IDENTIFICACAO;OCUPACAO;LOCAL DE TRABALHO;No DE VAGAS;"
        "EXPERIENCIA;ESCOLARIDADE;REMUNERACAO\n"
        "28/08/2026;12.345.678/0001-90;42;Técnico;Centro/BH;1;"
        "Não exigida;Ensino Médio;A combinar\n"
    ).encode("cp1252")

    resultado = importar_vagas_pbh_csv(
        conteudo,
        referencia_bruta="excel.csv",
        observado_em=OBSERVADO_EM,
    )

    assert resultado.anuncios[0].titulo_original == "Técnico"
