"""Leitura completa: casos reais e montados por plataforma (especificação, seção 4)."""

import json
from pathlib import Path

from observatorio_vagas.extraction.leitura.camadas import InventarioPagina, montar_inventario
from observatorio_vagas.extraction.leitura.interpretacao import (
    ler_vaga,
    limpar_texto,
    validar_apply_url,
    validar_endereco,
)

DADOS = Path(__file__).parent / "dados"


def test_abler_340518_le_salario_modalidade_e_vinculo():
    atributos = json.loads((DADOS / "abler_340518.json").read_text(encoding="utf-8"))
    leitura = ler_vaga(
        InventarioPagina(url=atributos["full_url"]),
        titulo=atributos["title"],
        id_externo="abler-340518",
        url_vaga=atributos["full_url"],
        atributos_plataforma=atributos,
    )
    campos = leitura.campos

    assert campos["salary"] == {"min": 2000}
    assert campos["workplaceTypes"] == "On-site"
    assert campos["employmentStatus"] == "FULL_TIME"
    assert campos["externalJobPostingId"] == "abler-340518"
    assert campos["title"] == "Assistente de Marketing"
    assert "Requisitos" in campos["description"]
    # Nunca a página de login devolvida em links.apply_to_url.
    assert "sign-in" not in campos["company"]["applyUrl"]
    assert leitura.diagnostico["campos"]["salary"]["origem"] == "plataforma.salary"
    assert leitura.diagnostico["campos"]["expireAt"]["vazio"] is True


QUICKIN = """<html><head>
<script type="application/ld+json">{"@type":"JobPosting","title":"Analista","datePosted":"2026-09-01",
"validThrough":"2026-11-30","hiringOrganization":{"name":"ACME"}}</script></head><body>
<h1>Analista</h1><a href="/acme/apply?job_id=6ab168398b97c700128d08f4">Apply</a>
<script>window.__NUXT__=(function(a,b){return {data:[{job:{_id:"6ab168398b97c700128d08f4",
title:"Analista",description:"\u003cp\u003eAtuar com fechamento contábil e conciliações mensais da carteira de clientes do escritório.\u003c\u002fp\u003e",
requirements:"\u003cp\u003eFormação em Ciências Contábeis e experiência com rotinas fiscais.\u003c\u002fp\u003e",
workplace_type:a,city:b,region:"SC"}}]}}("hybrid","Timbó"));</script></body></html>"""


def test_quickin_le_estado_nuxt_e_recusa_expiracao_calculada():
    url = "https://jobs.quickin.io/acme/jobs/6ab168398b97c700128d08f4"
    inv = montar_inventario(QUICKIN.encode(), url=url)
    leitura = ler_vaga(
        inv,
        titulo="Analista",
        id_externo="6ab168398b97c700128d08f4",
        url_vaga=url,
        url_candidatura_html="/acme/apply?job_id=6ab168398b97c700128d08f4",
    )
    campos = leitura.campos

    assert campos["workplaceTypes"] == "Hybrid"
    assert "Requisitos" in campos["description"]
    assert "job_id=6ab168398b97c700128d08f4" in campos["company"]["applyUrl"]
    assert campos["location"]["address"] == "Timbó, SC"
    assert "expireAt" not in campos
    assert "90 dias" in leitura.diagnostico["campos"]["expireAt"]["motivo"]


LEVER = """<html><body><div class="posting-headline"><h2>Data Engineer</h2>
<div class="posting-categories">
<div class="posting-category location">São Paulo, SP</div>
<div class="posting-category department">Data /</div>
<div class="posting-category commitment">Full Time /</div>
<div class="posting-category workplaceTypes">Remote</div></div></div>
<div class="section"><p>Build and maintain data pipelines for analytics products across teams and clients.</p></div>
</body></html>"""


def test_lever_le_commitment_e_workplace_do_cabecalho():
    url = "https://jobs.lever.co/acme/1a2b3c4d-0000-1111-2222-333344445555"
    leitura = ler_vaga(
        montar_inventario(LEVER.encode(), url=url),
        titulo="Data Engineer",
        id_externo="lever-1a2b3c4d",
        url_vaga=url,
    )

    assert leitura.campos["workplaceTypes"] == "Remote"
    assert leitura.campos["employmentStatus"] == "FULL_TIME"
    assert {"origem": "cabecalho", "rotulo": "department", "valor": "Data"} in leitura.diagnostico[
        "nao_mapeado"
    ]


SITE = """<html><body><nav>Menu Vagas Contato</nav>
<h1>Assistente Administrativo</h1>
<h2>Sobre a empresa</h2><p>Somos uma distribuidora regional com 30 anos de mercado.</p>
<h2>Atividades</h2><p>Emitir notas fiscais, controlar pagamentos e organizar documentos do setor.</p>
<h2>Requisitos</h2><p>Ensino médio completo e experiência com pacote Office.</p>
<h2>Remuneração</h2><p>Salário: R$ 2.200,00 mensal</p>
<p>Aceitar cookies</p>
<a href="/vagas/assistente-administrativo-123/candidatar">Candidatar-se</a>
<footer>Distribuidora XPTO LTDA - CNPJ 11.222.333/0001-81 - Rua das Flores, 10 - CEP 01234-567</footer>
</body></html>"""


def test_site_proprio_le_secoes_rodape_e_remove_interface():
    url = "https://www.xpto.com.br/vagas/assistente-administrativo-123"
    leitura = ler_vaga(
        montar_inventario(SITE.encode(), url=url),
        titulo="Assistente Administrativo",
        id_externo="xpto-123",
        url_vaga=url,
        url_candidatura_html="/vagas/assistente-administrativo-123/candidatar",
    )
    campos = leitura.campos

    assert campos["company"]["description"].startswith("Somos uma distribuidora")
    assert campos["company"]["nationalRegister"] == "11222333000181"
    assert campos["location"]["postalCode"] == "01234-567"
    assert campos["salary"] == {"min": 2200}
    assert "cookies" not in campos["description"].lower()
    assert campos["company"]["applyUrl"].endswith("/candidatar")


def test_validacoes():
    vaga = "https://site.com.br/vagas/analista-55"
    assert validar_apply_url("https://site.com.br/login?next=x", url_vaga=vaga, id_vaga="55")
    assert validar_apply_url("https://site.com.br/sitemap.xml", url_vaga=vaga, id_vaga="55")
    assert validar_apply_url("https://site.com.br/", url_vaga=vaga, id_vaga="55")
    assert validar_apply_url("https://site.com.br/trabalhe-conosco", url_vaga=vaga, id_vaga="55")
    assert (
        validar_apply_url(
            "https://site.com.br/vagas/analista-de-cadastro-55", url_vaga=vaga, id_vaga="55"
        )
        is None
    )
    assert validar_endereco("o Paulo, SP")
    assert validar_endereco("São Paulo, SP") is None
    assert limpar_texto("Linha 1\nLinha &amp; 2") == "Linha 1\nLinha & 2"


def test_pagina_com_varias_vagas_nao_mistura_dados():
    """Achado da revisão: cada vaga usa só o seu JobPosting."""

    html = """<html><body>
<script type="application/ld+json">[
{"@type":"JobPosting","identifier":"111","title":"Vaga A","url":"https://x.com.br/vagas/111",
 "hiringOrganization":{"name":"Empresa A"},"description":"Descrição completa da vaga A com atividades, responsabilidades do dia a dia e requisitos obrigatórios para o cargo na empresa."},
{"@type":"JobPosting","identifier":"222","title":"Vaga B","url":"https://x.com.br/vagas/222",
 "hiringOrganization":{"name":"Empresa B"},"description":"Descrição completa da vaga B com atividades, responsabilidades do dia a dia e requisitos obrigatórios para o cargo na empresa."}
]</script></body></html>"""
    inv = montar_inventario(html.encode(), url="https://x.com.br/vagas")
    documento = {"identifier": "111", "url": "https://x.com.br/vagas/111", "title": "Vaga A"}
    leitura = ler_vaga(
        inv,
        titulo="Vaga A",
        id_externo="111",
        url_vaga="https://x.com.br/vagas",
        documento=documento,
        varias_vagas_na_pagina=True,
    )

    assert leitura.campos["company"]["name"] == "Empresa A"
    assert leitura.campos["company"]["applyUrl"].endswith("/111")
    assert "vaga A" in leitura.campos["description"]


def test_cnpj_invalido_e_interface_estrita():
    from observatorio_vagas.extraction.leitura.interpretacao import cnpj_valido, limpar_descricao

    assert cnpj_valido("11.222.333/0001-81")
    assert not cnpj_valido("12.345.678/0001-90")
    assert not cnpj_valido("140718")
    assert (
        limpar_descricao("Elaborar o menu do restaurante\nMenu\n✕")
        == "Elaborar o menu do restaurante"
    )
