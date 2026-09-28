"""Testes da extração da API pública do Querido Diário."""

import json

import pytest

from observatorio_vagas.extraction.querido_diario import (
    extrair_job_postings_querido_diario,
)


def _resposta_api(
    excertos: list[object],
) -> bytes:
    """Cria uma resposta mínima semelhante a ``/gazettes``."""

    return json.dumps(
        {
            "total_gazettes": 1,
            "gazettes": [
                {
                    "territory_id": "3550308",
                    "territory_name": "São Paulo",
                    "state_code": "SP",
                    "date": "2026-09-02",
                    "edition": "1234",
                    "is_extra_edition": False,
                    "url": "https://diarios.example/3550308/2026-09-02.pdf",
                    "txt_url": ("https://diarios.example/3550308/2026-09-02.txt"),
                    "checksum": "a" * 64,
                    "excerpts": excertos,
                }
            ],
        },
        ensure_ascii=False,
    ).encode()


def test_converte_excerto_de_selecao_em_candidato_a_vaga() -> None:
    """Termos fortes produzem documento sem inventar CNPJ ou candidatura."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "<em>Processo seletivo</em> simplificado para "
                    "contratação temporária. Há 12 vagas. "
                    "Inscrições até 30/09/2026."
                )
            ]
        )
    )

    assert resultado.diarios_encontrados == 1
    assert resultado.excertos_analisados == 1
    assert resultado.excertos_ignorados == 0
    assert resultado.itens_invalidos == 0
    assert len(resultado.vagas) == 1

    vaga = resultado.vagas[0]

    assert vaga["title"] == "Processo seletivo público - São Paulo/SP"
    assert vaga["datePosted"] == "2026-09-02"
    assert vaga["validThrough"] == "2026-09-30"
    assert vaga["employmentType"] == "TEMPORARY"
    assert vaga["hiringOrganization"]["name"] == "Município de São Paulo"
    assert "taxID" not in vaga["hiringOrganization"]
    assert "_observatorio_apply_url" not in vaga
    assert len(vaga["identifier"]["value"]) == 50
    assert "Querido Diário / Open Knowledge Brasil" in vaga["description"]
    assert "Diário Oficial do Município de São Paulo" in vaga["description"]
    assert "Licença CC BY 4.0" in vaga["description"]
    assert (
        "Documento original: https://diarios.example/3550308/2026-09-02.pdf" in vaga["description"]
    )
    assert vaga["_observatorio_querido_diario"]["attributionRequired"] is True


def test_ignora_excerto_que_nao_representa_oportunidade() -> None:
    """Uma menção administrativa comum não deve virar vaga."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                "Relatório de execução do orçamento e prestação de contas.",
            ]
        )
    )

    assert resultado.vagas == ()
    assert resultado.excertos_ignorados == 1


@pytest.mark.parametrize(
    "excerto",
    [
        (
            "Portaria de nomeação de candidato aprovado em concurso público "
            "para provimento efetivo do cargo de Analista."
        ),
        (
            "Resultado final do processo seletivo. Classificação e convocação "
            "dos candidatos para o cargo de Auxiliar Administrativo."
        ),
        (
            "Edital de convocação referente ao concurso público. Em conformidade "
            "com o edital de abertura, ficam os candidatos aprovados convocados "
            "para o cargo de Assistente Administrativo."
        ),
    ],
)
def test_ignora_mencao_historica_sem_sinal_de_abertura(
    excerto: str,
) -> None:
    """Nomeação ou resultado antigo não representa vaga atualmente aberta."""

    resultado = extrair_job_postings_querido_diario(_resposta_api([excerto]))

    assert resultado.vagas == ()
    assert resultado.excertos_ignorados == 1


def test_extrai_cargo_quando_a_selecao_nao_e_edital() -> None:
    """Seleção de pessoal aberta é aceita sem tratar edital como vaga."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "Processo seletivo para preenchimento "
                    "de vagas no cargo de Dentista. Inscrições até 30/09/2026."
                )
            ]
        )
    )

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Dentista"


def test_extrai_vaga_direta_de_agencia_do_trabalhador() -> None:
    """Vagas do SINE são oportunidades de emprego, mesmo sem seleção pública."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "A Agência do Trabalhador informa vagas disponíveis para "
                    "Técnico de Enfermagem. Interessados devem comparecer ao SINE."
                )
            ]
        )
    )

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Técnico de Enfermagem"


@pytest.mark.parametrize(
    "excerto",
    [
        "Concurso público com vagas disponíveis e inscrições até 30/09/2026.",
        "Edital de processo seletivo com vagas disponíveis e inscrições até 30/09/2026.",
    ],
)
def test_ignora_concurso_e_edital_mesmo_quando_estao_abertos(excerto: str) -> None:
    """O produto não republica concursos nem editais, mesmo na vigência."""

    resultado = extrair_job_postings_querido_diario(_resposta_api([excerto]))

    assert resultado.vagas == ()


def test_reconhece_chamada_publica_aberta_para_contratacao_de_professores() -> None:
    """Chamadas públicas de pessoal não podem depender da palavra vaga."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "Chamada pública para contratação temporária de Professores. "
                    "Inscrições serão realizadas de 01/09/2026 a 15/09/2026."
                )
            ]
        )
    )

    assert len(resultado.vagas) == 1
    assert resultado.vagas[0]["title"] == "Professores"
    assert resultado.vagas[0]["validThrough"] == "2026-09-15"


def test_ignora_chamada_publica_com_inscricoes_encerradas() -> None:
    """O novo reconhecimento não pode publicar uma oportunidade já fechada."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "Chamada pública para contratação temporária de Professores. "
                    "Inscrições encerradas em 15/08/2026."
                )
            ]
        )
    )

    assert resultado.vagas == ()
    assert resultado.excertos_ignorados == 1


def test_associa_cnpj_valido_quando_e_da_prefeitura() -> None:
    """CNPJ ligado nominalmente ao município enriquece a organização."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "Prefeitura Municipal de São Paulo, inscrita no CNPJ "
                    "46.395.000/0001-39. Processo seletivo "
                    "para preenchimento de vagas. Inscrições até 30/09/2026."
                )
            ]
        )
    )

    organizacao = resultado.vagas[0]["hiringOrganization"]

    assert organizacao["taxID"] == "46395000000139"


def test_nao_associa_cnpj_de_terceiro_ao_municipio() -> None:
    """CNPJ sem vínculo textual com a prefeitura não pode ser atribuído a ela."""

    resultado = extrair_job_postings_querido_diario(
        _resposta_api(
            [
                (
                    "A empresa Aurum, CNPJ 17.160.849/0001-25, presta serviços. "
                    "Processo seletivo para preenchimento "
                    "de vagas. Inscrições até 30/09/2026."
                )
            ]
        )
    )

    organizacao = resultado.vagas[0]["hiringOrganization"]

    assert "taxID" not in organizacao


def test_contabiliza_item_incompleto_sem_interromper_a_resposta() -> None:
    """Um diário malformado é isolado e os demais podem ser processados."""

    conteudo = json.dumps(
        {
            "gazettes": [
                {
                    "territory_id": "3550308",
                    "excerpts": ["Processo seletivo com vagas e inscrições abertas."],
                },
                "item inválido",
            ]
        },
        ensure_ascii=False,
    )

    resultado = extrair_job_postings_querido_diario(conteudo)

    assert resultado.vagas == ()
    assert resultado.itens_invalidos == 2


def test_rejeita_json_malformado_com_mensagem_clara() -> None:
    """Falha estrutural da API deve aparecer no relatório do lote."""

    with pytest.raises(
        ValueError,
        match="não contém JSON válido",
    ):
        extrair_job_postings_querido_diario(b"{invalido")
