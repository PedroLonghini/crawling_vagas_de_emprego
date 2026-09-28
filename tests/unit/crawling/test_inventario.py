"""Testes do inventário de respostas brutas."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from observatorio_vagas.crawling.inventario import (
    ErroInventarioBruto,
    carregar_inventario_bruto,
)
from observatorio_vagas.domain.enums import TipoPaginaColeta


def salvar_metadados(
    diretorio_base: Path,
    *,
    nome: str,
    coletado_em: str,
    versao_schema: int = 3,
    alvo_id: str | None = "empresa_1",
    empresa_nome: str | None = "Empresa Um",
    numero_pagina: int = 2,
    tipo_pagina: str = TipoPaginaColeta.DETALHE_VAGA.value,
) -> Path:
    """Cria um JSON de coleta usado somente pelos testes."""

    caminho = diretorio_base / "respostas" / "outra" / "2026" / "08" / "21" / f"{nome}.json"

    caminho.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    metadados = {
        "versao_schema": versao_schema,
        "alvo_id": alvo_id,
        "empresa_nome": empresa_nome,
        "fonte": "outra",
        "url_solicitada": "https://empresa.example/carreiras",
        "url_final": "https://empresa.example/carreiras",
        "status_http": 200,
        "tamanho_bytes": 500,
        "tipo_conteudo": "text/html; charset=utf-8",
        "codificacao": "utf-8",
        "hash_conteudo": "a" * 64,
        "coletado_em": coletado_em,
        "caminho_corpo": "corpos/ab/conteudo.bin",
    }

    # Somente o schema 3 possui contexto da página.
    #
    # Isso permite criar arquivos antigos verdadeiros nos testes.
    if versao_schema >= 3:
        metadados["numero_pagina"] = numero_pagina
        metadados["tipo_pagina"] = tipo_pagina

    caminho.write_text(
        json.dumps(
            metadados,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return caminho


def test_diretorio_ainda_nao_criado_retorna_inventario_vazio(
    tmp_path: Path,
) -> None:
    """A tela pode abrir normalmente antes da primeira coleta."""

    resultado = carregar_inventario_bruto(
        tmp_path / "raw",
    )

    assert resultado == ()


def test_carrega_coletas_da_mais_recente_para_a_mais_antiga(
    tmp_path: Path,
) -> None:
    """O dashboard deve mostrar primeiro a coleta mais recente."""

    diretorio_base = tmp_path / "raw"

    salvar_metadados(
        diretorio_base,
        nome="antiga",
        coletado_em="2026-08-21T10:00:00+00:00",
    )

    salvar_metadados(
        diretorio_base,
        nome="recente",
        coletado_em="2026-08-21T12:00:00+00:00",
    )

    resultado = carregar_inventario_bruto(
        diretorio_base,
    )

    assert len(resultado) == 2

    assert resultado[0].coletado_em == datetime(
        2026,
        8,
        21,
        12,
        0,
        tzinfo=UTC,
    )

    assert resultado[1].coletado_em == datetime(
        2026,
        8,
        21,
        10,
        0,
        tzinfo=UTC,
    )


def test_preserva_empresa_e_contexto_da_pagina(
    tmp_path: Path,
) -> None:
    """A coleta deve continuar ligada à empresa e ao tipo da página."""

    diretorio_base = tmp_path / "raw"

    salvar_metadados(
        diretorio_base,
        nome="evento",
        coletado_em="2026-08-21T12:00:00+00:00",
        alvo_id="empresa_123",
        empresa_nome="Empresa Teste",
        numero_pagina=2,
        tipo_pagina=TipoPaginaColeta.DETALHE_VAGA.value,
    )

    registro = carregar_inventario_bruto(
        diretorio_base,
    )[0]

    assert registro.versao_schema == 3
    assert registro.alvo_id == "empresa_123"
    assert registro.empresa_nome == "Empresa Teste"
    assert registro.numero_pagina == 2
    assert registro.tipo_pagina == TipoPaginaColeta.DETALHE_VAGA.value
    assert registro.fonte == "outra"
    assert registro.status_http == 200
    assert registro.tamanho_bytes == 500
    assert registro.tipo_conteudo == "text/html; charset=utf-8"
    assert registro.codificacao == "utf-8"
    assert registro.hash_conteudo == "a" * 64
    assert registro.referencia.endswith("evento.json")


@pytest.mark.parametrize(
    "versao_schema",
    [
        1,
        2,
    ],
)
def test_aceita_coleta_antiga_sem_contexto_da_pagina(
    tmp_path: Path,
    versao_schema: int,
) -> None:
    """JSONs das versões 1 e 2 ainda aparecem no inventário."""

    diretorio_base = tmp_path / "raw"

    salvar_metadados(
        diretorio_base,
        nome=f"legado_{versao_schema}",
        coletado_em="2026-08-21T09:00:00+00:00",
        versao_schema=versao_schema,
        alvo_id=None,
        empresa_nome=None,
    )

    registro = carregar_inventario_bruto(
        diretorio_base,
    )[0]

    assert registro.versao_schema == versao_schema
    assert registro.alvo_id is None
    assert registro.empresa_nome is None

    # Como o arquivo antigo não informa o papel da página,
    # o inventário utiliza valores seguros de compatibilidade.
    assert registro.numero_pagina == 1
    assert registro.tipo_pagina == TipoPaginaColeta.AVULSA.value


def test_schema_3_exige_contexto_da_pagina(
    tmp_path: Path,
) -> None:
    """Um JSON novo incompleto precisa ser denunciado."""

    diretorio_base = tmp_path / "raw"

    caminho = salvar_metadados(
        diretorio_base,
        nome="incompleto",
        coletado_em="2026-08-21T09:00:00+00:00",
    )

    metadados = json.loads(
        caminho.read_text(
            encoding="utf-8",
        )
    )

    # Simulamos um arquivo interrompido ou escrito incorretamente.
    metadados.pop("tipo_pagina")

    caminho.write_text(
        json.dumps(
            metadados,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ErroInventarioBruto,
        match="tipo_pagina",
    ):
        carregar_inventario_bruto(diretorio_base)


def test_json_invalido_mostra_arquivo_problematico(
    tmp_path: Path,
) -> None:
    """Um arquivo corrompido deve mostrar qual arquivo tem problema."""

    diretorio_base = tmp_path / "raw"

    caminho = diretorio_base / "respostas" / "outra" / "quebrado.json"

    caminho.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    caminho.write_text(
        "{json incompleto",
        encoding="utf-8",
    )

    with pytest.raises(
        ErroInventarioBruto,
        match="quebrado.json",
    ):
        carregar_inventario_bruto(diretorio_base)
