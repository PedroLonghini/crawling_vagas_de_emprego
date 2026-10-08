"""Roda o lote e junta o máximo de informação sobre tempo, recursos e resultado.

    python scripts/medicao_completa.py rodar --nome teste_10mil -- \\
        --coletar --catalogo config/catalogo_10mil.csv --confirmar \\
        --processos-coleta 12 --alvos-por-coleta 2000 --limite-anuncios 200

Tudo depois do ``--`` vai para ``scripts/processar_lote.py``. A pasta
``outputs/medicao/<data>_<nome>/`` recebe:

    relatorio.md      resumo legível (mande este arquivo)
    resumo.json       os mesmos números, para máquina
    ambiente.json     máquina, versões, git, catálogo e ritmo por site
    amostras.csv      CPU, memória, disco, rede e Mongo a cada poucos segundos
    lote.log          a saída completa do lote
    por_site.csv      páginas, bytes e status HTTP por site

Para refazer o relatório de uma execução já terminada:

    python scripts/medicao_completa.py relatorio outputs/medicao/<pasta>

Precisa de ``psutil`` (``pip install psutil``).
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "outputs" / "medicao"
INTERVALO_AMOSTRA = 10
INTERVALO_MONGO = 60

COLUNAS_AMOSTRA = (
    "t_s",
    "cpu_total_pct",
    "mem_usada_gb",
    "mem_pct",
    "carga_1min",
    "processos_filhos",
    "cpu_filhos_pct",
    "mem_filhos_gb",
    "disco_leitura_mb_s",
    "disco_escrita_mb_s",
    "disco_leituras_s",
    "disco_escritas_s",
    "rede_recebido_mb_s",
    "rede_enviado_mb_s",
    "anuncios",
    "vagas_canonicas",
    "empresas",
)

# ---------------------------------------------------------------------------
# Leitura do log
# ---------------------------------------------------------------------------

_FASE = re.compile(r"^- (?P<nome>[^:]+): (?P<seg>[\d.]+)s(?: \((?P<pct>\d+)%\))?\s*$")
_CONTAGEM = re.compile(r"^(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^:\n]{3,60}): (?P<valor>\d+)\s*$")
_ESTATISTICA = re.compile(r"^\s*\{?'(?P<chave>[^']+)': (?P<valor>-?[\d.]+(?:e-?\d+)?),?\}?\s*$")
_CHAVES_SOMADAS = (
    "downloader/request_count",
    "downloader/response_count",
    "downloader/request_bytes",
    "downloader/response_bytes",
    "httpcompression/response_bytes",
    "item_scraped_count",
    "retry/count",
    "retry/max_reached",
    "robotstxt/request_count",
    "offsite/filtered",
    "observatorio/janela/requisicoes_descartadas",
    "observatorio/janela/listagens_sem_data_descartadas",
)
_PREFIXOS_SOMADOS = (
    "downloader/response_status_count/",
    "downloader/exception_type_count/",
    "observatorio/politica/bloqueios/",
    "retry/reason_count/",
)


def ler_fases(texto: str) -> dict[str, float]:
    """Lê a seção ``## TEMPO POR FASE`` (a última do log)."""

    fases: dict[str, float] = {}
    dentro = False
    for linha in texto.splitlines():
        if linha.startswith("## TEMPO POR FASE"):
            fases, dentro = {}, True
            continue
        if dentro:
            achado = _FASE.match(linha)
            if achado:
                fases[achado["nome"]] = float(achado["seg"])
            elif linha.strip() and not linha.startswith("-"):
                dentro = False
    return fases


def ler_contagens(texto: str) -> dict[str, int]:
    """Linhas ``Alvos concluídos: N`` etc. do resultado final."""

    inicio = texto.rfind("# RESULTADO FINAL DO LOTE")
    contagens: dict[str, int] = {}
    for linha in (texto[inicio:] if inicio >= 0 else "").splitlines():
        achado = _CONTAGEM.match(linha)
        if achado:
            contagens[achado["nome"]] = int(achado["valor"])
    return contagens


def somar_estatisticas_scrapy(texto: str) -> tuple[dict[str, float], int]:
    """Soma os blocos ``Dumping Scrapy stats`` de todos os processos do crawler."""

    totais: dict[str, float] = collections.defaultdict(float)
    blocos = 0
    dentro = False
    for linha in texto.splitlines():
        if "Dumping Scrapy stats" in linha:
            dentro, blocos = True, blocos + 1
            continue
        if not dentro:
            continue
        achado = _ESTATISTICA.match(linha)
        if achado:
            chave, valor = achado["chave"], float(achado["valor"])
            if chave in _CHAVES_SOMADAS or chave.startswith(_PREFIXOS_SOMADOS):
                totais[chave] += valor
            elif chave == "elapsed_time_seconds":
                totais["soma_tempo_dos_blocos_s"] += valor
                totais["maior_bloco_s"] = max(totais["maior_bloco_s"], valor)
        if linha.rstrip().endswith("}"):
            dentro = False
    return dict(totais), blocos


def contar_problemas(texto: str) -> dict[str, int]:
    padroes = {
        "Traceback": r"Traceback \(most recent call last\)",
        "ERRO": r"\bERRO\b",
        "Falha": r"\bFalha\b",
        "Timeout": r"TimeoutError|timed out",
        "Bloco com erro": r"O bloco terminou com erro",
        "Aviso": r"\bAVISO\b",
    }
    return {nome: len(re.findall(padrao, texto)) for nome, padrao in padroes.items()}


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------


def _comando(*args: str) -> str:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def descrever_ambiente(psutil, catalogo: Path | None, argumentos: list[str]) -> dict:
    memoria = psutil.virtual_memory()
    disco = shutil.disk_usage(RAIZ)
    ambiente = {
        "iniciado_em": datetime.now(UTC).isoformat(),
        "sistema": platform.platform(),
        "maquina": platform.machine(),
        "processador": platform.processor(),
        "nucleos_logicos": psutil.cpu_count(logical=True),
        "nucleos_fisicos": psutil.cpu_count(logical=False),
        "memoria_gb": round(memoria.total / 1e9, 1),
        "disco_livre_gb": round(disco.free / 1e9, 1),
        "python": sys.version.split()[0],
        "git_commit": _comando("git", "-C", str(RAIZ), "rev-parse", "--short", "HEAD"),
        "git_branch": _comando("git", "-C", str(RAIZ), "rev-parse", "--abbrev-ref", "HEAD"),
        "git_alteracoes_locais": bool(_comando("git", "-C", str(RAIZ), "status", "--porcelain")),
        "comando_do_lote": argumentos,
    }
    if sys.platform == "darwin":
        ambiente["chip"] = _comando("sysctl", "-n", "machdep.cpu.brand_string")
        ambiente["modo_economia_de_energia"] = _comando("pmset", "-g", "batt")[:200]
    with contextlib.suppress(Exception):
        import resource

        ambiente["limite_arquivos_abertos"] = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if catalogo is not None and catalogo.exists():
        ambiente["catalogo"] = _resumir_catalogo(catalogo)
    ritmo = RAIZ / "config" / "ritmo_sites.csv"
    if ritmo.exists():
        ambiente["ritmo_sites"] = ritmo.read_text(encoding="utf-8")
    return ambiente


def _resumir_catalogo(catalogo: Path) -> dict:
    from urllib.parse import urlsplit

    dominios: collections.Counter[str] = collections.Counter()
    linhas = 0
    with catalogo.open(encoding="utf-8-sig", newline="") as arquivo:
        for registro in csv.DictReader(arquivo):
            url = registro.get("url") or registro.get("url_inicial") or ""
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            if host:
                dominios[host] += 1
            linhas += 1
    return {
        "arquivo": str(catalogo),
        "fontes": linhas,
        "dominios": len(dominios),
        "dominios_com_5_ou_mais_fontes": sum(1 for n in dominios.values() if n >= 5),
        "maiores_dominios": dominios.most_common(15),
    }


# ---------------------------------------------------------------------------
# Amostragem durante a execução
# ---------------------------------------------------------------------------


class Amostrador(threading.Thread):
    def __init__(self, psutil, pid: int, destino: Path) -> None:
        super().__init__(daemon=True)
        self.psutil, self.pid, self.destino = psutil, pid, destino
        self.parar = threading.Event()
        self._inicio = time.monotonic()
        self._mongo: tuple[int, int, int] = (0, 0, 0)
        self._mongo_em = -INTERVALO_MONGO

    def _contagens_mongo(self) -> tuple[int, int, int]:
        agora = time.monotonic()
        if agora - self._mongo_em < INTERVALO_MONGO:
            return self._mongo
        self._mongo_em = agora
        try:
            from observatorio_vagas.config import get_settings
            from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB

            if not hasattr(self, "_banco"):
                self._banco = ConexaoMongoDB(get_settings()).banco
            self._mongo = tuple(
                self._banco[nome].estimated_document_count()
                for nome in ("anuncios", "vagas_canonicas", "empresas")
            )
        except Exception:
            pass
        return self._mongo

    def run(self) -> None:
        ps = self.psutil
        raiz = ps.Process(self.pid)
        ps.cpu_percent(None)
        disco0, rede0, t0 = ps.disk_io_counters(), ps.net_io_counters(), time.monotonic()
        with self.destino.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.writer(arquivo)
            escritor.writerow(COLUNAS_AMOSTRA)
            while not self.parar.wait(INTERVALO_AMOSTRA):
                agora = time.monotonic()
                dt = max(agora - t0, 1e-6)
                disco1, rede1 = ps.disk_io_counters(), ps.net_io_counters()
                filhos = []
                with contextlib.suppress(ps.Error):
                    filhos = [raiz, *raiz.children(recursive=True)]
                cpu_f = mem_f = 0.0
                vivos = 0
                for processo in filhos:
                    with contextlib.suppress(ps.Error):
                        cpu_f += processo.cpu_percent(None)
                        mem_f += processo.memory_info().rss
                        vivos += 1
                memoria = ps.virtual_memory()
                carga = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
                escritor.writerow(
                    [
                        round(agora - self._inicio),
                        ps.cpu_percent(None),
                        round((memoria.total - memoria.available) / 1e9, 2),
                        memoria.percent,
                        round(carga, 2),
                        vivos,
                        round(cpu_f, 1),
                        round(mem_f / 1e9, 2),
                        round((disco1.read_bytes - disco0.read_bytes) / dt / 1e6, 2),
                        round((disco1.write_bytes - disco0.write_bytes) / dt / 1e6, 2),
                        round((disco1.read_count - disco0.read_count) / dt),
                        round((disco1.write_count - disco0.write_count) / dt),
                        round((rede1.bytes_recv - rede0.bytes_recv) / dt / 1e6, 3),
                        round((rede1.bytes_sent - rede0.bytes_sent) / dt / 1e6, 3),
                        *self._contagens_mongo(),
                    ]
                )
                arquivo.flush()
                disco0, rede0, t0 = disco1, rede1, agora


# ---------------------------------------------------------------------------
# Resultado da coleta (cadernos e cobertura)
# ---------------------------------------------------------------------------


def resumir_coleta(desde: datetime, pasta_saida: Path) -> dict:
    """Páginas, bytes e status HTTP por site, a partir dos cadernos do lote."""

    from urllib.parse import urlsplit

    cadernos = RAIZ / "data" / "raw" / "cadernos"
    por_site: dict[str, dict] = collections.defaultdict(
        lambda: {"paginas": 0, "detalhes": 0, "bytes": 0, "erros_http": 0, "fontes": set()}
    )
    status: collections.Counter[int] = collections.Counter()
    tipos: collections.Counter[str] = collections.Counter()
    if cadernos.exists():
        for pasta in cadernos.iterdir():
            if not pasta.is_dir() or datetime.fromtimestamp(pasta.stat().st_mtime, UTC) < desde:
                continue
            for caderno in pasta.glob("*.jsonl"):
                for linha in caderno.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        meta = json.loads(linha)["metadados"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    host = (urlsplit(meta.get("url_final", "")).hostname or "?").removeprefix(
                        "www."
                    )
                    site = por_site[host]
                    site["paginas"] += 1
                    site["bytes"] += int(meta.get("tamanho_bytes") or 0)
                    site["fontes"].add(meta.get("alvo_id"))
                    codigo = int(meta.get("status_http") or 0)
                    status[codigo] += 1
                    tipos[str(meta.get("tipo_pagina"))] += 1
                    if meta.get("tipo_pagina") == "detalhe_vaga":
                        site["detalhes"] += 1
                    if codigo >= 400:
                        site["erros_http"] += 1
    linhas = sorted(por_site.items(), key=lambda item: -item[1]["paginas"])
    with (pasta_saida / "por_site.csv").open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(["site", "fontes", "paginas", "paginas_de_detalhe", "mb", "erros_http"])
        for host, dados in linhas:
            escritor.writerow(
                [
                    host,
                    len(dados["fontes"]),
                    dados["paginas"],
                    dados["detalhes"],
                    round(dados["bytes"] / 1e6, 1),
                    dados["erros_http"],
                ]
            )
    return {
        "paginas": sum(s["paginas"] for s in por_site.values()),
        "paginas_de_detalhe": sum(s["detalhes"] for s in por_site.values()),
        "mb_coletados": round(sum(s["bytes"] for s in por_site.values()) / 1e6, 1),
        "sites": len(por_site),
        "fontes_com_pagina": len({f for s in por_site.values() for f in s["fontes"]}),
        "status_http": dict(sorted(status.items())),
        "tipos_de_pagina": dict(tipos),
        "maiores_sites": [(h, d["paginas"]) for h, d in linhas[:15]],
    }


def resumir_amostras(caminho: Path) -> dict:
    if not caminho.exists():
        return {}
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    if not linhas:
        return {}

    def serie(nome: str) -> list[float]:
        return [float(linha[nome]) for linha in linhas if linha.get(nome) not in (None, "")]

    def media(nome: str) -> float:
        valores = serie(nome)
        return round(sum(valores) / len(valores), 2) if valores else 0.0

    anuncios = serie("anuncios")
    return {
        "amostras": len(linhas),
        "cpu_total_media_pct": media("cpu_total_pct"),
        "cpu_total_maxima_pct": max(serie("cpu_total_pct")),
        "memoria_usada_maxima_gb": max(serie("mem_usada_gb")),
        "processos_filhos_maximo": int(max(serie("processos_filhos"))),
        "memoria_dos_filhos_maxima_gb": max(serie("mem_filhos_gb")),
        "disco_leitura_media_mb_s": media("disco_leitura_mb_s"),
        "disco_leitura_maxima_mb_s": max(serie("disco_leitura_mb_s")),
        "disco_escrita_media_mb_s": media("disco_escrita_mb_s"),
        "disco_leituras_por_s_maximo": int(max(serie("disco_leituras_s"))),
        "rede_recebida_media_mb_s": media("rede_recebido_mb_s"),
        "rede_recebida_maxima_mb_s": max(serie("rede_recebido_mb_s")),
        "carga_maxima_1min": max(serie("carga_1min")),
        "mongo_anuncios_primeiro": int(anuncios[0]) if anuncios else 0,
        "mongo_anuncios_ultimo": int(anuncios[-1]) if anuncios else 0,
    }


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------


def _hms(segundos: float) -> str:
    segundos = int(segundos)
    return f"{segundos // 3600}h {segundos % 3600 // 60:02d}min {segundos % 60:02d}s"


def montar_relatorio(resumo: dict) -> str:
    ambiente, fases = resumo["ambiente"], resumo["fases"]
    coleta, rec, scrapy = resumo["coleta"], resumo["recursos"], resumo["scrapy"]
    total = resumo["duracao_s"]
    linhas = [
        f"# Medição: {resumo['nome']}",
        "",
        f"- Duração total: **{_hms(total)}** ({total:.0f}s), "
        f"código de saída {resumo['codigo_saida']}",
        f"- Máquina: {ambiente.get('chip') or ambiente.get('processador')}, "
        f"{ambiente['nucleos_fisicos']} núcleos físicos / {ambiente['nucleos_logicos']} lógicos, "
        f"{ambiente['memoria_gb']} GB, {ambiente['sistema']}",
        f"- Versão: {ambiente['git_branch']} @ {ambiente['git_commit']}"
        + (" (com alterações locais)" if ambiente["git_alteracoes_locais"] else ""),
        f"- Comando: `{' '.join(ambiente['comando_do_lote'])}`",
        "",
        "## Tempo por fase",
    ]
    for nome, seg in fases.items():
        if nome != "Total":
            linhas.append(f"- {nome}: {_hms(seg)} ({seg / total:.0%} do total medido)")
    if "catalogo" in ambiente:
        c = ambiente["catalogo"]
        linhas += [
            "",
            "## Catálogo",
            f"- {c['fontes']} fontes em {c['dominios']} domínios "
            f"({c['dominios_com_5_ou_mais_fontes']} domínios com 5+ fontes)",
        ]
    linhas += ["", "## Resultado do lote"]
    linhas += [f"- {nome}: {valor}" for nome, valor in resumo["contagens"].items()]
    linhas += [
        "",
        "## Coleta",
        f"- Páginas: {coleta.get('paginas', 0)} "
        f"({coleta.get('paginas_de_detalhe', 0)} de detalhe), "
        f"{coleta.get('mb_coletados', 0)} MB, {coleta.get('sites', 0)} sites",
        f"- Fontes que renderam páginas: {coleta.get('fontes_com_pagina', 0)}",
        f"- Status HTTP: {coleta.get('status_http', {})}",
    ]
    if scrapy:
        linhas += [
            f"- Requisições feitas: {int(scrapy.get('downloader/request_count', 0))}, "
            f"respostas: {int(scrapy.get('downloader/response_count', 0))}, "
            f"retentativas: {int(scrapy.get('retry/count', 0))}",
            f"- Blocos de coleta: {resumo['blocos_scrapy']}, maior bloco: "
            f"{_hms(scrapy.get('maior_bloco_s', 0))}",
        ]
        bloqueios = {
            k.rsplit("/", 1)[-1]: int(v)
            for k, v in scrapy.items()
            if k.startswith("observatorio/politica/bloqueios/")
        }
        if bloqueios:
            linhas.append(f"- Bloqueios de política: {bloqueios}")
        if scrapy.get("observatorio/janela/requisicoes_descartadas"):
            linhas.append(
                f"- Requisições poupadas pela janela de 24h/vagas já gravadas: "
                f"{int(scrapy['observatorio/janela/requisicoes_descartadas'])}"
            )
    linhas += ["", "### Maiores sites (páginas)"]
    linhas += [f"- {host}: {n}" for host, n in coleta.get("maiores_sites", [])]
    if rec:
        linhas += [
            "",
            "## Recursos da máquina (amostras a cada "
            f"{INTERVALO_AMOSTRA}s, {rec['amostras']} no total)",
            f"- CPU total: média {rec['cpu_total_media_pct']}%, "
            f"máxima {rec['cpu_total_maxima_pct']}%",
            f"- Memória usada: máxima {rec['memoria_usada_maxima_gb']} GB "
            f"(só os processos do lote: {rec['memoria_dos_filhos_maxima_gb']} GB)",
            f"- Processos do lote vivos ao mesmo tempo: até {rec['processos_filhos_maximo']}",
            f"- Disco: leitura média {rec['disco_leitura_media_mb_s']} MB/s "
            f"(pico {rec['disco_leitura_maxima_mb_s']}), "
            f"escrita média {rec['disco_escrita_media_mb_s']} MB/s, "
            f"pico de {rec['disco_leituras_por_s_maximo']} leituras/s",
            f"- Rede: recebido médio {rec['rede_recebida_media_mb_s']} MB/s "
            f"(pico {rec['rede_recebida_maxima_mb_s']})",
            f"- Carga do sistema (1 min): máxima {rec['carga_maxima_1min']}",
            f"- Mongo: anúncios {rec['mongo_anuncios_primeiro']} -> {rec['mongo_anuncios_ultimo']}",
            "",
            "Como ler: CPU total perto de 100% = limitado por processador; CPU baixa com "
            "muita espera = limitado por disco, rede ou pelo ritmo dos sites.",
        ]
    linhas += ["", "## Problemas no log"]
    linhas += [f"- {nome}: {n}" for nome, n in resumo["problemas"].items()]
    linhas += ["", "Arquivos desta medição: amostras.csv, por_site.csv, lote.log, ambiente.json."]
    return "\n".join(linhas) + "\n"


def gerar_relatorio(pasta: Path, codigo_saida: int, duracao: float, inicio: datetime) -> dict:
    texto = (pasta / "lote.log").read_text(encoding="utf-8", errors="replace")
    ambiente = json.loads((pasta / "ambiente.json").read_text(encoding="utf-8"))
    scrapy, blocos = somar_estatisticas_scrapy(texto)
    resumo = {
        "nome": pasta.name,
        "codigo_saida": codigo_saida,
        "duracao_s": duracao,
        "ambiente": ambiente,
        "fases": ler_fases(texto),
        "contagens": ler_contagens(texto),
        "scrapy": scrapy,
        "blocos_scrapy": blocos,
        "coleta": resumir_coleta(inicio, pasta),
        "recursos": resumir_amostras(pasta / "amostras.csv"),
        "problemas": contar_problemas(texto),
    }
    (pasta / "resumo.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    (pasta / "relatorio.md").write_text(montar_relatorio(resumo), encoding="utf-8")
    return resumo


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def _catalogo_dos_argumentos(argumentos: list[str]) -> Path | None:
    for posicao, argumento in enumerate(argumentos):
        if argumento == "--catalogo" and posicao + 1 < len(argumentos):
            return Path(argumentos[posicao + 1])
    return None


def rodar(args: argparse.Namespace) -> int:
    try:
        import psutil
    except ImportError:
        print("Falta o psutil. Instale com:  pip install psutil")
        return 2

    argumentos = [a for a in args.lote if a != "--"]
    if not argumentos:
        print("Informe os argumentos do lote depois de --")
        return 2

    inicio = datetime.now(UTC)
    pasta = SAIDA / f"{inicio.strftime('%Y%m%dT%H%M%S')}_{args.nome}"
    pasta.mkdir(parents=True)
    ambiente = descrever_ambiente(psutil, _catalogo_dos_argumentos(argumentos), argumentos)
    (pasta / "ambiente.json").write_text(
        json.dumps(ambiente, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )

    comando = [sys.executable, "-u", str(RAIZ / "scripts" / "processar_lote.py"), *argumentos]
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        comando = ["caffeinate", "-i", *comando]  # impede o Mac de dormir durante o lote
    print(f"Medição em {pasta}\nComando: {' '.join(comando)}\n", flush=True)

    ambiente_filho = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    inicio_relogio = time.monotonic()
    with (pasta / "lote.log").open("w", encoding="utf-8", errors="replace") as log:
        processo = subprocess.Popen(
            comando,
            cwd=RAIZ,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=ambiente_filho,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        amostrador = Amostrador(psutil, processo.pid, pasta / "amostras.csv")
        amostrador.start()
        try:
            assert processo.stdout is not None
            for linha in processo.stdout:
                log.write(linha)
                if args.eco:
                    print(linha, end="")
            codigo = processo.wait()
        except KeyboardInterrupt:
            processo.terminate()
            codigo = processo.wait()
            print("\nInterrompido; gerando o relatório do que existe.")
        finally:
            amostrador.parar.set()
            amostrador.join(timeout=5)
    duracao = time.monotonic() - inicio_relogio
    gerar_relatorio(pasta, codigo, duracao, inicio)
    print(f"\nPronto em {_hms(duracao)}. Relatório: {pasta / 'relatorio.md'}")
    return codigo


def refazer(args: argparse.Namespace) -> int:
    pasta = Path(args.pasta)
    resumo = (
        json.loads((pasta / "resumo.json").read_text(encoding="utf-8"))
        if (pasta / "resumo.json").exists()
        else {"codigo_saida": 0, "duracao_s": 1.0}
    )
    inicio = datetime.fromisoformat(
        json.loads((pasta / "ambiente.json").read_text(encoding="utf-8"))["iniciado_em"]
    )
    gerar_relatorio(pasta, resumo["codigo_saida"], resumo["duracao_s"], inicio)
    print(f"Relatório refeito em {pasta / 'relatorio.md'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    r = sub.add_parser("rodar", help="roda o lote medindo tudo")
    r.add_argument("--nome", default="medicao", help="nome curto da medição")
    r.add_argument("--eco", action="store_true", help="também mostra o log no terminal")
    r.add_argument("lote", nargs=argparse.REMAINDER, help="-- e os argumentos do processar_lote.py")
    r.set_defaults(funcao=rodar)
    f = sub.add_parser("relatorio", help="refaz o relatório de uma pasta já medida")
    f.add_argument("pasta")
    f.set_defaults(funcao=refazer)
    args = parser.parse_args()
    return args.funcao(args)


if __name__ == "__main__":
    sys.exit(main())
