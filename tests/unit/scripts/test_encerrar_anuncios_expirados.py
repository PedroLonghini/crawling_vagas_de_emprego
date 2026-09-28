"""Testes do fechamento de anúncios que já passaram da expiração."""

from datetime import UTC, date, datetime

from scripts.encerrar_anuncios_expirados import encerrar_expirados, esta_expirado

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.enums import Fonte, StatusAnuncio


class RepositorioFalso:
    """Registra as atualizações sem acessar o MongoDB."""

    def __init__(self) -> None:
        self.salvos = []

    def salvar(self, anuncio):
        self.salvos.append(anuncio)
        return anuncio


def criar_anuncio(
    *,
    expira_em: date,
    status: StatusAnuncio,
) -> AnuncioVaga:
    """Cria um anúncio válido e mínimo para testar a alteração de status."""

    return AnuncioVaga.model_validate(
        {
            "fonte": Fonte.PAGINA_CARREIRAS,
            "id_externo": f"vaga-{expira_em.isoformat()}-{status.value}",
            "url": "https://empresa.example.com/carreiras/vaga",
            "titulo_original": "Pessoa Analista",
            "expira_em": expira_em,
            "status": status,
            "hash_conteudo": "a" * 64,
            "referencia_bruta": "raw/teste.json",
        }
    )


def test_data_sem_horario_permanece_valida_no_proprio_dia() -> None:
    referencia = datetime(2026, 9, 25, 18, tzinfo=UTC)

    assert not esta_expirado(date(2026, 9, 25), momento_referencia=referencia)
    assert esta_expirado(date(2026, 9, 24), momento_referencia=referencia)


def test_encerrar_expirados_atualiza_sem_apagar_historico() -> None:
    referencia = datetime(2026, 9, 25, 12, tzinfo=UTC)
    expirado = criar_anuncio(
        expira_em=date(2026, 9, 24),
        status=StatusAnuncio.ATIVO,
    )
    valido = criar_anuncio(
        expira_em=date(2026, 9, 25),
        status=StatusAnuncio.ATIVO,
    )
    ja_encerrado = criar_anuncio(
        expira_em=date(2026, 9, 20),
        status=StatusAnuncio.ENCERRADO,
    )
    repositorio = RepositorioFalso()

    resultado = encerrar_expirados(
        [expirado, valido, ja_encerrado],
        repositorio=repositorio,
        confirmar=True,
        momento_referencia=referencia,
    )

    assert resultado.avaliados == 3
    assert resultado.expirados == 2
    assert resultado.atualizados == 1
    assert resultado.ja_encerrados == 1
    assert len(repositorio.salvos) == 1
    assert repositorio.salvos[0].id == expirado.id
    assert repositorio.salvos[0].status is StatusAnuncio.ENCERRADO
    assert repositorio.salvos[0].primeira_observacao_em == expirado.primeira_observacao_em
