"""Regras da seção 3 da especificação: interpretação de texto livre."""

from datetime import date
from decimal import Decimal

import pytest

from observatorio_vagas.extraction.leitura.texto_livre import (
    Indefinido,
    interpretar_cep,
    interpretar_cnpj,
    interpretar_email_candidatura,
    interpretar_expiracao,
    interpretar_modalidade,
    interpretar_nivel,
    interpretar_recrutador,
    interpretar_salario,
    interpretar_vinculo,
)


@pytest.mark.parametrize(
    ("titulo", "texto", "rotulo", "esperado"),
    [
        ("Analista", "", "Hybrid", "HYBRID"),
        ("Analista", "", "On-site", "ON_SITE"),
        ("Desenvolvedor (Híbrido)", "", None, "HYBRID"),
        ("Vendedor - Home Office", "", None, "REMOTE"),
        ("Analista", "Modalidade: Presencial em São Paulo", None, "ON_SITE"),
        ("Analista", "Trabalho 100% remoto, de qualquer lugar.", None, "REMOTE"),
        ("Analista", "3x presencial e 2 home office por semana", None, "HYBRID"),
        ("Analista", "presencial 2x por semana no escritório", None, "HYBRID"),
        ("Analista", "Somos híbridos: presencial e remoto.", None, "HYBRID"),
    ],
)
def test_modalidade(titulo, texto, rotulo, esperado):
    assert interpretar_modalidade(titulo, texto, rotulo).valor == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "Prestar suporte remoto aos clientes",
        "Realizar atendimento presencial no balcão",
        "Visitas presenciais a clientes da região",
        "Benefícios: auxílio home office",
        "Experiência com nuvem híbrida",
        "Atuar presencialmente nas lojas",
    ],
)
def test_modalidade_ignora_falsos_positivos(texto):
    assert interpretar_modalidade("Analista", texto) is None


def test_modalidade_conflito_fica_vazia_com_motivo():
    resultado = interpretar_modalidade(
        "Analista", "Presencial em São Paulo ou remoto para outras cidades."
    )
    assert isinstance(resultado, Indefinido)
    assert "conflito" in resultado.motivo


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Estágio em Marketing", "INTERNSHIP"),
        ("Contrato temporário de 3 meses", "TEMPORARY"),
        ("Contratação PJ", "CONTRACT"),
        ("Regime CLT", "FULL_TIME"),
        ("Full Time", "FULL_TIME"),
    ],
)
def test_vinculo(texto, esperado):
    assert interpretar_vinculo(texto, "teste").valor == esperado


def test_vinculo_sem_enum_registra_motivo():
    resultado = interpretar_vinculo("Programa Jovem Aprendiz", "teste")
    assert isinstance(resultado, Indefinido)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Estagiário de Vendas", "INTERNSHIP"),
        ("Analista Júnior", "ENTRY_LEVEL"),
        ("Programa Trainee 2026", "ENTRY_LEVEL"),
        ("Desenvolvedor Pleno", "MID_SENIOR_LEVEL"),
        ("Engenheiro Sênior", "MID_SENIOR_LEVEL"),
        ("Diretor Comercial", "DIRECTOR"),
    ],
)
def test_nivel(texto, esperado):
    assert interpretar_nivel(texto, "titulo").valor == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "Gerente de Loja",
        "Sales Manager",
        "Head de Produto",
        "Coordenador Financeiro",
        "Assistente Administrativo",
        "Médico Interno",
        "Desenvolvedor PL/SQL",
        "Analista Júnior ou Pleno",
    ],
)
def test_nivel_nao_confunde_cargo_com_nivel(texto):
    assert interpretar_nivel(texto, "titulo") is None


def test_salario_faixa_e_valor_unico():
    faixa = interpretar_salario("Salário: R$ 2.501 a R$ 3.500 mensal", "corpo").valor
    assert (faixa.minimo, faixa.maximo, faixa.periodicidade) == (
        Decimal(2501),
        Decimal(3500),
        "MONTH",
    )

    unico = interpretar_salario("Remuneração: R$ 1.800,00", "corpo").valor
    assert (unico.minimo, unico.maximo) == (Decimal("1800.00"), None)

    assert interpretar_salario("Bolsa auxílio de R$ 1.200", "corpo").valor.minimo == Decimal(1200)


@pytest.mark.parametrize(
    "texto",
    [
        "Salário: a combinar",
        "Remuneração compatível com o mercado + bonificação",
        "Salário + Bonificação",
    ],
)
def test_salario_vago_fica_vazio(texto):
    resultado = interpretar_salario(texto, "corpo")
    assert resultado is None or isinstance(resultado, Indefinido)


def test_salario_ignora_beneficio():
    assert interpretar_salario("Benefícios: VR R$ 30 por dia", "corpo") is None


def test_cnpj_cep_email_recrutador():
    texto = (
        "Empresa XPTO LTDA - CNPJ 12.345.678/0001-90. Endereço: Rua das Flores, 100, CEP 01234-567. "
        "Envie seu currículo para vagas@xpto.com.br. Recrutadora: Maria Souza"
    )
    assert interpretar_cnpj(texto, "rodape").valor == "12345678000190"
    assert interpretar_cep(texto, "corpo").valor == "01234-567"
    assert interpretar_email_candidatura(texto, "corpo").valor == "vagas@xpto.com.br"
    assert interpretar_recrutador(texto, "corpo").valor == "Maria Souza"


def test_cep_exige_contexto_de_endereco():
    assert interpretar_cep("Código da vaga 12345678", "corpo") is None


def test_expiracao():
    hoje = date(2026, 10, 1)
    assert interpretar_expiracao(
        "Inscrições até 15/10/2026", "corpo", referencia=hoje
    ).valor == date(2026, 10, 15)
    assert interpretar_expiracao(
        "Vaga válida até 20 de novembro", "corpo", referencia=hoje
    ).valor == date(2026, 11, 20)
    assert interpretar_expiracao("Publicada em 01/10/2026", "corpo", referencia=hoje) is None


def _local(texto: str) -> str | None:
    from observatorio_vagas.extraction.leitura.texto_livre import interpretar_localizacao

    leitura = interpretar_localizacao(texto, "descricao")
    return leitura.valor if leitura else None


def test_local_com_rotulo_na_mesma_linha() -> None:
    assert _local("Cargo: Operador\nLocal: Camaçari - BA\nTurno: noite") == "Camaçari, BA"
    assert _local("Localização: Porto Alegre/RS") == "Porto Alegre, RS"
    assert _local("Cidade: Curitiba") == "Curitiba"


def test_local_em_titulo_sozinho_seguido_do_valor() -> None:
    assert (
        _local("Local\n\nCamaçari - BA\n\nRemoto\n\nResponsabilidades\n\n- Operar")
        == "Camaçari, BA"
    )


def test_local_cidade_uf_no_inicio_do_texto() -> None:
    assert _local("Vaga de analista em São Paulo - SP, com benefícios") == "São Paulo, SP"
    assert _local("Atuação em Sapucaia do Sul/RS") == "Sapucaia do Sul, RS"


def test_titulo_em_maiusculas_colado_na_cidade_fica_de_fora() -> None:
    assert _local("AUXILIAR DE TELECOMUNICAÇÕES Panambi - RS") == "Panambi, RS"
    assert _local("SÃO PAULO - SP") == "SÃO PAULO, SP"


def test_nao_confunde_modalidade_nem_sigla_solta_com_local() -> None:
    assert _local("Local: Remoto\nRequisitos: inglês") is None
    assert _local("Conhecimento em TI SP e SQL, atender a LGPD e CLT PA") is None
    assert _local("Sem nenhuma pista de local nesta vaga") is None
    assert _local("") is None


def test_rotulo_no_meio_da_linha_e_valor_cortado_no_proximo_rotulo() -> None:
    assert _local("Estágio Presencial Cidade: Presidente Prudente") == "Presidente Prudente"
    assert _local("Vaga Cidade: Presidente Prudente Bolsa: R$ 1.100") == "Presidente Prudente"


def test_cidade_no_titulo() -> None:
    from observatorio_vagas.extraction.leitura.texto_livre import cidade_do_titulo

    def cidade(titulo: str) -> str | None:
        leitura = cidade_do_titulo(titulo, "titulo")
        return leitura.valor if leitura else None

    assert cidade("Vaga: Vendedor em Recife") == "Recife"
    assert cidade("Analista em Campinas - SP") == "Campinas, SP"
    assert cidade("Operador em Sapucaia do Sul/RS (temporário)") == "Sapucaia do Sul, RS"
    assert cidade("Analista em Tecnologia") is None
    assert cidade("Vendedor em Loja de Shopping") is None
