"""Regras mínimas para licenças usadas pela publicação."""

import pytest

from observatorio_vagas.domain.politica_fonte import licenca_permite_republicacao


@pytest.mark.parametrize(
    "licenca",
    ("CC0 1.0", "CC BY 4.0", "CC BY-SA 4.0", "Creative Commons Attribution", "ODbL 1.0"),
)
def test_licencas_abertas_compativeis_sao_aceitas(licenca: str) -> None:
    assert licenca_permite_republicacao(licenca) is True


@pytest.mark.parametrize(
    "licenca",
    (
        "Permissao expressa com credito a fonte",
        "CC BY-ND 4.0",
        "CC BY-NC 4.0",
        "",
    ),
)
def test_permissoes_ou_licencas_restritivas_sao_recusadas(licenca: str) -> None:
    assert licenca_permite_republicacao(licenca) is False
