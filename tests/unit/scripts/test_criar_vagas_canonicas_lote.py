"""A criação de vagas em lote deve gravar o mesmo que a versão individual."""

from scripts import criar_vagas_canonicas

from observatorio_vagas.domain.enums import ModalidadeTrabalho
from observatorio_vagas.extraction import converter_anuncio_em_vaga_canonica

from ..extraction.test_normalizacao_vaga import criar_anuncio


class RepositorioVagasMemoria:
    def __init__(self) -> None:
        self.vagas: dict[object, object] = {}
        self.consultas = 0
        self.lotes = 0

    def salvar(self, vaga):
        self.vagas[vaga.id] = vaga
        return vaga

    def salvar_lote(self, vagas):
        self.lotes += 1
        for vaga in vagas:
            self.vagas[vaga.id] = vaga

    def buscar_por_id(self, vaga_id):
        self.consultas += 1
        return self.vagas.get(vaga_id)

    def buscar_por_ids(self, vaga_ids):
        self.consultas += 1
        return {i: self.vagas[i] for i in vaga_ids if i in self.vagas}


def _cenario():
    novo = criar_anuncio()
    existente = criar_anuncio().model_copy(update={"id_externo": "outra-vaga"})
    sem_empresa = criar_anuncio(associado=False).model_copy(update={"id_externo": "sem-empresa"})

    # Vaga já gravada com modalidade diferente: a atualização deve preservar o resto.
    gravada = converter_anuncio_em_vaga_canonica(existente).model_copy(
        update={"modalidade": ModalidadeTrabalho.PRESENCIAL, "titulo_normalizado": "Antigo"}
    )
    return [novo, existente, sem_empresa], gravada


def test_lote_grava_o_mesmo_que_um_por_um() -> None:
    anuncios, gravada = _cenario()

    individual = RepositorioVagasMemoria()
    individual.salvar(gravada)
    resultado_individual = criar_vagas_canonicas.processar_anuncios(
        anuncios, confirmar=True, repositorio_vagas=individual
    )

    lote = RepositorioVagasMemoria()
    lote.salvar(gravada)
    criadas, reutilizadas, falhas = criar_vagas_canonicas.processar_anuncios_em_lote(
        anuncios, repositorio_vagas=lote
    )

    assert (criadas, reutilizadas, len(falhas)) == resultado_individual == (1, 1, 1)
    assert lote.vagas == individual.vagas
    # Uma consulta e uma gravação para o lote inteiro.
    assert (lote.consultas, lote.lotes) == (1, 1)
