"""Aplica a leitura completa à amostra e compara com os payloads atuais.

Entrada (geradas em outputs/especificacao/):
- amostra_150.json: payloads atuais da amostra (o "antes");
- anuncios_amostra.json: anúncio de cada vaga no MongoDB (referência bruta);

Saída: payloads_depois.json (com _diagnostico) e a tabela de aceite no terminal.
Nada é gravado no MongoDB nem enviado à API.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from observatorio_vagas.crawling.raw_storage import ler_corpo_bruto
from observatorio_vagas.extraction.leitura.camadas import InventarioPagina, montar_inventario
from observatorio_vagas.extraction.leitura.interpretacao import ler_vaga, validar_apply_url
from observatorio_vagas.extraction.url_candidatura import extrair_url_candidatura_html

PASTA = Path("outputs/especificacao")
RAW = Path("data/raw")
PLATAFORMAS = ("abler", "quickin", "randstad", "lever", "sites_proprios")
BARRA_N = chr(92) + "n"
ENTIDADE = re.compile(r"&(amp|lt|gt|quot|nbsp|#\d+);")


def _corpo(referencia: str) -> tuple[dict, bytes]:
    meta = json.loads((RAW / referencia).read_text(encoding="utf-8"))
    return meta, ler_corpo_bruto(RAW / meta["caminho_corpo"])


def _atributos_abler(corpo: bytes, id_externo: str) -> dict | None:
    alvo = id_externo.removeprefix("abler-")
    for item in json.loads(corpo).get("data", []):
        if str(item.get("id")) == alvo:
            return item.get("attributes") or {}
    return None


def ler_amostra() -> list[dict]:
    amostra = json.loads((PASTA / "amostra_150.json").read_text(encoding="utf-8"))
    anuncios = json.loads((PASTA / "anuncios_amostra.json").read_text(encoding="utf-8"))
    depois = []

    for antes in amostra:
        anuncio = anuncios[antes["externalJobPostingId"]]
        meta, corpo = _corpo(anuncio["referencia_bruta"])
        url = (
            meta["url_final"]
            if "json" not in (meta.get("tipo_conteudo") or "")
            else (anuncio.get("url") or meta["url_final"])
        )
        eh_json = "json" in (meta.get("tipo_conteudo") or "")

        inv = (
            InventarioPagina(url=url)
            if eh_json
            else montar_inventario(corpo, url=meta["url_final"])
        )
        apply_html = (
            None if eh_json else extrair_url_candidatura_html(corpo, url_base=meta["url_final"])
        )
        leitura = ler_vaga(
            inv,
            titulo=antes["title"],
            id_externo=antes["externalJobPostingId"],
            url_vaga=url,
            documento=anuncio.get("campos_estruturados") or {},
            atributos_plataforma=_atributos_abler(corpo, antes["externalJobPostingId"])
            if "abler" in url
            else None,
            url_candidatura_html=apply_html.url if apply_html else None,
        )
        depois.append(
            {
                "_plataforma": antes["_plataforma"],
                **leitura.campos,
                "_diagnostico": leitura.diagnostico,
            }
        )

    (PASTA / "payloads_depois.json").write_text(
        json.dumps(depois, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return depois


def _tem(vaga: dict, campo: str) -> bool:
    atual = vaga
    for parte in campo.split("."):
        atual = atual.get(parte) if isinstance(atual, dict) else None
    return atual not in (None, "", [], {})


def _texto(vaga: dict) -> str:
    return " ".join(str(vaga.get(k) or "") for k in ("title", "description")) + str(
        (vaga.get("location") or {}).get("address", "")
    )


def tabela(antes: list[dict], depois: list[dict]) -> None:
    campos = [
        "salary",
        "workplaceTypes",
        "employmentStatus",
        "experienceLevel",
        "expireAt",
        "company.description",
        "company.logoUrl",
        "company.nationalRegister",
        "company.industries",
        "company.recruiterName",
        "location.postalCode",
        "location.geolocation",
    ]
    print("\n## PREENCHIMENTO (antes -> depois)")
    print(f"{'campo':26}" + "".join(f"{p:>18}" for p in PLATAFORMAS))
    for campo in campos:
        linha = f"{campo:26}"
        for p in PLATAFORMAS:
            a = [v for v in antes if v["_plataforma"] == p]
            d = [v for v in depois if v["_plataforma"] == p]
            linha += f"{sum(_tem(v, campo) for v in a) / len(a):>8.0%} -> {sum(_tem(v, campo) for v in d) / len(d):>4.0%}"
        print(linha)

    def apply_ok(v: dict) -> bool:
        return (
            validar_apply_url(
                v["company"]["applyUrl"], url_vaga=v["company"]["applyUrl"], id_vaga=None
            )
            is None
            and not re.search(r"(login|sign-?in|sitemap)", v["company"]["applyUrl"], re.I)
            and (
                re.search(r"[0-9a-f]{8,}|\d{4,}|JP-\d+", v["company"]["applyUrl"], re.I) is not None
            )
        )

    def contar(lista, f):
        return sum(1 for v in lista if f(v))

    print("\n## CRITÉRIOS (antes -> depois, na amostra de 150)")
    criterios = [
        ("Vagas com _diagnostico", lambda v: "_diagnostico" in v),
        ("applyUrl com ID da vaga (sem login/sitemap/raiz)", apply_ok),
        (
            "Salário 0/0",
            lambda v: (
                _tem(v, "salary") and not v["salary"].get("min") and not v["salary"].get("max")
            ),
        ),
        ("\\n literal nos textos", lambda v: BARRA_N in _texto(v)),
        ("Entidades HTML nos textos", lambda v: bool(ENTIDADE.search(_texto(v)))),
        (
            "Gerente/Manager -> DIRECTOR",
            lambda v: (
                v.get("experienceLevel") == "DIRECTOR"
                and re.search(r"\b(gerente|manager)\b", v["title"], re.I)
                and not re.search(r"diretor|director", v["title"], re.I)
            ),
        ),
        (
            "Interno -> INTERNSHIP",
            lambda v: (
                v.get("experienceLevel") == "INTERNSHIP"
                and re.search(r"\bintern[oa]\b", v["title"], re.I)
                and not re.search(r"est[aá]gi", v["title"], re.I)
            ),
        ),
        (
            "Quickin expireAt = criação + 90",
            lambda v: v["_plataforma"] == "quickin" and _tem(v, "expireAt"),
        ),
    ]
    for nome, f in criterios:
        print(f"- {nome:48} {contar(antes, f):4} -> {contar(depois, f):4}")

    def cobertura(nome: str, plataforma: str, campo: str, condicao=lambda v: True) -> None:
        alvo = [v for v in depois if v["_plataforma"] == plataforma and condicao(v)]
        ok = sum(_tem(v, campo) for v in alvo)
        print(f"- {nome:48} {ok}/{len(alvo)}")

    print("\n## METAS POR PLATAFORMA (depois)")
    origem = lambda v, c: (v["_diagnostico"]["campos"].get(c) or {}).get("origem", "")  # noqa: E731
    for campo in ("salary", "workplaceTypes", "employmentStatus"):
        cobertura(
            f"Abler: {campo} quando existe na API",
            "abler",
            campo,
            lambda v, c=campo: (
                c in str(v["_diagnostico"]["campos"].get(c, {}).get("origem", ""))
                or origem(v, c).startswith("plataforma")
                or not v["_diagnostico"]["campos"].get(c, {}).get("vazio")
                or "plataforma"
                not in str(v["_diagnostico"]["campos"].get(c, {}).get("procurado_em", []))
            ),
        )
    cobertura("Quickin: workplaceTypes do estado/cabeçalho", "quickin", "workplaceTypes")
    cobertura("Lever: workplaceTypes do cabeçalho", "lever", "workplaceTypes")
    cobertura("Lever: employmentStatus do commitment", "lever", "employmentStatus")
    q = [v for v in depois if v["_plataforma"] == "quickin"]
    print(
        f"- {'Quickin: descrição contém Requisitos':48} {sum('Requisitos' in (v.get('description') or '') for v in q)}/{len(q)}"
    )

    print("\n## SEÇÃO 5 (não pode piorar)")
    pares = list(zip(antes, depois, strict=True))
    print(f"- title igual: {sum(a['title'] == d['title'] for a, d in pares)}/150")
    print(
        f"- externalJobPostingId igual: {sum(a['externalJobPostingId'] == d['externalJobPostingId'] for a, d in pares)}/150"
    )
    print(f"- CREATE: {sum(d['jobPostingOperationType'] == 'CREATE' for d in depois)}/150")
    r = [(a, d) for a, d in pares if a["_plataforma"] == "randstad"]
    print(
        f"- Randstad expireAt igual: {sum(a.get('expireAt', '')[:10] == d.get('expireAt', '')[:10] for a, d in r)}/{len(r)}"
    )
    print(
        f"- Randstad salário igual (fora os 0/0): {sum(a.get('salary') == d.get('salary') for a, d in r if (a.get('salary') or {}).get('min'))}/{sum(1 for a, _ in r if (a.get('salary') or {}).get('min'))}"
    )

    print("\n## NAO_MAPEADO MAIS FREQUENTE")
    contagem = Counter(
        f"{item.get('origem')}:{item.get('rotulo') or item.get('secao')}"
        for v in depois
        for item in v["_diagnostico"]["nao_mapeado"]
    )
    for chave, n in contagem.most_common(15):
        print(f"- {n:4}  {chave}")


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="backslashreplace")
    depois = ler_amostra()
    antes = json.loads((PASTA / "amostra_150.json").read_text(encoding="utf-8"))
    tabela(antes, depois)
