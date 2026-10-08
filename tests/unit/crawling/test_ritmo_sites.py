from pathlib import Path

from scrapy import Request

from observatorio_vagas.crawling.ritmo_sites import (
    RitmoPorSiteDownloaderMiddleware,
    carregar_ritmo,
    definir_ritmo,
)

CSV = (
    "# comentario\n"
    "dominio,requisicoes_simultaneas,intervalo_segundos,observacao\n"
    "*,2,0.5,padrao\n"
    "grande.com.br,3,0.35,grande\n"
    "quebrado.com,99,0.5,simultaneas demais\n"
    "sem_numero.com,x,0.5,\n"
)


def _arquivo(tmp_path: Path) -> Path:
    caminho = tmp_path / "ritmo.csv"
    caminho.write_text(CSV, encoding="utf-8")
    return caminho


def test_le_padrao_e_sites_e_ignora_linhas_invalidas(tmp_path: Path) -> None:
    ritmo = carregar_ritmo(_arquivo(tmp_path))

    assert ritmo.padrao.simultaneas == 2 and ritmo.padrao.intervalo == 0.5
    assert set(ritmo.por_dominio) == {"grande.com.br"}
    assert len(ritmo.ignoradas) == 2


def test_arquivo_ausente_usa_padrao_seguro(tmp_path: Path) -> None:
    ritmo = carregar_ritmo(tmp_path / "nao_existe.csv")

    assert ritmo.padrao.intervalo == 0.5 and not ritmo.por_dominio
    assert ritmo.ignoradas


def test_host_com_www_e_subdominio_usam_o_site_configurado(tmp_path: Path) -> None:
    ritmo = carregar_ritmo(_arquivo(tmp_path))

    assert ritmo.do_host("www.grande.com.br").dominio == "grande.com.br"
    assert ritmo.do_host("vagas.grande.com.br").dominio == "grande.com.br"
    assert ritmo.do_host("outro.com.br") is None


def test_middleware_junta_www_e_apex_no_mesmo_slot(tmp_path: Path) -> None:
    middleware = RitmoPorSiteDownloaderMiddleware(carregar_ritmo(_arquivo(tmp_path)))
    com_www = Request("https://www.grande.com.br/vaga/1")
    sem_www = Request("https://grande.com.br/vaga/2")
    outro = Request("https://outro.com.br/vaga/3")

    for requisicao in (com_www, sem_www, outro):
        middleware.process_request(requisicao)

    assert com_www.meta["download_slot"] == sem_www.meta["download_slot"] == "grande.com.br"
    assert com_www.meta["autothrottle_dont_adjust_delay"] is True
    assert "download_slot" not in outro.meta


def test_definir_ritmo_troca_a_linha_e_preserva_comentarios(tmp_path: Path) -> None:
    caminho = _arquivo(tmp_path)

    definir_ritmo("https://www.grande.com.br/vagas", 4, 0.25, caminho=caminho)
    definir_ritmo("novo.com", 1, 1.0, "teste", caminho=caminho)

    texto = caminho.read_text(encoding="utf-8")
    ritmo = carregar_ritmo(caminho)
    assert texto.startswith("# comentario")
    assert ritmo.por_dominio["grande.com.br"].simultaneas == 4
    assert ritmo.por_dominio["grande.com.br"].observacao == "grande"
    assert ritmo.por_dominio["novo.com"].observacao == "teste"
    assert texto.count("grande.com.br") == 1
