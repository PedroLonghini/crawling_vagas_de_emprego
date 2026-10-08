"""Testes do cache de análise de páginas idênticas."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from observatorio_vagas.crawling.inventario import carregar_inventario_bruto
from observatorio_vagas.domain.enums import TipoPaginaColeta
from observatorio_vagas.extraction import cache_paginas
from observatorio_vagas.extraction.processador import processar_respostas_brutas

from .test_processador import HTML_VAGA, _salvar_paginas_variadas, salvar_pagina


def _comparavel(resultado):
    return replace(
        resultado,
        paginas_do_cache=0,
        anuncios=tuple(anuncio.model_dump(exclude={"id"}) for anuncio in resultado.anuncios),
    )


def test_cache_nao_muda_o_resultado_e_evita_reler_paginas(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _salvar_paginas_variadas(raw)
    registros = carregar_inventario_bruto(raw)
    cache = tmp_path / "cache.sqlite"

    sem_cache = processar_respostas_brutas(raw, registros=registros)
    primeira = processar_respostas_brutas(raw, registros=registros, cache_paginas=cache)
    assert primeira.paginas_do_cache == 0

    # Sem os corpos no disco, só o cache consegue responder.
    for corpo in (raw / "corpos").rglob("*.bin"):
        corpo.unlink()

    segunda = processar_respostas_brutas(raw, registros=registros, cache_paginas=cache)

    assert segunda.paginas_do_cache == segunda.paginas_analisadas == 31
    assert segunda.falhas == ()
    assert _comparavel(primeira) == _comparavel(sem_cache) == _comparavel(segunda)


def test_mudar_o_codigo_dos_extratores_invalida_o_cache(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "raw"
    _salvar_paginas_variadas(raw)
    registros = carregar_inventario_bruto(raw)
    cache = tmp_path / "cache.sqlite"
    processar_respostas_brutas(raw, registros=registros, cache_paginas=cache)

    monkeypatch.setattr(cache_paginas, "assinatura_dos_extratores", lambda: "codigo-novo")

    resultado = processar_respostas_brutas(raw, registros=registros, cache_paginas=cache)
    assert resultado.paginas_do_cache == 0


def test_pagina_repetida_usa_dados_da_coleta_nova(tmp_path: Path) -> None:
    """O cache guarda a análise; datas e referências vêm sempre da coleta atual."""

    raw = tmp_path / "raw"
    cache = tmp_path / "cache.sqlite"
    ontem = datetime(2026, 9, 29, 12, tzinfo=UTC)
    hoje = datetime(2026, 9, 30, 12, tzinfo=UTC)

    salvar_pagina(raw, html=HTML_VAGA, tipo_pagina=TipoPaginaColeta.DETALHE_VAGA, coletado_em=ontem)
    processar_respostas_brutas(raw, coletado_desde=ontem, cache_paginas=cache)

    salvar_pagina(raw, html=HTML_VAGA, tipo_pagina=TipoPaginaColeta.DETALHE_VAGA, coletado_em=hoje)
    resultado = processar_respostas_brutas(raw, coletado_desde=hoje, cache_paginas=cache)

    assert resultado.paginas_do_cache == 1
    (anuncio,) = resultado.anuncios
    assert anuncio.ultima_observacao_em == hoje
    registro_hoje = next(r for r in carregar_inventario_bruto(raw) if r.coletado_em == hoje)
    assert anuncio.referencia_bruta == registro_hoje.referencia
