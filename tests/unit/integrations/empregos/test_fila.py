"""Testes da fila somente leitura destinada ao Empregos."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from observatorio_vagas.crawling.catalog import AlvoColeta
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import (
    Fonte,
    SituacaoPublicacaoEmpregos,
    StatusAnuncio,
    StatusPoliticaFonte,
)
from observatorio_vagas.domain.politica_fonte import PoliticaFonte
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.domain.vaga import VagaCanonica
from observatorio_vagas.extraction.normalizacao_vaga import (
    converter_anuncio_em_vaga_canonica,
)
from observatorio_vagas.integrations.empregos import (
    SituacaoItemFilaEmpregos,
    preparar_fila_empregos,
)
from observatorio_vagas.integrations.empregos.fila import (
    ItemFilaEmpregos,
    _deduplicar_itens,
)

AGORA = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class RepositorioEmpresasFalso:
    """Consulta empresas sem qualquer escrita."""

    def __init__(self, *empresas: Empresa) -> None:
        self.empresas = {empresa.id: empresa for empresa in empresas}

    def buscar_por_id(self, empresa_id: UUID) -> Empresa | None:
        return self.empresas.get(empresa_id)


class RepositorioVagasFalso:
    """Consulta vagas canônicas sem qualquer escrita."""

    def __init__(self, *vagas: VagaCanonica) -> None:
        self.vagas = {vaga.id: vaga for vaga in vagas}

    def buscar_por_id(self, vaga_id: UUID) -> VagaCanonica | None:
        return self.vagas.get(vaga_id)


class RepositorioPublicacoesFalso:
    """Devolve uma operação opcional para qualquer chave consultada."""

    def __init__(self, operacao: OperacaoPublicacaoEmpregos | None = None) -> None:
        self.operacao = operacao
        self.chaves_consultadas: list[str] = []

    def buscar_por_chave(self, chave: str) -> OperacaoPublicacaoEmpregos | None:
        self.chaves_consultadas.append(chave)
        return self.operacao


def criar_cenario(
    *,
    politica: StatusPoliticaFonte = StatusPoliticaFonte.APROVADA,
) -> tuple[Empresa, AnuncioVaga, VagaCanonica, AlvoColeta]:
    """Cria entidades completas e ligadas pelo mesmo alvo."""

    empresa = Empresa(
        razao_social="Tecnologia Atlas S.A.",
        nome_fantasia="Tecnologia Atlas",
        cnpj="12.345.678/0001-90",
        descricao="Empresa fictícia especializada em tecnologia.",
    )
    anuncio = AnuncioVaga(
        alvo_id="tecnologia_atlas",
        fonte=Fonte.PAGINA_CARREIRAS,
        id_externo="VAGA-001",
        url="https://tecnologia-atlas.example.com/carreiras/vaga-001",
        empresa_id=empresa.id,
        titulo_original="Pessoa Desenvolvedora Python",
        descricao_original=(
            "Desenvolvimento de aplicações Python, testes automatizados "
            "e integração com produtos digitais. A pessoa colaborará com "
            "engenharia, qualidade e produto durante todo o desenvolvimento."
        ),
        url_candidatura="https://tecnologia-atlas.example.com/carreiras/vaga-001/candidatura",
        localidade_original="São Paulo, SP",
        status=StatusAnuncio.ATIVO,
        hash_conteudo="a" * 64,
        referencia_bruta="corpos/vaga-001.bin",
        primeira_observacao_em=AGORA,
        ultima_observacao_em=AGORA,
    )
    vaga = converter_anuncio_em_vaga_canonica(anuncio)
    alvo = AlvoColeta(
        alvo_id="tecnologia_atlas",
        empresa_nome="Tecnologia Atlas",
        fonte=Fonte.PAGINA_CARREIRAS,
        url_inicial="https://tecnologia-atlas.example.com/carreiras",
        ativa=True,
        limite_paginas=20,
        politica=PoliticaFonte(
            dominio="tecnologia-atlas.example.com",
            status=politica,
            licenca_nome=("CC BY 4.0" if politica is StatusPoliticaFonte.APROVADA else ""),
            licenca_url=(
                "https://creativecommons.org/licenses/by/4.0/deed.pt-br"
                if politica is StatusPoliticaFonte.APROVADA
                else ""
            ),
            republicacao_permitida=(politica is StatusPoliticaFonte.APROVADA),
        ),
    )
    return empresa, anuncio, vaga, alvo


def test_fila_libera_vaga_completa_e_sem_historico() -> None:
    """Uma vaga pronta deve fornecer IDs e chave para revisão final."""

    empresa, anuncio, vaga, alvo = criar_cenario()
    publicacoes = RepositorioPublicacoesFalso()

    resultado = preparar_fila_empregos(
        [anuncio],
        alvos={alvo.alvo_id: alvo},
        repositorio_empresas=RepositorioEmpresasFalso(empresa),
        repositorio_vagas=RepositorioVagasFalso(vaga),
        repositorio_publicacoes=publicacoes,
        momento_referencia=AGORA,
    )

    item = resultado.itens[0]

    assert item.situacao is SituacaoItemFilaEmpregos.ELEGIVEL
    assert item.vaga_id == vaga.id
    assert item.chave_idempotencia is not None
    assert item.campos_preenchidos > 0
    assert len(resultado.elegiveis) == 1
    assert publicacoes.chaves_consultadas == [item.chave_idempotencia]


def test_fila_isola_anuncio_sem_empresa_e_continua() -> None:
    """Uma pré-condição ausente não elimina o item seguinte."""

    empresa, anuncio, vaga, alvo = criar_cenario()
    sem_empresa = anuncio.model_copy(
        update={
            "id": UUID("10000000-0000-4000-8000-000000000001"),
            "empresa_id": None,
        }
    )

    resultado = preparar_fila_empregos(
        [sem_empresa, anuncio],
        alvos={alvo.alvo_id: alvo},
        repositorio_empresas=RepositorioEmpresasFalso(empresa),
        repositorio_vagas=RepositorioVagasFalso(vaga),
        repositorio_publicacoes=RepositorioPublicacoesFalso(),
        momento_referencia=AGORA,
    )

    assert len(resultado.itens) == 2
    assert resultado.itens[0].bloqueios[0].codigo == "empresa_nao_associada"
    assert resultado.itens[1].situacao is SituacaoItemFilaEmpregos.ELEGIVEL


def test_fila_mantem_fonte_somente_coleta_bloqueada() -> None:
    """Cobertura completa não substitui autorização de republicação."""

    empresa, anuncio, vaga, alvo = criar_cenario(
        politica=StatusPoliticaFonte.SOMENTE_COLETA,
    )

    resultado = preparar_fila_empregos(
        [anuncio],
        alvos={alvo.alvo_id: alvo},
        repositorio_empresas=RepositorioEmpresasFalso(empresa),
        repositorio_vagas=RepositorioVagasFalso(vaga),
        repositorio_publicacoes=RepositorioPublicacoesFalso(),
        momento_referencia=AGORA,
    )

    item = resultado.itens[0]

    assert item.situacao is SituacaoItemFilaEmpregos.BLOQUEADA
    assert any(bloqueio.codigo == "fonte_sem_permissao" for bloqueio in item.bloqueios)


def test_fila_nao_oferece_novamente_operacao_com_historico() -> None:
    """Uma chave já registrada precisa de reconciliação, não de novo POST."""

    empresa, anuncio, vaga, alvo = criar_cenario()
    iniciada = OperacaoPublicacaoEmpregos(
        vaga_id=vaga.id,
        anuncio_id=anuncio.id,
        external_job_posting_id="VAGA-001",
        operation_type="CREATE",
        payload_sha256="b" * 64,
        criado_em=AGORA,
        atualizado_em=AGORA,
    ).iniciar_envio(momento=AGORA)
    sucesso = iniciada.concluir(
        situacao=SituacaoPublicacaoEmpregos.SUCESSO,
        momento=AGORA + timedelta(seconds=1),
        status_http=201,
    )

    resultado = preparar_fila_empregos(
        [anuncio],
        alvos={alvo.alvo_id: alvo},
        repositorio_empresas=RepositorioEmpresasFalso(empresa),
        repositorio_vagas=RepositorioVagasFalso(vaga),
        repositorio_publicacoes=RepositorioPublicacoesFalso(sucesso),
        momento_referencia=AGORA,
    )

    item = resultado.itens[0]

    assert item.situacao is SituacaoItemFilaEmpregos.JA_REGISTRADA
    assert item.situacao_publicacao_existente is SituacaoPublicacaoEmpregos.SUCESSO
    assert len(resultado.ja_registradas) == 1


def test_fila_indica_quando_vaga_canonica_ainda_nao_existe() -> None:
    """O relatório deve orientar a execução da etapa anterior."""

    empresa, anuncio, _, alvo = criar_cenario()

    resultado = preparar_fila_empregos(
        [anuncio],
        alvos={alvo.alvo_id: alvo},
        repositorio_empresas=RepositorioEmpresasFalso(empresa),
        repositorio_vagas=RepositorioVagasFalso(),
        repositorio_publicacoes=RepositorioPublicacoesFalso(),
        momento_referencia=AGORA,
    )

    item = resultado.itens[0]

    assert item.situacao is SituacaoItemFilaEmpregos.BLOQUEADA
    assert item.bloqueios[0].codigo == "vaga_nao_encontrada"
    assert item.vaga_id is not None


def test_deduplicacao_remove_mesma_vaga_vinda_de_duas_fontes() -> None:
    """A mesma vaga com crédito de fontes diferentes só gera um payload."""

    def criar_item(alvo_id: str, descricao: str) -> ItemFilaEmpregos:
        return ItemFilaEmpregos(
            anuncio_id=uuid4(),
            titulo="Analista de Dados",
            alvo_id=alvo_id,
            empresa_id=uuid4(),
            vaga_id=uuid4(),
            situacao=SituacaoItemFilaEmpregos.ELEGIVEL,
            preparacao=SimpleNamespace(
                payload={
                    "company": {"name": "Empresa Exemplo"},
                    "location": {"address": "São Paulo, SP"},
                    "description": descricao,
                }
            ),
        )

    itens = _deduplicar_itens(
        (
            criar_item("consultoria", "Atividades da vaga.\n\nFonte: Consultoria"),
            criar_item("empresa", "Atividades da vaga.\n\nFonte: Empresa"),
        )
    )

    assert itens[0].situacao is SituacaoItemFilaEmpregos.ELEGIVEL
    assert itens[1].situacao is SituacaoItemFilaEmpregos.DUPLICADA
    assert itens[1].bloqueios[0].codigo == "duplicada_entre_fontes"
