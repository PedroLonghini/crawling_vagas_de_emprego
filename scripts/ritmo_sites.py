"""Ritmo de requisições por site: listar, mudar e medir.

    python scripts/ritmo_sites.py listar
    python scripts/ritmo_sites.py definir emploive.com --intervalo 0.35 --simultaneas 3
    python scripts/ritmo_sites.py sondar empregandobrasil.com.br --aplicar

A tabela editável é ``config/ritmo_sites.csv`` (abra no VS Code).
``sondar`` pede páginas do site em ritmos crescentes e para no primeiro sinal
de limite (429, 403, erro 5xx, falha de rede ou latência muito maior).
"""

from __future__ import annotations

import argparse
import contextlib
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from observatorio_vagas.crawling.ritmo_sites import (
    PADRAO,
    caminho_do_arquivo,
    carregar_ritmo,
    definir_ritmo,
    normalizar_dominio,
)

USER_AGENT = "ObservatorioVagas/0.1.0"
NIVEIS_PADRAO = (1, 2, 3, 4, 6)
MARGEM_DE_SEGURANCA = 0.7
LATENCIA_LIMITE = 2.5
IGNORAR_LINKS = re.compile(r"login|apply|api/|\.(css|js|png|jpe?g|svg|ico|pdf)$")


def _baixar(url: str) -> tuple[object, float, bytes]:
    inicio = time.perf_counter()
    try:
        requisicao = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(requisicao, timeout=30) as resposta:
            corpo = resposta.read()
            return resposta.status, time.perf_counter() - inicio, corpo
    except urllib.error.HTTPError as erro:
        return erro.code, time.perf_counter() - inicio, b""
    except Exception as erro:
        return type(erro).__name__, time.perf_counter() - inicio, b""


def _links(dominio: str, sementes: list[str], robots, minimo: int) -> list[str]:
    encontrados: list[str] = []
    for semente in sementes:
        status, _, corpo = _baixar(semente)
        if status != 200:
            continue
        for href in re.findall(r'href="([^"#]+)"', corpo.decode("utf-8", "ignore")):
            alvo = urljoin(semente, href)
            if (
                normalizar_dominio(urlparse(alvo).netloc) == dominio
                and alvo not in encontrados
                and robots.can_fetch(USER_AGENT, alvo)
                and not IGNORAR_LINKS.search(alvo)
            ):
                encontrados.append(alvo)
        if len(encontrados) >= minimo:
            break
    return encontrados


def _nivel(urls: list[str], taxa: float) -> list[tuple[object, float]]:
    resultados: list[tuple[object, float]] = []
    trava = threading.Lock()

    def um(url: str) -> None:
        status, latencia, _ = _baixar(url)
        with trava:
            resultados.append((status, latencia))

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(2, int(taxa * 3))) as pool:
        for posicao, url in enumerate(urls):
            espera = inicio + posicao / taxa - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
            pool.submit(um, url)
    return resultados


def sondar(dominio: str, sementes: list[str], niveis: tuple[float, ...], por_nivel: int):
    dominio = normalizar_dominio(dominio)
    sementes = sementes or [f"https://{dominio}/", f"https://www.{dominio}/"]
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(f"https://{urlparse(sementes[0]).netloc}/robots.txt")
    with contextlib.suppress(Exception):
        robots.read()
    atraso = robots.crawl_delay(USER_AGENT)
    links = _links(dominio, sementes, robots, por_nivel * len(niveis))
    print(f"{dominio}: crawl-delay no robots.txt = {atraso}; páginas distintas = {len(links)}")
    if atraso:
        print(f"  O site pede {atraso}s entre requisições (máximo {1 / float(atraso):.2f}/s).")

    base = None
    seguro = None
    for posicao, taxa in enumerate(niveis):
        amostra = links[posicao * por_nivel : (posicao + 1) * por_nivel]
        if len(amostra) < por_nivel:
            print(f"  {taxa:g}/s: poucas páginas ({len(amostra)}); parei sem testar este nível.")
            break
        resultados = _nivel(amostra, taxa)
        codigos: dict[object, int] = {}
        for status, _ in resultados:
            codigos[status] = codigos.get(status, 0) + 1
        latencias = sorted(latencia for _, latencia in resultados)
        mediana = statistics.median(latencias)
        p90 = latencias[max(0, int(len(latencias) * 0.9) - 1)]
        base = base or mediana
        print(f"  {taxa:g}/s: códigos={codigos} mediana={mediana:.2f}s p90={p90:.2f}s")
        problemas = {c: n for c, n in codigos.items() if c != 200 and c not in (404, 410)}
        if problemas or (mediana > LATENCIA_LIMITE * base and mediana > 1.0):
            motivo = problemas or "latência muito maior que a base"
            print(f"  -> sinal de limite: {motivo}. Parei.")
            break
        seguro = float(taxa)
        time.sleep(10)
    return seguro


def _cmd_listar(_: argparse.Namespace) -> int:
    ritmo = carregar_ritmo()
    print(f"Arquivo: {caminho_do_arquivo()}\n")
    linhas = [ritmo.padrao, *sorted(ritmo.por_dominio.values(), key=lambda r: r.dominio)]
    print(f"{'site':32} {'simult.':>7} {'intervalo':>9} {'max/s':>6}  observacao")
    for r in linhas:
        print(
            f"{r.dominio:32} {r.simultaneas:>7} {r.intervalo:>9g} "
            f"{r.requisicoes_por_segundo:>6.1f}  {r.observacao}"
        )
    for aviso in ritmo.ignoradas:
        print(f"AVISO: {aviso}")
    return 0


def _cmd_definir(args: argparse.Namespace) -> int:
    atual = carregar_ritmo()
    base = atual.padrao if args.dominio == PADRAO else atual.do_host(args.dominio) or atual.padrao
    novo = definir_ritmo(
        args.dominio,
        args.simultaneas or base.simultaneas,
        args.intervalo or base.intervalo,
        args.observacao or "",
    )
    print(
        f"{novo.dominio}: {novo.simultaneas} simultâneas, intervalo {novo.intervalo:g}s "
        f"(até {novo.requisicoes_por_segundo:.1f} requisições por segundo)"
    )
    return 0


def _cmd_sondar(args: argparse.Namespace) -> int:
    niveis = tuple(float(n) for n in args.niveis.split(","))
    seguro = sondar(args.dominio, args.sementes or [], niveis, args.por_nivel)
    if seguro is None:
        print("\nNenhum nível foi confirmado como seguro; o ritmo não foi alterado.")
        return 1
    recomendado = max(1.0, seguro * MARGEM_DE_SEGURANCA)
    intervalo = round(1 / recomendado, 2)
    margem = int((1 - MARGEM_DE_SEGURANCA) * 100)
    print(
        f"\nMaior nível sem problema: {seguro:g}/s. Recomendado com margem de {margem}%: "
        f"{recomendado:.1f}/s (intervalo {intervalo}s)."
    )
    if args.aplicar:
        definir_ritmo(
            args.dominio,
            args.simultaneas,
            intervalo,
            f"sonda {time.strftime('%Y-%m-%d')}: sem problema ate {seguro:g}/s",
        )
        print("Gravado em config/ritmo_sites.csv.")
    else:
        print("Nada foi gravado. Use --aplicar para gravar no CSV.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("listar", help="mostra o ritmo de cada site").set_defaults(funcao=_cmd_listar)

    definir = sub.add_parser("definir", help="muda o ritmo de um site")
    definir.add_argument("dominio", help='dominio, ou "*" para o padrao')
    definir.add_argument("--simultaneas", type=int)
    definir.add_argument("--intervalo", type=float, help="segundos entre requisicoes")
    definir.add_argument("--observacao")
    definir.set_defaults(funcao=_cmd_definir)

    medir = sub.add_parser("sondar", help="mede quanto o site aguenta (faz requisicoes reais)")
    medir.add_argument("dominio")
    medir.add_argument("--sementes", nargs="*", help="URLs de onde tirar paginas para testar")
    medir.add_argument("--niveis", default=",".join(str(n) for n in NIVEIS_PADRAO))
    medir.add_argument("--por-nivel", type=int, default=25)
    medir.add_argument("--simultaneas", type=int, default=3)
    medir.add_argument("--aplicar", action="store_true", help="grava o recomendado no CSV")
    medir.set_defaults(funcao=_cmd_sondar)

    args = parser.parse_args()
    return args.funcao(args)


if __name__ == "__main__":
    sys.exit(main())
