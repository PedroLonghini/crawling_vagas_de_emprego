"""Testes da interface segura da fila do Empregos."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from scripts import preparar_fila_empregos

from observatorio_vagas.integrations.empregos import (
    ItemFilaEmpregos,
    MotivoFilaEmpregos,
    ResultadoFilaEmpregos,
    SituacaoItemFilaEmpregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
)


def test_parser_usa_catalogo_central_e_limite_seguro() -> None:
    """O comando deve funcionar sem repetir caminhos operacionais."""

    opcoes = preparar_fila_empregos.criar_parser().parse_args([])

    assert opcoes.catalogo == Path("config/catalogo_fontes.csv")
    assert opcoes.limite == 100
    assert not opcoes.somente_catalogo


def test_parser_aceita_escopo_do_catalogo() -> None:
    """A rotina diária pode excluir anúncios de outros catálogos."""

    opcoes = preparar_fila_empregos.criar_parser().parse_args(["--somente-catalogo"])

    assert opcoes.somente_catalogo


def test_parser_aceita_filtro_por_data_de_publicacao() -> None:
    """A fila deve permitir isolar as vagas publicadas em um dia."""

    opcoes = preparar_fila_empregos.criar_parser().parse_args(["--publicados-em", "2026-09-17"])

    assert opcoes.publicados_em == date(2026, 9, 17)
    assert not opcoes.publicados_ontem


def test_limite_invalido_para_antes_de_consultar_mongodb(capsys: object) -> None:
    """Uma entrada insegura deve falhar antes da criação de repositórios."""

    codigo = preparar_fila_empregos.executar(["--limite", "0"])

    assert codigo == 2
    assert "limite deve estar entre 1 e 50000" in capsys.readouterr().out


def test_item_json_preserva_ids_bloqueios_e_percentual() -> None:
    """O relatório exportado precisa continuar auditável."""

    item = ItemFilaEmpregos(
        anuncio_id=UUID("10000000-0000-4000-8000-000000000001"),
        vaga_id=UUID("20000000-0000-4000-8000-000000000002"),
        empresa_id=UUID("30000000-0000-4000-8000-000000000003"),
        alvo_id="empresa_teste",
        titulo="Pessoa Desenvolvedora",
        situacao=SituacaoItemFilaEmpregos.BLOQUEADA,
        campos_preenchidos=12,
        total_campos=24,
        bloqueios=(
            MotivoFilaEmpregos(
                codigo="fonte_sem_permissao",
                mensagem="A fonte não permite republicação.",
            ),
        ),
    )

    documento = preparar_fila_empregos._item_para_json(item)

    assert documento["situacao"] == "bloqueada"
    assert documento["percentual_preenchimento"] == 50.0
    assert documento["bloqueios"][0]["codigo"] == "fonte_sem_permissao"


def test_exporta_payload_e_manifesto_sem_operacao_externa(tmp_path: Path) -> None:
    """A saída local precisa conter a identidade e o corpo já aprovado."""

    item = ItemFilaEmpregos(
        anuncio_id=UUID("10000000-0000-4000-8000-000000000001"),
        vaga_id=UUID("20000000-0000-4000-8000-000000000002"),
        empresa_id=UUID("30000000-0000-4000-8000-000000000003"),
        alvo_id="fonte_aberta",
        titulo="Pessoa Desenvolvedora",
        situacao=SituacaoItemFilaEmpregos.ELEGIVEL,
        chave_idempotencia="a" * 64,
        preparacao=ResultadoPreparacaoEmpregos(
            relatorio=None,  # type: ignore[arg-type]
            elegibilidade=None,  # type: ignore[arg-type]
            payload={"title": "Pessoa Desenvolvedora"},
        ),
    )

    diretorio = preparar_fila_empregos._exportar_payloads_locais(
        preparar_fila_empregos.ResultadoFilaEmpregos(itens=(item,)),
        diretorio_base=tmp_path,
        gerado_em=datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
    )

    manifesto = (diretorio / "manifesto.json").read_text(encoding="utf-8")
    payload = (diretorio / "payloads" / f"{'a' * 24}.json").read_text(encoding="utf-8")
    payloads_unificados = json.loads(
        (diretorio / "payloads_unificados.json").read_text(encoding="utf-8")
    )

    assert "PREPARACAO_LOCAL_SEM_API" in manifesto
    assert '"payloads_exportados": 1' in manifesto
    assert '"title": "Pessoa Desenvolvedora"' in payload
    assert '"proveniencia_fonte": null' in payload
    assert '"arquivo_payloads_unificados": "payloads_unificados.json"' in manifesto
    assert payloads_unificados == [{"title": "Pessoa Desenvolvedora"}]


def test_exporta_amostra_das_bloqueadas_por_motivo_e_site(tmp_path: Path) -> None:
    from types import SimpleNamespace

    def anuncio(numero: int, host: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=UUID(f"20000000-0000-4000-8000-{numero:012d}"),
            url=f"https://www.{host}/vaga/{numero}",
            fonte=SimpleNamespace(value="pagina_carreiras"),
            referencia_bruta="respostas/x.json",
            id_externo=f"ext-{numero}",
            titulo_original=f"Vaga {numero}",
            empresa_original=None,
            localidade_original=None,
            endereco_original=None,
            cep_original=None,
            salario_original=None,
            modalidade_original=None,
            regime_original=None,
            senioridade_original=None,
            publicado_em=None,
            expira_em=None,
            url_candidatura=None,
            descricao_original="x" * 5000,
            campos_estruturados={"title": "t", "_leitura": {}},
        )

    def item(a: SimpleNamespace) -> ItemFilaEmpregos:
        return ItemFilaEmpregos(
            anuncio_id=a.id,
            alvo_id="alvo",
            titulo=a.titulo_original,
            empresa_id=None,
            vaga_id=None,
            situacao=SituacaoItemFilaEmpregos.BLOQUEADA,
            bloqueios=(MotivoFilaEmpregos("empresa_nao_associada", None, "sem empresa"),),
        )

    anuncios = tuple(anuncio(n, "a.example") for n in range(1, 6)) + (anuncio(9, "b.example"),)
    resultado = ResultadoFilaEmpregos(itens=tuple(item(a) for a in anuncios))

    pasta = preparar_fila_empregos._exportar_bloqueadas(
        resultado,
        anuncios,
        diretorio_base=tmp_path,
        gerado_em=datetime(2026, 10, 5, tzinfo=UTC),
        amostra=2,
    )

    resumo = json.loads((pasta / "resumo.json").read_text(encoding="utf-8"))
    assert resumo["total_bloqueadas"] == 6
    assert resumo["por_motivo"] == {"empresa_nao_associada": 6}
    por_site = {r["site"]: (r["bloqueadas"], r["exportadas"]) for r in resumo["por_motivo_e_site"]}
    assert por_site == {"a.example": (5, 2), "b.example": (1, 1)}
    arquivos = list((pasta / "empresa_nao_associada" / "a.example").glob("*.json"))
    assert len(arquivos) == 2
    documento = json.loads(arquivos[0].read_text(encoding="utf-8"))
    assert documento["url_anuncio"].startswith("https://www.a.example/vaga/")
    assert documento["anuncio_extraido"]["tamanho_da_descricao"] == 5000
    assert len(documento["anuncio_extraido"]["descricao_original"]) < 2100
    assert "campos_estruturados_chaves" in documento
    assert documento["payload_previa"]["title"].startswith("Vaga ")
    assert documento["payload_previa"]["company"]["nationalRegister"] == "00.000.000/0000-00"
    assert "company.name" in documento["campos_obrigatorios_sem_valor"]
    assert "NÃO PUBLICAR".casefold() in documento["AVISO"].casefold().replace("nao", "não")
    previas = json.loads((pasta / "previas_bloqueadas.json").read_text(encoding="utf-8"))
    assert len(previas) == 3
    assert all("NAO_PUBLICAR" in previa for previa in previas)
