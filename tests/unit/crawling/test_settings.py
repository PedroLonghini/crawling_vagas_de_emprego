"""Testes das configurações de segurança do crawler."""

from observatorio_vagas.crawling import settings


def test_crawler_respeita_robots_txt() -> None:
    """O crawler deve respeitar robots.txt por padrão."""

    assert settings.ROBOTSTXT_OBEY is True


def test_paralelismo_aumenta_somente_entre_dominios() -> None:
    assert settings.CONCURRENT_REQUESTS == 180
    assert settings.CONCURRENT_REQUESTS_PER_DOMAIN == 2
    assert settings.DOWNLOAD_DELAY >= 0.5


def test_abler_autorizado_tem_fila_moderadamente_mais_rapida() -> None:
    slot = settings.DOWNLOAD_SLOTS["ats.abler.com.br"]
    assert slot["concurrency"] == 2
    assert slot["delay"] == 0.5


def test_apis_de_ats_possuem_slots_rapidos_e_isolados() -> None:
    solides = settings.DOWNLOAD_SLOTS["apigw.solides.com.br"]
    senior = settings.DOWNLOAD_SLOTS["platform.senior.com.br"]

    assert solides["concurrency"] == 3
    assert solides["delay"] < 0.5
    assert senior["concurrency"] == 2


def test_piloto_limita_requisicoes_por_dominio() -> None:
    """Sites comuns recebem no máximo duas requisições simultâneas."""

    assert settings.CONCURRENT_REQUESTS_PER_DOMAIN == 2
    assert settings.DOWNLOAD_DELAY >= 0.5


def test_autothrottle_esta_ativado() -> None:
    """A velocidade deve se adaptar ao tempo de resposta do site."""

    assert settings.AUTOTHROTTLE_ENABLED is True
    assert settings.AUTOTHROTTLE_TARGET_CONCURRENCY <= 2.0
    assert settings.AUTOTHROTTLE_MAX_DELAY >= settings.AUTOTHROTTLE_START_DELAY


def test_piloto_possui_limites_de_seguranca() -> None:
    """O crawler não deve navegar ou baixar conteúdo sem limites."""

    assert settings.DEPTH_LIMIT > 0
    assert settings.CLOSESPIDER_PAGECOUNT > 0
    assert settings.DOWNLOAD_TIMEOUT > 0
    assert settings.DOWNLOAD_MAXSIZE > 0
    assert settings.REDIRECT_MAX_TIMES > 0


def test_recursos_desnecessarios_estao_desativados() -> None:
    """Cookies e console remoto não são necessários neste piloto."""

    assert settings.COOKIES_ENABLED is False
    assert settings.TELNETCONSOLE_ENABLED is False


def test_user_agent_identifica_o_projeto() -> None:
    """O crawler não deve fingir ser um navegador comum."""

    assert settings.USER_AGENT.startswith("ObservatorioVagas/")
    assert "Mozilla" not in settings.USER_AGENT


def test_pipeline_de_armazenamento_bruto_esta_ativado() -> None:
    """Toda resposta bruta deve passar pelo pipeline de armazenamento."""

    # Guardamos o caminho em uma variável para não repetir
    # um texto grande dentro das verificações.
    caminho_pipeline = "observatorio_vagas.crawling.pipelines.PipelineArmazenamentoBruto"

    # Confirma onde os arquivos serão armazenados durante o piloto.
    assert settings.RAW_STORAGE_DIRECTORY == "data/raw"

    # Confirma que o pipeline foi registrado no Scrapy.
    assert caminho_pipeline in settings.ITEM_PIPELINES

    # Confirma sua prioridade de execução.
    assert settings.ITEM_PIPELINES[caminho_pipeline] == 100
