"""Testes do armazenamento local de respostas brutas."""

import json
from datetime import UTC, datetime
from pathlib import Path

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.raw_storage import ArmazenamentoBrutoLocal, ler_corpo_bruto
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta

DATA_INICIAL = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
DATA_SEGUINTE = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)

CORPO_HTML = b"<html><body><h1>Pessoa Desenvolvedora Python</h1></body></html>"


def criar_resposta(
    *,
    url: str = "https://empresa.example/vagas/123",
    coletado_em: datetime = DATA_INICIAL,
    corpo: bytes = CORPO_HTML,
    alvo_id: str | None = "empresa_teste",
    empresa_nome: str | None = "Empresa Teste",
    numero_pagina: int = 2,
    tipo_pagina: TipoPaginaColeta = TipoPaginaColeta.DETALHE_VAGA,
) -> RespostaBruta:
    """Cria uma resposta válida para os testes."""

    return RespostaBruta(
        fonte=Fonte.GUPY,
        url_solicitada=url,
        url_final=url,
        status_http=200,
        corpo=corpo,
        # Contexto que normalmente vem do catálogo.
        alvo_id=alvo_id,
        empresa_nome=empresa_nome,
        numero_pagina=numero_pagina,
        tipo_pagina=tipo_pagina,
        tipo_conteudo="text/html",
        codificacao="utf-8",
        cabecalhos=(("Content-Type", "text/html; charset=utf-8"),),
        coletado_em=coletado_em,
    )


def test_salvar_cria_corpo_e_metadados(
    tmp_path: Path,
) -> None:
    """Uma resposta deve produzir dois arquivos."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)
    resposta = criar_resposta()

    resultado = armazenamento.salvar(resposta)

    assert resultado.corpo_novo is True
    assert resultado.caminho_corpo.exists()
    assert resultado.caminho_metadados.exists()

    assert ler_corpo_bruto(resultado.caminho_corpo) == CORPO_HTML

    metadados = json.loads(resultado.caminho_metadados.read_text(encoding="utf-8"))

    assert metadados["versao_schema"] == 3
    assert metadados["alvo_id"] == "empresa_teste"
    assert metadados["empresa_nome"] == "Empresa Teste"
    assert metadados["numero_pagina"] == 2
    assert metadados["tipo_pagina"] == TipoPaginaColeta.DETALHE_VAGA.value
    assert metadados["fonte"] == Fonte.GUPY.value
    assert metadados["status_http"] == 200
    assert metadados["url_final"] == resposta.url_final
    assert metadados["hash_conteudo"] == resposta.hash_conteudo
    assert metadados["tamanho_bytes"] == len(CORPO_HTML)

    caminho_corpo = tmp_path / metadados["caminho_corpo"]

    assert ler_corpo_bruto(caminho_corpo) == CORPO_HTML


def test_referencia_e_relativa(
    tmp_path: Path,
) -> None:
    """A referência não deve depender do computador atual."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)
    resultado = armazenamento.salvar(criar_resposta())

    referencia = Path(resultado.referencia)

    assert referencia.is_absolute() is False
    assert resultado.referencia.startswith("respostas/gupy/2026/08/20/")


def test_hash_distribui_corpos_em_subpastas(
    tmp_path: Path,
) -> None:
    """Os primeiros caracteres do hash devem definir a pasta."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)
    resultado = armazenamento.salvar(criar_resposta())

    prefixo_esperado = resultado.hash_conteudo[:2]

    assert resultado.caminho_corpo.parent.name == prefixo_esperado
    assert resultado.caminho_corpo.name == f"{resultado.hash_conteudo}.bin.gz"


def test_mesma_resposta_nao_sobrescreve_corpo(
    tmp_path: Path,
) -> None:
    """Salvar a mesma resposta duas vezes deve reutilizar o corpo."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)
    resposta = criar_resposta()

    primeiro = armazenamento.salvar(resposta)
    segundo = armazenamento.salvar(resposta)

    assert primeiro.corpo_novo is True
    assert segundo.corpo_novo is False
    assert primeiro.caminho_corpo == segundo.caminho_corpo
    assert primeiro.caminho_metadados == segundo.caminho_metadados

    corpos = list((tmp_path / "corpos").rglob("*.bin*"))

    assert len(corpos) == 1


def test_coletas_diferentes_reutilizam_o_mesmo_corpo(
    tmp_path: Path,
) -> None:
    """Metadados distintos podem apontar para o mesmo conteúdo."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)

    primeira_resposta = criar_resposta(
        url="https://empresa.example/vagas/123",
        coletado_em=DATA_INICIAL,
    )

    segunda_resposta = criar_resposta(
        url="https://empresa.example/vagas/456",
        coletado_em=DATA_SEGUINTE,
    )

    primeiro = armazenamento.salvar(primeira_resposta)

    segundo = armazenamento.salvar(segunda_resposta)

    assert primeiro.corpo_novo is True
    assert segundo.corpo_novo is False
    assert primeiro.caminho_corpo == segundo.caminho_corpo
    assert primeiro.caminho_metadados != segundo.caminho_metadados

    corpos = list((tmp_path / "corpos").rglob("*.bin*"))

    respostas = list((tmp_path / "respostas").rglob("*.json"))

    assert len(corpos) == 1
    assert len(respostas) == 2


def test_resposta_avulsa_guarda_contexto_nulo(
    tmp_path: Path,
) -> None:
    """Coletas antigas sem catálogo devem continuar armazenáveis."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)

    resultado = armazenamento.salvar(
        criar_resposta(
            alvo_id=None,
            empresa_nome=None,
            numero_pagina=1,
            tipo_pagina=TipoPaginaColeta.AVULSA,
        )
    )

    metadados = json.loads(
        resultado.caminho_metadados.read_text(
            encoding="utf-8",
        )
    )

    assert metadados["versao_schema"] == 3
    assert metadados["alvo_id"] is None
    assert metadados["empresa_nome"] is None
    assert metadados["numero_pagina"] == 1
    assert metadados["tipo_pagina"] == TipoPaginaColeta.AVULSA.value


def test_contextos_de_pagina_diferentes_geram_eventos_diferentes(
    tmp_path: Path,
) -> None:
    """A mesma URL pode exercer papéis diferentes durante uma coleta."""

    armazenamento = ArmazenamentoBrutoLocal(tmp_path)

    pagina_inicial = armazenamento.salvar(
        criar_resposta(
            numero_pagina=1,
            tipo_pagina=TipoPaginaColeta.INICIAL,
        )
    )

    detalhe_vaga = armazenamento.salvar(
        criar_resposta(
            numero_pagina=2,
            tipo_pagina=TipoPaginaColeta.DETALHE_VAGA,
        )
    )

    # O HTML é idêntico e continua deduplicado.
    assert pagina_inicial.caminho_corpo == detalhe_vaga.caminho_corpo

    # Cada contexto representa um evento de coleta diferente.
    assert pagina_inicial.caminho_metadados != detalhe_vaga.caminho_metadados

    arquivos_metadados = list(
        (tmp_path / "respostas").rglob("*.json"),
    )

    assert len(arquivos_metadados) == 2


def test_corpo_novo_e_gravado_comprimido_e_volta_igual(tmp_path) -> None:
    armazenamento = ArmazenamentoBrutoLocal(tmp_path)
    resposta = criar_resposta(corpo=CORPO_HTML * 40)

    resultado = armazenamento.salvar(resposta)

    assert resultado.caminho_corpo.suffix == ".gz"
    assert resultado.caminho_corpo.read_bytes()[:2] == b"\x1f\x8b"
    assert ler_corpo_bruto(resultado.caminho_corpo) == resposta.corpo
    assert resultado.caminho_corpo.stat().st_size < len(resposta.corpo)


def test_corpo_no_formato_antigo_continua_sendo_reutilizado(tmp_path) -> None:
    resposta = criar_resposta(corpo=CORPO_HTML * 40)
    antigo = tmp_path / "corpos" / resposta.hash_conteudo[:2] / f"{resposta.hash_conteudo}.bin"
    antigo.parent.mkdir(parents=True)
    antigo.write_bytes(resposta.corpo)

    resultado = ArmazenamentoBrutoLocal(tmp_path).salvar(resposta)

    assert resultado.caminho_corpo == antigo
    assert resultado.corpo_novo is False
    assert ler_corpo_bruto(resultado.caminho_corpo) == resposta.corpo
    assert not list(tmp_path.rglob("*.gz"))
