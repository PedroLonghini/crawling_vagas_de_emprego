"""Testes do extrator específico das vagas do Tor Project."""

from observatorio_vagas.extraction.tor_project import extrair_job_posting_tor_project


HTML_VAGA = b"""
<main role="main">
  <h2 class="mx-auto display-3 text-white">UX Lead</h2>
  <p>The Tor Project seeks a UX Lead to guide research, design and delivery of
  privacy-preserving products with a distributed international team.</p>
  <p>This is a full-time, remote position with flexible geographic location.
  Applicants will collaborate across time zones and document their work.</p>
  <p>Deadline: September 30, 2026 by 21:00 UTC.</p>
  <h2>How to apply</h2><p>Apply using <a href="https://www.idealist.org/example">this link</a>.</p>
</main>
"""


def test_extrai_vaga_inglesa_com_cargo_local_e_candidatura() -> None:
    resultado = extrair_job_posting_tor_project(
        HTML_VAGA,
        url="https://tor.eff.org/about/jobs/ux-lead/",
    )

    assert resultado.dominio_reconhecido
    vaga = resultado.vagas[0]
    assert vaga["identifier"] == "ux-lead"
    assert vaga["title"] == "UX Lead"
    assert vaga["jobLocationType"] == "TELECOMMUTE"
    assert vaga["jobLocation"]["address"]["addressLocality"] == "Remote"
    assert vaga["validThrough"] == "2026-09-30"
    assert vaga["_observatorio_apply_url"] == "https://www.idealist.org/example"


def test_recusa_traducao_listagem_e_pagina_do_conselho() -> None:
    for url in (
        "https://tor.eff.org/ar/about/jobs/ux-lead/",
        "https://tor.eff.org/about/jobs/",
        "https://tor.eff.org/about/jobs/board-of-directors/",
    ):
        resultado = extrair_job_posting_tor_project(HTML_VAGA, url=url)
        assert resultado.dominio_reconhecido
        assert resultado.vagas == ()
