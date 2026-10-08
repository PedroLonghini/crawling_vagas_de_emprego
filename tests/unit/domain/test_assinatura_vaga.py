"""Assinatura de conteúdo: mesma vaga em sites diferentes."""

from observatorio_vagas.domain.assinatura_vaga import (
    assinatura_de_payload,
    calcular_assinatura_conteudo,
)


def assinatura(
    titulo="Analista de Dados", empresa="Empresa X", local="São Paulo, SP", descricao="Texto."
):
    return calcular_assinatura_conteudo(
        titulo=titulo, empresa=empresa, local=local, descricao=descricao
    )


def test_diferencas_que_nao_mudam_a_vaga_dao_a_mesma_assinatura():
    base = assinatura(titulo="Analista – Dados (Pleno)")
    assert assinatura(titulo="Analista &#8211; Dados (Pleno)") == base
    assert assinatura(titulo="Analista &amp;#8211; Dados (Pleno)") == base
    assert assinatura(titulo="ANALISTA – DADOS  PLENO") == base
    assert assinatura(titulo="Analista – Dados (Pleno)", local="SP - Sao Paulo / Brasil") == base
    assert (
        assinatura(titulo="Analista – Dados (Pleno)", descricao="Texto.\n\nFonte: outro site")
        == base
    )


def test_campos_diferentes_dao_assinaturas_diferentes():
    base = assinatura()
    assert assinatura(empresa="Empresa Y") != base
    assert assinatura(local="Campinas, SP") != base
    assert assinatura(titulo="Analista de Dados Sênior") != base
    assert assinatura(descricao="Outro texto.") != base


def test_cargos_com_mais_e_cerquilha_nao_se_confundem():
    assert assinatura(titulo="Desenvolvedor C++") != assinatura(titulo="Desenvolvedor C#")


def test_mesma_vaga_depende_do_site():
    from observatorio_vagas.domain.assinatura_vaga import mesma_vaga

    def comparar(dominio_a, completa_a, dominio_b, completa_b):
        return mesma_vaga(
            dominio_a=dominio_a,
            assinatura_completa_a=completa_a,
            dominio_b=dominio_b,
            assinatura_completa_b=completa_b,
        )

    assert comparar("a.com.br", "1", "b.com.br", "2") is True  # outro site: começo basta
    assert comparar("a.com.br", "1", "www.A.com.br", "2") is False  # mesmo site, texto diferente
    assert comparar("a.com.br", "1", "a.com.br", "1") is True  # mesmo site, cópia idêntica
    assert comparar(None, None, "a.com.br", "1") is True  # site desconhecido: na dúvida, mesma


def test_so_o_comeco_da_descricao_conta():
    comeco = "a" * 300
    assert assinatura(descricao=comeco + " rodapé do site A") == assinatura(
        descricao=comeco + " candidate-se pelo site B"
    )


def test_payload_incompleto_nao_tem_assinatura():
    completo = {
        "title": "Analista",
        "company": {"name": "X"},
        "location": {"address": "SP"},
        "description": "Texto",
    }
    assert assinatura_de_payload(completo) is not None
    assert assinatura_de_payload({**completo, "title": " "}) is None
    assert assinatura_de_payload({**completo, "company": {}}) is None
    assert assinatura_de_payload(None) is None
