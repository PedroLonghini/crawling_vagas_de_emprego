"""Testes do processamento em lote das páginas brutas."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from observatorio_vagas.crawling.contracts import (
    RespostaBruta,
)
from observatorio_vagas.crawling.inventario import carregar_inventario_bruto
from observatorio_vagas.crawling.raw_storage import (
    ArmazenamentoBrutoLocal,
)
from observatorio_vagas.domain.enums import (
    Fonte,
    TipoPaginaColeta,
)
from observatorio_vagas.extraction import processador
from observatorio_vagas.extraction.processador import (
    processar_respostas_brutas,
)

# HTML fictício semelhante ao que será armazenado pelo crawler.
#
# Ele possui:
#
# - um JobPosting em JSON-LD;
# - um link real de candidatura.
HTML_VAGA = """
<html>
    <body>
        <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "identifier": {
                    "@type": "PropertyValue",
                    "value": "vaga-123"
                },
                "title": "Pessoa Desenvolvedora Python",
                "description": "Desenvolvimento de aplicações em Python.",
                "url": "https://empresa.example/jobs/vaga-123",
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Empresa Exemplo"
                }
            }
        </script>

        <a
            href="https://empresa.example/jobs/vaga-123/apply"
        >
            Apply now
        </a>
    </body>
</html>
"""


def test_publicacao_filtra_dia_brasilia_independente_da_coleta(tmp_path):
    datas = ["2026-09-15", "2026-09-16T02:59:59Z", "2026-09-16T03:00:00Z", "2026-09-14", None]
    for indice, publicada in enumerate(datas):
        html = HTML_VAGA.replace("vaga-123", f"vaga-{indice}")
        if publicada:
            html = html.replace(
                '"@type": "JobPosting",', f'"@type": "JobPosting", "datePosted": "{publicada}",'
            )
        salvar_pagina(
            tmp_path,
            html=html,
            tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
            coletado_em=datetime(2026, 9, 17, tzinfo=UTC),
        )
    resultado = processar_respostas_brutas(tmp_path, publicado_em=date(2026, 9, 15))
    assert not resultado.falhas
    assert {a.id_externo for a in resultado.anuncios} == {"vaga-0", "vaga-1"}
    assert resultado.anuncios_fora_data_publicacao == 2
    assert resultado.anuncios_sem_data_publicacao == 1
    assert len(processar_respostas_brutas(tmp_path).anuncios) == 5


def test_snapshot_equivale_ao_disco_e_preserva_filtros(tmp_path, monkeypatch):
    momento = datetime(2026, 9, 11, tzinfo=UTC)
    for alvo in ("empresa_exemplo", "outro"):
        salvar_pagina(
            tmp_path,
            html=HTML_VAGA,
            tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
            coletado_em=momento,
            alvo_id=alvo,
        )
    esperado = processar_respostas_brutas(
        tmp_path,
        alvo_id="empresa_exemplo",
        coletado_desde=momento,
    )
    snapshot = carregar_inventario_bruto(tmp_path)

    def nao_reler(*args):
        raise AssertionError("snapshot não deve reler metadados")

    monkeypatch.setattr(processador, "carregar_inventario_bruto", nao_reler)
    obtido = processar_respostas_brutas(
        tmp_path,
        alvo_id="empresa_exemplo",
        coletado_desde=momento,
        registros=snapshot,
    )
    # IDs de anúncios são gerados em cada processamento.
    assert obtido.paginas_analisadas == esperado.paginas_analisadas == 1
    assert obtido.paginas_fora_filtro == esperado.paginas_fora_filtro == 1
    assert len(obtido.anuncios) == len(esperado.anuncios) == 1
    assert obtido.anuncios[0].titulo_original == esperado.anuncios[0].titulo_original
    assert not obtido.falhas
    assert not processar_respostas_brutas(tmp_path, registros=()).anuncios
    adulterado = (replace(snapshot[0], hash_conteudo="0" * 64),)
    assert processar_respostas_brutas(tmp_path, registros=adulterado).falhas


def test_resumo_separa_outro_alvo_de_http_invalido(tmp_path: Path) -> None:
    for alvo, status in (("outro", 200), ("empresa_exemplo", 503)):
        salvar_pagina(
            tmp_path,
            html=HTML_VAGA,
            tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
            coletado_em=datetime(2026, 9, 3, tzinfo=UTC),
            alvo_id=alvo,
            status_http=status,
        )
    resultado = processar_respostas_brutas(tmp_path, alvo_id="empresa_exemplo")
    assert resultado.paginas_ignoradas == 2
    assert resultado.paginas_fora_filtro == 1
    assert resultado.paginas_incompativeis == 1


def salvar_pagina(
    diretorio_base: Path,
    *,
    html: str,
    tipo_pagina: TipoPaginaColeta,
    coletado_em: datetime,
    status_http: int = 200,
    alvo_id: str = "empresa_exemplo",
    url: str = "https://empresa.example/jobs/vaga-123",
) -> None:
    """Armazena uma resposta usando o componente real."""

    armazenamento = ArmazenamentoBrutoLocal(
        diretorio_base,
    )

    armazenamento.salvar(
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada=url,
            url_final=url,
            status_http=status_http,
            corpo=html.encode(),
            alvo_id=alvo_id,
            empresa_nome="Empresa Exemplo",
            numero_pagina=2,
            tipo_pagina=tipo_pagina,
            tipo_conteudo=("text/html; charset=utf-8"),
            codificacao="utf-8",
            coletado_em=coletado_em,
        )
    )


def salvar_resposta_querido_diario(
    diretorio_base: Path,
    *,
    coletado_em: datetime,
) -> None:
    """Armazena JSON da API usando a mesma trilha do crawler real."""

    corpo = json.dumps(
        {
            "gazettes": [
                {
                    "territory_id": "3106200",
                    "territory_name": "Belo Horizonte",
                    "state_code": "MG",
                    "date": "2026-09-02",
                    "edition": "456",
                    "url": "https://diarios.example/bh/2026-09-02.pdf",
                    "excerpts": [
                        (
                            "Processo seletivo simplificado com vagas para "
                            "contratação temporária. Inscrições até "
                            "30/09/2026."
                        )
                    ],
                }
            ]
        },
        ensure_ascii=False,
    ).encode()

    ArmazenamentoBrutoLocal(diretorio_base).salvar(
        RespostaBruta(
            fonte=Fonte.QUERIDO_DIARIO,
            url_solicitada=("https://api.queridodiario.org.br/gazettes"),
            url_final="https://api.queridodiario.org.br/gazettes",
            status_http=200,
            corpo=corpo,
            alvo_id="querido_diario_selecoes_municipais",
            empresa_nome="Querido Diário",
            numero_pagina=1,
            tipo_pagina=TipoPaginaColeta.INICIAL,
            tipo_conteudo="application/json; charset=utf-8",
            codificacao="utf-8",
            coletado_em=coletado_em,
        )
    )


def salvar_resposta_ckan(
    diretorio_base: Path,
    *,
    coletado_em: datetime,
) -> None:
    """Armazena um CSV CKAN como uma página de detalhe autorizada."""

    cabecalho = ",".join(
        (
            "VAGA",
            "CÓDIGO DA VAGA",
            "DESCRIÇÃO DA VAGA",
            "EXIGÊNCIA DE QUALIFICAÇÃO",
            "EXIGÊNCIA DE ESCOLARIZAÇÃO",
            "QUANTIDADE",
            "dataAtualizacao",
            "posto",
        )
    )
    linha = (
        "Eletricista,ES-900,Manutenção elétrica,Curso profissionalizante,"
        "Ensino médio,3,09/02/2026,Linhares"
    )
    corpo = f"{cabecalho}\n{linha}\n".encode()

    ArmazenamentoBrutoLocal(diretorio_base).salvar(
        RespostaBruta(
            fonte=Fonte.CKAN,
            url_solicitada="https://dados.es.gov.br/vagas_linhares.csv",
            url_final="https://dados.es.gov.br/vagas_linhares.csv",
            status_http=200,
            corpo=corpo,
            alvo_id="es_setades_vagas_agencias",
            empresa_nome="SETADES ES",
            numero_pagina=2,
            tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
            tipo_conteudo="text/csv; charset=utf-8",
            codificacao="utf-8",
            coletado_em=coletado_em,
        )
    )


def test_processa_listagem_json_de_pagina_de_carreiras(tmp_path: Path) -> None:
    """Uma API pública de vagas deve dispensar a visita a cada detalhe."""

    diretorio_raw = tmp_path / "raw"
    ArmazenamentoBrutoLocal(diretorio_raw).salvar(
        RespostaBruta(
            fonte=Fonte.PAGINA_CARREIRAS,
            url_solicitada="https://empresa.example/api/jobs",
            url_final="https://empresa.example/api/jobs",
            status_http=200,
            corpo=(
                b'{"jobs":[{"id":"api-1","title":"Analista de Dados",'
                b'"description":"Atua com dados","detailUrl":"/vagas/api-1"}]}'
            ),
            alvo_id="empresa_api",
            empresa_nome="Empresa API",
            numero_pagina=1,
            tipo_pagina=TipoPaginaColeta.INICIAL,
            tipo_conteudo="application/json; charset=utf-8",
            codificacao="utf-8",
            coletado_em=datetime(2026, 9, 24, tzinfo=UTC),
        )
    )

    resultado = processar_respostas_brutas(diretorio_raw)

    assert not resultado.falhas
    assert [anuncio.id_externo for anuncio in resultado.anuncios] == ["api-1"]
    assert str(resultado.anuncios[0].url) == "https://empresa.example/vagas/api-1"


def test_processa_pagina_de_detalhe_com_job_posting(
    tmp_path: Path,
) -> None:
    """Uma página de detalhe deve produzir um anúncio."""

    diretorio_raw = tmp_path / "raw"

    salvar_pagina(
        diretorio_raw,
        html=HTML_VAGA,
        tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
        coletado_em=datetime(
            2026,
            8,
            24,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    resultado = processar_respostas_brutas(
        diretorio_raw,
    )

    assert resultado.paginas_analisadas == 1
    assert resultado.paginas_ignoradas == 0
    assert resultado.documentos_encontrados == 1
    assert resultado.anuncios_duplicados == 0
    assert resultado.falhas == ()
    assert len(resultado.anuncios) == 1

    anuncio = resultado.anuncios[0]

    # Confirma que o identificador do catálogo atravessou:
    #
    # resposta bruta
    # -> inventário
    # -> extrator
    # -> AnuncioVaga
    assert anuncio.alvo_id == "empresa_exemplo"

    assert anuncio.id_externo == "vaga-123"

    assert anuncio.titulo_original == ("Pessoa Desenvolvedora Python")

    assert anuncio.empresa_original == ("Empresa Exemplo")

    # Confirma que o link encontrado no HTML
    # chegou corretamente ao anúncio.
    assert str(anuncio.url_candidatura) == ("https://empresa.example/jobs/vaga-123/apply")

    assert anuncio.primeira_observacao_em == datetime(
        2026,
        8,
        24,
        12,
        0,
        tzinfo=UTC,
    )

    assert anuncio.ultima_observacao_em == datetime(
        2026,
        8,
        24,
        12,
        0,
        tzinfo=UTC,
    )


def test_processa_resposta_json_do_querido_diario(
    tmp_path: Path,
) -> None:
    """A API pública deve entrar no mesmo domínio auditável de anúncios."""

    diretorio_raw = tmp_path / "raw"
    coletado_em = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    salvar_resposta_querido_diario(
        diretorio_raw,
        coletado_em=coletado_em,
    )

    resultado = processar_respostas_brutas(diretorio_raw)

    assert resultado.paginas_analisadas == 1
    assert resultado.paginas_ignoradas == 0
    assert resultado.documentos_encontrados == 1
    assert resultado.blocos_invalidos == 0
    assert resultado.falhas == ()
    assert len(resultado.anuncios) == 1

    anuncio = resultado.anuncios[0]

    assert anuncio.fonte is Fonte.QUERIDO_DIARIO
    assert anuncio.alvo_id == "querido_diario_selecoes_municipais"
    assert anuncio.titulo_original == ("Processo seletivo público - Belo Horizonte/MG")
    assert anuncio.empresa_original == "Município de Belo Horizonte"
    assert anuncio.url_candidatura is None
    assert anuncio.publicado_em.isoformat() == "2026-09-02"
    assert anuncio.expira_em.isoformat() == "2026-09-30"
    assert anuncio.primeira_observacao_em == coletado_em


def test_processa_recurso_csv_ckan(
    tmp_path: Path,
) -> None:
    """Um CSV oficial deve atravessar a trilha bruta sem usar HTML."""

    diretorio_raw = tmp_path / "raw"
    coletado_em = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    salvar_resposta_ckan(diretorio_raw, coletado_em=coletado_em)

    resultado = processar_respostas_brutas(diretorio_raw)

    assert resultado.paginas_analisadas == 1
    assert resultado.falhas == ()
    assert len(resultado.anuncios) == 1

    anuncio = resultado.anuncios[0]

    assert anuncio.fonte is Fonte.CKAN
    assert anuncio.alvo_id == "es_setades_vagas_agencias"
    assert anuncio.titulo_original == "Eletricista"
    assert anuncio.empresa_original == "Empregador não divulgado (Linhares)"
    assert anuncio.publicado_em.isoformat() == "2026-09-02"
    assert anuncio.primeira_observacao_em == coletado_em


def test_processa_pagina_inicial_quando_ela_e_uma_vaga_direta(
    tmp_path: Path,
) -> None:
    """Uma URL direta no catálogo também pode conter um JobPosting."""

    diretorio_raw = tmp_path / "raw"

    salvar_pagina(
        diretorio_raw,
        html=HTML_VAGA,
        tipo_pagina=(TipoPaginaColeta.INICIAL),
        coletado_em=datetime(
            2026,
            8,
            24,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    resultado = processar_respostas_brutas(
        diretorio_raw,
    )

    assert resultado.paginas_analisadas == 1
    assert resultado.paginas_ignoradas == 0
    assert len(resultado.anuncios) == 1
    assert resultado.anuncios[0].id_externo == "vaga-123"


def test_processa_detalhe_da_teleperformance_sem_json_ld(tmp_path: Path) -> None:
    """A descrição codificada pelo portal deve virar uma vaga brasileira."""

    html = """
    <h1 class="vagaDetails__title">Agente de Atendimento</h1>
    <input id="dh" value="&lt;p&gt;Atenda clientes e ofereça suporte técnico com
    comunicação clara, atenção aos detalhes e foco em uma boa experiência.&lt;/p&gt;
    &lt;p&gt;Local: Lapa – São Paulo/SP&lt;/p&gt;">
    """
    url = "https://portaldevagas.teleperformance.com.br/VagaCandidatura/VagasDetail?idVaga=abc123"
    salvar_pagina(
        tmp_path,
        html=html,
        tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
        coletado_em=datetime(2026, 9, 22, tzinfo=UTC),
        alvo_id="portaldevagas_a7fd7ecb03",
        url=url,
    )

    resultado = processar_respostas_brutas(tmp_path)

    assert resultado.falhas == ()
    assert len(resultado.anuncios) == 1
    anuncio = resultado.anuncios[0]
    assert anuncio.titulo_original == "Agente de Atendimento"
    assert anuncio.empresa_original == "Teleperformance"
    assert anuncio.localidade_original == "São Paulo, SP, BR"
    assert str(anuncio.url_candidatura) == url


def test_registra_pagina_sem_json_ld(
    tmp_path: Path,
) -> None:
    """Uma página sem JobPosting deve ser contabilizada."""

    diretorio_raw = tmp_path / "raw"

    salvar_pagina(
        diretorio_raw,
        html=("<html><body>Vaga sem dados estruturados</body></html>"),
        tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
        coletado_em=datetime(
            2026,
            8,
            24,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    resultado = processar_respostas_brutas(
        diretorio_raw,
    )

    assert resultado.paginas_analisadas == 1
    assert resultado.paginas_sem_json_ld == 1
    assert resultado.documentos_encontrados == 0
    assert resultado.anuncios == ()


def test_processa_detalhe_html_sem_json_ld(
    tmp_path: Path,
) -> None:
    """Uma página de vaga estática pode usar o fallback HTML."""

    diretorio_raw = tmp_path / "raw"
    html = """
        <html>
            <head><meta property="og:site_name" content="Empresa Exemplo"></head>
            <body>
                <h1>Analista de Dados</h1>
                <div class="job-description">
                    A pessoa será responsável por analisar bases de dados,
                    desenvolver indicadores, documentar resultados e apoiar
                    decisões das diferentes áreas da organização.
                </div>
            </body>
        </html>
    """

    salvar_pagina(
        diretorio_raw,
        html=html,
        tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
        coletado_em=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        url="https://empresa.example/vagas/analista-dados",
    )

    resultado = processar_respostas_brutas(diretorio_raw)

    assert resultado.paginas_sem_json_ld == 0
    assert resultado.documentos_encontrados == 1
    assert len(resultado.anuncios) == 1
    assert resultado.anuncios[0].titulo_original == "Analista de Dados"


def test_processa_edital_publico_sem_json_ld(
    tmp_path: Path,
) -> None:
    """Um edital municipal aberto pode usar o adaptador HTML seguro."""

    from tests.unit.extraction.test_portal_publico import (
        HTML_EDITAL,
        URL_EDITAL,
    )

    diretorio_raw = tmp_path / "raw"

    salvar_pagina(
        diretorio_raw,
        html=HTML_EDITAL,
        tipo_pagina=TipoPaginaColeta.INICIAL,
        coletado_em=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        alvo_id="pratania_processo_seletivo_motorista",
        url=URL_EDITAL,
    )

    resultado = processar_respostas_brutas(diretorio_raw)

    assert resultado.paginas_analisadas == 1
    assert resultado.paginas_sem_json_ld == 0
    assert resultado.documentos_encontrados == 1
    assert resultado.falhas == ()

    anuncio = resultado.anuncios[0]

    assert anuncio.id_externo == "2/2026"
    assert anuncio.titulo_original == "Motorista Nível III"
    assert anuncio.empresa_original == "Prefeitura de Pratânia"
    assert str(anuncio.url_candidatura) == URL_EDITAL
    assert anuncio.campos_estruturados["hiringOrganization"]["taxID"] == ("01.576.782/0001-74")


def test_remove_anuncio_repetido_de_coletas_diferentes(
    tmp_path: Path,
) -> None:
    """Duas observações devem produzir anúncio único."""

    diretorio_raw = tmp_path / "raw"

    salvar_pagina(
        diretorio_raw,
        html=HTML_VAGA,
        tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
        coletado_em=datetime(
            2026,
            8,
            24,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    salvar_pagina(
        diretorio_raw,
        html=HTML_VAGA,
        tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
        coletado_em=datetime(
            2026,
            8,
            24,
            13,
            0,
            tzinfo=UTC,
        ),
    )

    resultado = processar_respostas_brutas(
        diretorio_raw,
    )

    assert resultado.paginas_analisadas == 2
    assert resultado.documentos_encontrados == 2
    assert resultado.anuncios_duplicados == 1
    assert len(resultado.anuncios) == 1


def test_filtra_processamento_por_alvo_id(
    tmp_path: Path,
) -> None:
    """Somente o alvo solicitado deve produzir anúncios."""

    diretorio_raw = tmp_path / "raw"

    salvar_pagina(
        diretorio_raw,
        html=HTML_VAGA,
        tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
        coletado_em=datetime(
            2026,
            8,
            24,
            12,
            0,
            tzinfo=UTC,
        ),
        alvo_id="empresa_permitida",
    )

    salvar_pagina(
        diretorio_raw,
        html=HTML_VAGA.replace(
            "vaga-123",
            "vaga-ignorada",
        ),
        tipo_pagina=(TipoPaginaColeta.DETALHE_VAGA),
        coletado_em=datetime(
            2026,
            8,
            24,
            13,
            0,
            tzinfo=UTC,
        ),
        alvo_id="empresa_ignorada",
    )

    resultado = processar_respostas_brutas(
        diretorio_raw,
        alvo_id="empresa_permitida",
    )

    assert resultado.paginas_analisadas == 1
    assert resultado.paginas_ignoradas == 1
    assert len(resultado.anuncios) == 1

    assert resultado.anuncios[0].id_externo == ("vaga-123")


def _salvar_paginas_variadas(diretorio_base: Path) -> None:
    """Páginas de vaga com repetições entre coletas e uma página sem vaga."""

    for hora in range(3):
        for numero in range(10):
            # Vagas 0 a 4 aparecem nas três coletas: viram duplicadas.
            identificador = numero if numero < 5 else numero + 10 * hora
            salvar_pagina(
                diretorio_base,
                html=HTML_VAGA.replace("vaga-123", f"vaga-{identificador}"),
                tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
                coletado_em=datetime(2026, 9, 17, hora, numero, tzinfo=UTC),
                url=f"https://empresa.example/jobs/vaga-{identificador}",
            )
    salvar_pagina(
        diretorio_base,
        html="<html><body>Sem vagas</body></html>",
        tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
        coletado_em=datetime(2026, 9, 17, 5, tzinfo=UTC),
        url="https://empresa.example/sobre",
    )


def test_extracao_em_pedacos_e_identica_a_sequencial(tmp_path: Path) -> None:
    """Dividir um alvo em pedaços não pode mudar anúncios nem contagens."""

    _salvar_paginas_variadas(tmp_path)
    registros = carregar_inventario_bruto(tmp_path)

    sequencial = processar_respostas_brutas(tmp_path, registros=registros)

    def comparavel(resultado):
        # O id do anúncio é aleatório a cada conversão; o resto deve ser igual,
        # inclusive a ordem e qual observação de cada vaga foi preservada.
        return replace(
            resultado,
            anuncios=tuple(anuncio.model_dump(exclude={"id"}) for anuncio in resultado.anuncios),
        )

    for tamanho in (1, 4, 7, len(registros)):
        parciais = [
            processador.extrair_parcial(tmp_path, registros[inicio : inicio + tamanho])
            for inicio in range(0, len(registros), tamanho)
        ]

        assert comparavel(processador.combinar_parciais(parciais)) == comparavel(sequencial)

    # Sanidade do cenário: houve duplicatas e uma página sem vaga.
    assert sequencial.anuncios_duplicados == 10
    assert sequencial.paginas_sem_json_ld == 1
    assert len(sequencial.anuncios) == 20


def test_mesma_vaga_publicada_com_varios_enderecos_conta_uma_vez() -> None:
    from observatorio_vagas.domain.anuncio import AnuncioVaga
    from observatorio_vagas.domain.enums import Fonte
    from observatorio_vagas.extraction.processador import chave_de_conteudo

    def anuncio(sufixo: str, local: str = "Macaé - RJ") -> AnuncioVaga:
        return AnuncioVaga(
            fonte=Fonte.PAGINA_CARREIRAS,
            id_externo=f"id-{sufixo}",
            url=f"https://vagas.click.example/vagas/auxiliar-administrativo-pcd-{sufixo}/",
            titulo_original="AUXILIAR ADMINISTRATIVO I- VAGA EXCLUSIVA PARA PESSOA COM DEFICIENCIA",
            descricao_original=(
                "A hora de fazer parte de um time comprometido, acolhedor e que valoriza "
                "a diversidade. Atividades: rotinas administrativas, atendimento e apoio "
                "ao setor de compras e ao almoxarifado da unidade offshore. Requisitos: "
                f"ensino médio completo e pacote office intermediário. Código {sufixo}."
            ),
            localidade_original=local,
            hash_conteudo="a" * 64,
            referencia_bruta=f"raw/{sufixo}.json",
        )

    assert chave_de_conteudo(anuncio("1040")) == chave_de_conteudo(anuncio("872"))
    assert chave_de_conteudo(anuncio("1040")) != chave_de_conteudo(anuncio("1040", "Niterói - RJ"))
    # Texto curto (SINE/CKAN): vagas iguais de empresas diferentes não se juntam.
    curto_a = anuncio("1").model_copy(update={"descricao_original": "Vaga do SINE."})
    curto_b = anuncio("2").model_copy(update={"descricao_original": "Vaga do SINE."})
    assert chave_de_conteudo(curto_a) != chave_de_conteudo(curto_b)


def test_titulo_com_entidade_html_vira_caractere() -> None:
    from observatorio_vagas.extraction.mapeamento_json_ld import converter_job_posting_em_anuncio

    anuncio = converter_job_posting_em_anuncio(
        {
            "@type": "JobPosting",
            "title": "Auxiliar De Reposição &#8211; Arujá",
            "description": "Repor mercadorias.",
            "url": "https://farma.example/vagas/1",
        },
        fonte=Fonte.PAGINA_CARREIRAS,
        hash_conteudo="b" * 64,
        referencia_bruta="raw/1.json",
    )

    assert anuncio.titulo_original == "Auxiliar De Reposição – Arujá"
