"""Testes dos adaptadores conservadores de notícias com vagas."""

from observatorio_vagas.extraction.noticias_vagas import (
    extrair_job_postings_noticia_itaqui,
    extrair_job_postings_noticia_palotina24h,
)


def test_extrai_somente_cargos_literalmente_listados_na_materia() -> None:
    """O total divulgado não pode criar vagas que a notícia não detalhou."""

    html = """
        <article class="post_content">
            <p>Há 231 vagas disponíveis nesta semana.</p>
            <p>Veja as vagas</p>
            <p>Auxiliar administrativo</p>
            <p>Motorista entregador</p>
            <p>Representante comercial autônomo</p>
            <p>Reprodução permitida desde que seja mantido o crédito ao Palotina 24 Horas.</p>
        </article>
    """

    resultado = extrair_job_postings_noticia_palotina24h(
        html,
        url=(
            "https://palotina24horas.com.br/"
            "agencia-do-trabalhador-inicia-a-semana-com-231-vagas-disponiveis/"
        ),
    )

    assert resultado.pagina_reconhecida
    assert [vaga["title"] for vaga in resultado.vagas] == [
        "Auxiliar administrativo",
        "Motorista entregador",
        "Representante comercial autônomo",
    ]
    assert resultado.vagas[0]["hiringOrganization"]["name"].startswith("Empregador não divulgado")


def test_nao_extrai_quando_a_permissao_da_materia_nao_esta_presente() -> None:
    """A página muda de status quando a condição jurídica desaparece."""

    resultado = extrair_job_postings_noticia_palotina24h(
        "<article class='post_content'><p>Auxiliar administrativo</p></article>",
        url=(
            "https://palotina24horas.com.br/"
            "agencia-do-trabalhador-inicia-a-semana-com-231-vagas-disponiveis/"
        ),
    )

    assert resultado.pagina_reconhecida
    assert resultado.vagas == ()


def test_extrai_a_vaga_unica_da_cvale() -> None:
    """A matéria da C.Vale nomeia uma única vaga, sem extrapolar números."""

    html = """
        <article class="post_content">
            <p>C.Vale – Auxiliar de Produção</p>
            <p>Reprodução permitida desde que seja mantido o crédito ao Palotina 24 Horas.</p>
        </article>
    """

    resultado = extrair_job_postings_noticia_palotina24h(
        html,
        url=(
            "https://palotina24horas.com.br/"
            "c-vale-abre-inscricoes-para-jovem-aprendiz-administrativo/"
        ),
    )

    assert [vaga["title"] for vaga in resultado.vagas] == ["Auxiliar de Produção"]
    assert resultado.vagas[0]["hiringOrganization"]["name"] == "C.Vale"


def test_palotina_aceita_nova_materia_sem_cadastrar_cada_url() -> None:
    """Novas listas licenciadas da categoria devem funcionar automaticamente."""

    html = """
        <article class="post_content">
            <p>Agência do Trabalhador de Palotina</p>
            <p>Vagas disponíveis</p>
            <ul><li>Analista de suporte</li><li>Motorista entregador</li></ul>
            <p>Reprodução permitida desde que seja mantido o crédito ao Palotina 24 Horas.</p>
        </article>
    """

    resultado = extrair_job_postings_noticia_palotina24h(
        html,
        url="https://palotina24horas.com.br/nova-lista-semanal-de-vagas/",
    )

    assert [vaga["title"] for vaga in resultado.vagas] == [
        "Analista de suporte",
        "Motorista entregador",
    ]


def test_palotina_descarta_concurso_mesmo_com_licenca() -> None:
    """A licença do veículo não deve furar o bloqueio de concursos públicos."""

    html = """
        <article class="post_content">
            <p>Concurso público da prefeitura</p>
            <p>Agência do Trabalhador</p>
            <p>Professor</p>
            <p>Reprodução permitida desde que seja mantido o crédito ao Palotina 24 Horas.</p>
        </article>
    """

    resultado = extrair_job_postings_noticia_palotina24h(
        html,
        url="https://palotina24horas.com.br/concurso-publico-com-vagas/",
    )

    assert resultado.pagina_reconhecida
    assert resultado.vagas == ()


def test_extrai_lista_licenciada_do_sine_itaqui() -> None:
    """Uma notícia do Sine gera um anúncio para cada cargo explicitamente listado."""

    html = """
        <html>
            <head>
                <meta property="article:published_time" content="2026-09-09T11:39:00-03:00">
            </head>
            <body>
                <main>
                    <p>A FGTAS/Sine atualiza suas vagas privadas.</p>
                    <p>Auxiliar de Produção – 3 vagas: não exige experiência.</p>
                    <p>Motorista de Caminhão – 2 vagas: exige CNH D.</p>
                </main>
                <footer>
                    Todo material produzido pela Assessoria de Comunicação pode ser
                    reproduzido desde que citada a fonte.
                </footer>
            </body>
        </html>
    """

    resultado = extrair_job_postings_noticia_itaqui(
        html,
        url=("https://www.itaqui.rs.gov.br/noticias/2026/09/fgtas-sine-atualiza-vagas.html"),
    )

    assert [vaga["title"] for vaga in resultado.vagas] == [
        "Auxiliar de Produção",
        "Motorista de Caminhão",
    ]
    assert resultado.vagas[0]["validThrough"] == "2026-09-16T11:39:00-03:00"
    assert resultado.vagas[0]["jobLocation"]["address"]["addressRegion"] == "RS"


def test_itaqui_exige_aviso_de_reproducao() -> None:
    """Sem a autorização visível na página, a notícia não produz anúncios."""

    resultado = extrair_job_postings_noticia_itaqui(
        "<main><p>FGTAS/Sine</p><p>Auxiliar – 1 vaga.</p></main>",
        url="https://www.itaqui.rs.gov.br/noticias/2026/09/vagas.html",
    )

    assert resultado.pagina_reconhecida
    assert resultado.vagas == ()
