"""Testes dos adaptadores de artigos de organizações brasileiras."""

from observatorio_vagas.extraction.organizacoes import extrair_vagas_organizacoes


def test_extrai_vaga_unica_da_data_privacy() -> None:
    """Título, descrição, prazo e candidatura vêm apenas do artigo."""

    html = """
    <html><head>
      <meta property="article:published_time" content="2026-05-25T13:02:27+00:00">
    </head><body>
      <h1 class="news__item__title">Vaga para Gestora de Cursos e Formações</h1>
      <section class="section--single-content"><article>
        <p>Estamos em busca de uma pessoa para gerir cursos e formações,
        organizar docentes e acompanhar indicadores de desempenho acadêmico.</p>
        <p>Prazo para candidaturas: até 14 de junho de 2026.</p>
        <p><a href="https://forms.example/vaga">Inscrições disponíveis no link</a></p>
      </article></section>
    </body></html>
    """.encode()

    resultado = extrair_vagas_organizacoes(
        html,
        url="https://www.dataprivacybr.org/vaga-gestora/",
    )

    assert resultado.dominio_reconhecido
    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert vaga["title"] == "Gestora de Cursos e Formações"
    assert vaga["datePosted"] == "2026-05-25"
    assert vaga["validThrough"] == "2026-06-14"
    assert vaga["_observatorio_apply_url"] == "https://forms.example/vaga"


def test_separa_tres_cargos_da_internetlab() -> None:
    """Uma matéria com vários cargos gera um documento por cargo."""

    html = """
    <html><head>
      <meta property="article:published_time" content="2025-12-19T13:19:58+00:00">
    </head><body>
      <h1 class="single-header__title">InternetLab abre seleção</h1>
      <div class="single-content__text">
        <p>As inscrições estarão abertas até 18 de janeiro de 2026. As vagas
        são para trabalho híbrido em São Paulo, com 30 horas semanais.</p>
        <p><strong>VAGA DE PESQUISADOR(A)</strong></p>
        <p>A pessoa participará de projetos de direito, internet e sociedade,
        realizará pesquisas e produzirá relatórios técnicos para a equipe.</p>
        <p><strong>VAGA DE ESTÁGIO EM COMUNICAÇÃO</strong></p>
        <p>A pessoa apoiará a estratégia de comunicação, o calendário editorial,
        o relacionamento com a imprensa e a produção de conteúdo institucional.</p>
        <p><strong>VAGA DE ESTÁGIO EM PESQUISA</strong></p>
        <p>A pessoa apoiará levantamentos bibliográficos, análises de jurisprudência,
        eventos e todas as etapas dos projetos de pesquisa da organização.</p>
        <p><a href="https://go.internetlab.org.br/vagas">Acesse aqui o formulário</a></p>
      </div>
    </body></html>
    """.encode()

    resultado = extrair_vagas_organizacoes(
        html,
        url="https://internetlab.org.br/pt/noticias/selecao/",
    )

    assert [vaga["title"] for vaga in resultado.vagas] == [
        "PESQUISADOR(A)",
        "ESTÁGIO EM COMUNICAÇÃO",
        "ESTÁGIO EM PESQUISA",
    ]
    assert {vaga["validThrough"] for vaga in resultado.vagas} == {"2026-01-18"}


def test_isola_vaga_no_meio_da_newsletter() -> None:
    """Notícias anteriores e posteriores não entram na descrição da vaga."""

    html = """
    <html><body><h1 class="post-title">Boletim semanal</h1>
      <div class="body markup">
        <h2>Notícia sobre dados públicos</h2><p>Texto sem relação com emprego.</p>
        <h2>Vaga para Estágio em Direito na Fiquem</h2>
        <p>Estudantes a partir do quinto período podem se candidatar para apoiar
        pesquisas, pareceres jurídicos e ações de advocacy da organização.</p>
        <p>Inscrições até 23/08/2026.</p>
        <p><a href="https://forms.example/estagio">Saiba mais e candidate-se</a></p>
        <h2>Outras notícias</h2><p>Conteúdo que não pertence à vaga.</p>
      </div>
    </body></html>
    """.encode()

    resultado = extrair_vagas_organizacoes(
        html,
        url="https://news.fiquemsabendo.com.br/p/boletim",
    )

    assert len(resultado.vagas) == 1
    vaga = resultado.vagas[0]
    assert "Texto sem relação" not in vaga["description"]
    assert "Outras notícias" not in vaga["description"]
    assert vaga["validThrough"] == "2026-08-23"


def test_layout_desconhecido_e_artigo_encerrado_falham_fechado() -> None:
    """Mudanças de layout e inscrições encerradas não geram falso positivo."""

    desconhecido = extrair_vagas_organizacoes(
        b"<h1>Vaga para Desenvolvedor</h1><p>Descricao longa de uma vaga.</p>",
        url="https://example.com/vaga",
    )
    encerrado = extrair_vagas_organizacoes(
        b'<h1 class="news__item__title">Inscricoes encerradas - Vaga para Analista</h1>'
        b'<section class="section--single-content"><article>Descricao suficientemente '
        b'longa para representar a oportunidade antiga publicada no site.</article></section>',
        url="https://www.dataprivacybr.org/vaga-antiga/",
    )

    assert not desconhecido.dominio_reconhecido
    assert desconhecido.vagas == ()
    assert encerrado.dominio_reconhecido
    assert encerrado.vagas == ()
