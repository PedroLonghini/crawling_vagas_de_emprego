"""O que a varredura semanal tira do Empregos."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.integrations.empregos.remocao import (
    MotivoRemocao,
    RemovedorNaoConfigurado,
    SituacaoRemocao,
    encontrar_duplicatas_publicadas,
    montar_fila_remocao,
    remover_publicacoes,
)

INICIO = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def publicacao(
    numero: int,
    *,
    assinatura: str | None = "a" * 64,
    completa: str | None = "c" * 64,
    dominio: str | None = "site-a.com.br",
    situacao: SituacaoPublicacaoEmpregos = SituacaoPublicacaoEmpregos.SUCESSO,
) -> OperacaoPublicacaoEmpregos:
    criado = INICIO + timedelta(days=numero)
    operacao = OperacaoPublicacaoEmpregos(
        vaga_id=uuid4(),
        anuncio_id=uuid4(),
        external_job_posting_id=f"VAGA-{numero}",
        operation_type="CREATE",
        payload_sha256="b" * 64,
        assinatura_conteudo=assinatura,
        assinatura_descricao_completa=completa,
        dominio_origem=dominio,
        criado_em=criado,
        atualizado_em=criado,
    )
    if situacao is SituacaoPublicacaoEmpregos.PREPARADA:
        return operacao
    return operacao.iniciar_envio(momento=criado).concluir(
        situacao=situacao,
        momento=criado,
        status_http=201 if situacao is SituacaoPublicacaoEmpregos.SUCESSO else None,
    )


def test_duplicata_entre_sites_mantem_a_mais_antiga():
    antiga = publicacao(1, dominio="site-a.com.br")
    copia = publicacao(2, dominio="agregador.com.br", completa="d" * 64)

    duplicatas = encontrar_duplicatas_publicadas([copia, antiga])

    assert [item.operacao.id for item in duplicatas] == [copia.id]
    assert duplicatas[0].motivo is MotivoRemocao.DUPLICATA
    assert str(antiga.anuncio_id) in duplicatas[0].detalhe


def test_mesmo_site_so_e_duplicata_com_a_descricao_inteira_igual():
    primeira = publicacao(1)
    outra_vaga = publicacao(2, completa="d" * 64)  # outro turno, mesma loja
    copia = publicacao(3)  # cópia idêntica republicada pelo site

    duplicatas = encontrar_duplicatas_publicadas([primeira, outra_vaga, copia])

    assert [item.operacao.id for item in duplicatas] == [copia.id]


def test_sem_assinatura_nao_entra_na_comparacao():
    assert (
        encontrar_duplicatas_publicadas(
            [publicacao(1, assinatura=None), publicacao(2, assinatura=None)]
        )
        == []
    )


def test_fila_junta_encerradas_e_duplicatas_sem_repetir_e_so_o_que_pode_estar_no_ar():
    encerrada = publicacao(1)
    copia_da_encerrada = publicacao(2, dominio="agregador.com.br")
    preparada = publicacao(3, assinatura="e" * 64, situacao=SituacaoPublicacaoEmpregos.PREPARADA)
    indeterminada = publicacao(
        4, assinatura="f" * 64, situacao=SituacaoPublicacaoEmpregos.INDETERMINADA
    )

    fila = montar_fila_remocao(
        publicadas=[encerrada, copia_da_encerrada, preparada, indeterminada],
        anuncios_encerrados=[encerrada.anuncio_id, preparada.anuncio_id, indeterminada.anuncio_id],
    )

    assert [(item.operacao.id, item.motivo) for item in fila] == [
        (encerrada.id, MotivoRemocao.VAGA_ENCERRADA_NA_ORIGEM),
        (indeterminada.id, MotivoRemocao.VAGA_ENCERRADA_NA_ORIGEM),
        (copia_da_encerrada.id, MotivoRemocao.DUPLICATA),
    ]


def test_sem_api_de_remocao_tudo_fica_simulado_e_uma_falha_nao_para_as_outras():
    encerrada = publicacao(1)
    fila = montar_fila_remocao(
        publicadas=[encerrada, publicacao(2, assinatura="e" * 64)],
        anuncios_encerrados=[encerrada.anuncio_id],
    )
    assert [r.situacao for r in remover_publicacoes(fila, removedor=RemovedorNaoConfigurado())] == [
        SituacaoRemocao.SIMULADA
    ]

    class Quebrado:
        def remover(self, item):
            raise RuntimeError("API fora do ar")

    resultados = remover_publicacoes(fila * 2, removedor=Quebrado())
    assert [r.situacao for r in resultados] == [SituacaoRemocao.FALHA] * 2
    assert resultados[0].mensagem == "API fora do ar"
