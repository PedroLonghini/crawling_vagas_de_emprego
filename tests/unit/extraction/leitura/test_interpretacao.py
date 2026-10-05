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


DESCRICAO = (
    "Atividades do dia a dia, responsabilidades da função e requisitos obrigatórios para o cargo."
)


def _pagina(*postings):
    html = f'<html><body><script type="application/ld+json">{json.dumps(list(postings))}</script></body></html>'
    return montar_inventario(html.encode(), url="https://x.com.br/vagas")


def test_listagem_com_um_jobposting_alheio_nao_empresta_dados():
    """Revisão 2, caso 1a: um JobPosting só, mas de outra vaga da listagem."""

    inv = _pagina(
        {
            "@type": "JobPosting",
            "identifier": "A1",
            "title": "Vaga A",
            "hiringOrganization": {"name": "Empresa A"},
            "validThrough": "2026-01-01",
            "description": DESCRICAO,
        }
    )
    leitura = ler_vaga(
        inv,
        titulo="Vaga Z",
        id_externo="Z9",
        url_vaga="https://x.com.br/vagas",
        documento={"identifier": "Z9", "title": "Vaga Z", "description": DESCRICAO},
        varias_vagas_na_pagina=True,
    )

    assert leitura.campos["company"].get("name") != "Empresa A"
    assert "expireAt" not in leitura.campos


def test_mesma_url_em_varios_jobpostings_casa_pelo_identificador():
    """Revisão 2, caso 1b: todos com a URL da listagem, identificadores diferentes."""

    comum = {"@type": "JobPosting", "url": "https://x.com.br/vagas", "description": DESCRICAO}
    inv = _pagina(
        {**comum, "identifier": "1", "title": "A", "hiringOrganization": {"name": "Empresa A"}},
        {**comum, "identifier": "2", "title": "B", "hiringOrganization": {"name": "Empresa B"}},
    )
    leitura = ler_vaga(
        inv,
        titulo="B",
        id_externo="2",
        url_vaga="https://x.com.br/vagas",
        documento={"identifier": "2", "url": "https://x.com.br/vagas", "title": "B"},
        varias_vagas_na_pagina=True,
    )

    assert leitura.campos["company"]["name"] == "Empresa B"


def test_expiracao_vem_do_documento_em_fonte_json():
    """Revisão 2, caso 2: Workday/CKAN trazem validThrough no documento, não no HTML."""

    leitura = ler_vaga(
        InventarioPagina(url="https://x.myworkdayjobs.com/wday/cxs/x/y/job/z"),
        titulo="Analista",
        id_externo="workday-x-1",
        url_vaga="https://x.myworkdayjobs.com/wday/cxs/x/y/job/z",
        documento={"validThrough": "2026-10-30", "description": DESCRICAO},
    )

    assert leitura.campos["expireAt"] == "2026-10-30"


PAGINA_COM_META_RESUMIDA = """<html><head>
<meta name="description" content="Operador de máquina em Pindamonhangaba. A COMDARPE é uma empresa... Saiba mais.">
</head><body><nav>Início | Vagas</nav>
<h1>Operador de Máquina - Motoniveladora</h1>
<p>Descrição de atividades:</p>
<ul><li>Opera máquinas motoniveladoras em obras de terraplanagem.</li>
<li>Realiza manutenção preventiva e inspeção diária do equipamento.</li>
<li>Segue normas de segurança e registra as horas trabalhadas.</li></ul>
<p>Requisitos: experiência comprovada e CNH categoria D.</p>
<p>Cidade: Pindamonhangaba - SP</p>
<p>Candidate-se pelo WhatsApp</p>
<p>Vagas relacionadas: Ajudante geral</p>
</body></html>"""


def test_resumo_da_meta_da_lugar_ao_bloco_da_vaga_no_corpo():
    url = "https://empresa.example/trabalhe-conosco/operador-de-maquina"
    leitura = ler_vaga(
        montar_inventario(PAGINA_COM_META_RESUMIDA.encode(), url=url),
        titulo="Operador de Máquina - Motoniveladora",
        id_externo="x1",
        url_vaga=url,
        documento={
            "description": "Operador de máquina em Pindamonhangaba. A COMDARPE é uma "
            "empresa... Saiba mais."
        },
    )

    descricao = leitura.campos["description"]
    assert "Realiza manutenção preventiva" in descricao
    assert "Requisitos" in descricao
    assert "Saiba mais" not in descricao
    assert "Vagas relacionadas" not in descricao  # parou no fim do bloco
    assert leitura.diagnostico["campos"]["description"]["origem"] == "corpo.bloco_da_vaga"


def test_descricao_completa_do_documento_continua_valendo():
    url = "https://empresa.example/vagas/2"
    completa = "Atividades: atender clientes e organizar a agenda da equipe comercial. " * 6
    leitura = ler_vaga(
        montar_inventario(PAGINA_COM_META_RESUMIDA.encode(), url=url),
        titulo="Operador de Máquina - Motoniveladora",
        id_externo="x2",
        url_vaga=url,
        documento={"description": completa},
    )

    assert leitura.diagnostico["campos"]["description"]["origem"] == "documento.description"


def test_lista_de_cards_no_corpo_nao_vira_descricao():
    from observatorio_vagas.extraction.leitura.interpretacao import bloco_da_vaga

    cards = ["Presencial", "Efetivo/CLT", "R$ 6.000,00 por mês", "Home-Office"] * 10
    corpo = "\n".join(["Desenvolvedor .NET", *cards])

    assert bloco_da_vaga(corpo, "Desenvolvedor .NET") is None


FRASE = "Realizar inspeções visuais e dimensionais em produtos acabados conforme as normas."


def test_bloco_comeca_no_titulo_seguido_de_texto_e_pula_autor_e_titulo_repetido():
    from observatorio_vagas.extraction.leitura.interpretacao import bloco_da_vaga

    corpo = "\n".join(
        [
            "Início > Vagas > Inspetor de Qualidade",
            "Inspetor de Qualidade",
            "Por: Admin - 22 de Setembro de 2026",
            FRASE,
            FRASE,
            "Candidate-se",
            "Formulário",
            "Inspetor de Qualidade",
        ]
    )

    bloco = bloco_da_vaga(corpo, "Inspetor de Qualidade")

    assert bloco is not None and bloco.startswith("Realizar inspeções")
    assert "Por: Admin" not in bloco and "Formulário" not in bloco


def test_titulo_curto_ou_generico_nao_gera_bloco():
    from observatorio_vagas.extraction.leitura.interpretacao import bloco_da_vaga

    corpo = "\n".join(["Tag: vagas", FRASE, FRASE, FRASE])

    assert bloco_da_vaga(corpo, "Tag:") is None
    assert bloco_da_vaga(corpo, "Vagas de emprego Vendedor em São Paulo") is None


def test_listagem_sem_marcador_de_fim_e_enorme_nao_gera_bloco():
    from observatorio_vagas.extraction.leitura.interpretacao import bloco_da_vaga

    corpo = "\n".join(["Analista de Marketing Digital", *([FRASE] * 200)])

    assert bloco_da_vaga(corpo, "Analista de Marketing Digital") is None


def test_frase_que_comeca_com_candidatos_nao_encerra_o_bloco():
    from observatorio_vagas.extraction.leitura.interpretacao import bloco_da_vaga

    contato = "Candidatos interessados devem enviar currículo para rh@empresa.example até sexta."
    corpo = "\n".join(["Analista de Marketing Digital", FRASE, contato, FRASE, "Compartilhar"])

    bloco = bloco_da_vaga(corpo, "Analista de Marketing Digital")

    assert bloco is not None and "rh@empresa.example" in bloco


def test_endereco_com_trecho_de_codigo_e_recusado():
    assert validar_endereco("window.location.href,page:`")
    assert validar_endereco("/^(?:about")
    assert validar_endereco('<"')
    assert validar_endereco("Presidente Prudente, SP") is None
    # Ponto e vírgula e abreviação são de endereço de verdade.
    assert validar_endereco("Av. Paulista, 1000 (Bela Vista); São Paulo") is None
    assert validar_endereco("Atuar no centro. Ter carro próprio")


def _ler_local(json_ld: dict, titulo: str = "Motorista Logístico", corpo: str = "") -> str | None:
    html = (
        f'<html><head><script type="application/ld+json">{json.dumps(json_ld)}</script>'
        f"</head><body><h1>{titulo}</h1>{corpo}</body></html>"
    )
    url = "https://careers.empresa.example/jobs/1"
    leitura = ler_vaga(
        montar_inventario(html.encode(), url=url), titulo=titulo, id_externo="1", url_vaga=url
    )
    return (leitura.campos.get("location") or {}).get("address")


def test_macrorregiao_nao_entra_como_estado_e_a_uf_vem_do_texto():
    vaga = {
        "@type": "JobPosting",
        "title": "Motorista Logístico",
        "jobLocation": {"address": {"addressLocality": "Recife", "addressRegion": "Nordeste"}},
    }

    # Sem UF no texto, ela vem da lista do IBGE (Recife só existe em PE).
    assert _ler_local(vaga) == "Recife, PE"
    assert _ler_local(vaga, corpo="<p>Local de trabalho: Recife - PE</p>") == "Recife, PE"
    bom_jesus = {**vaga, "jobLocation": {"address": {"addressLocality": "Bom Jesus"}}}
    assert _ler_local(bom_jesus) == "Bom Jesus"


def test_cidade_do_titulo_quando_nada_mais_traz_o_local():
    assert _ler_local({"@type": "WebPage"}, titulo="Vendedor em Recife") == "Recife, PE"


def _ler_empresa(
    url: str, json_ld: dict | None = None, corpo: str = "", site: str = ""
) -> str | None:
    meta = f'<meta property="og:site_name" content="{site}">' if site else ""
    script = f'<script type="application/ld+json">{json.dumps(json_ld)}</script>' if json_ld else ""
    html = f"<html><head>{meta}{script}</head><body><h1>Analista Fiscal</h1>{corpo}</body></html>"
    leitura = ler_vaga(
        montar_inventario(html.encode(), url=url),
        titulo="Analista Fiscal",
        id_externo="1",
        url_vaga=url,
    )
    return (leitura.campos.get("company") or {}).get("name")


def test_consultoria_que_esconde_o_cliente_vira_confidential():
    url = "https://www.michaelpage.com.br/job-detail/analista-fiscal/ref/1"
    vaga = {"@type": "JobPosting", "hiringOrganization": {"name": "Michael Page"}}

    assert _ler_empresa(url, vaga) == "confidential"
    assert _ler_empresa(url) == "confidential"
    # Quando a consultoria diz quem é o cliente, o nome dele vale.
    assert (
        _ler_empresa(url, {"@type": "JobPosting", "hiringOrganization": {"name": "Vale"}}) == "Vale"
    )


def test_texto_que_diz_empresa_confidencial_vira_confidential():
    corpo = "<p>Empresa confidencial do ramo varejista contrata analista fiscal.</p>"

    assert (
        _ler_empresa("https://portal.example/vaga/1", corpo=corpo, site="Portal X")
        == "confidential"
    )
    assert _ler_empresa("https://loja.example/carreiras/1", site="Loja Y") == "Loja Y"


def test_empresa_em_microdata_da_pagina():
    corpo = (
        '<div itemprop="hiringOrganization" itemscope itemtype="http://schema.org/Organization">'
        '<meta itemprop="name" content="Red Bull"></div>'
    )

    assert _ler_empresa("https://jobs.smartrecruiters.com/RedBull/1", corpo=corpo) == "Red Bull"
