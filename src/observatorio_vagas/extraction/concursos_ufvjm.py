"""Adapta o CSV licenciado da UFVJM sem confundir validade com inscrições."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


def extrair_concursos_ufvjm(
    linhas: Sequence[Mapping[str, str]], *, url: str
) -> tuple[tuple[dict[str, Any], ...], int, int]:
    """Aceita somente editais explicitamente com inscrições abertas.

    Em andamento pode significar resultado, homologação ou cadastro reserva.
    Data final da validade NÃO é data limite para novas candidaturas.
    """
    vagas: list[dict[str, Any]] = []
    ignoradas = invalidas = 0
    for linha in linhas:
        situacao = linha.get("situacao", "").casefold()
        if not re.search(r"inscri[çc][õo]es\s+abertas", situacao):
            ignoradas += 1
            continue
        edital = (linha.get("no_do_edital") or linha.get("n_do_edital") or "").strip()
        finalidade = linha.get("finalidade_do_concurso_processo_seletivo", "").strip()
        if not edital or not finalidade:
            invalidas += 1
            continue
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": {"value": f"ufvjm:{edital}"},
                "title": f"{finalidade} - {edital}",
                "description": (
                    f"{finalidade}. {edital}. Situação informada: {situacao}. "
                    f"{linha.get('observacoes_metodologicas', '')} "
                    f"Fonte: UFVJM, {url}. Dados sob Creative Commons Attribution. "
                    "Confira os cargos, requisitos e inscrições no edital original."
                ),
                "url": linha.get("url_da_fonte") or url,
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Universidade Federal dos Vales do Jequitinhonha e Mucuri",
                },
                "_observatorio_ckan": {"portal": "dados.ufvjm.edu.br", "edital": edital},
            }
        )
    return tuple(vagas), ignoradas, invalidas
