"""Estado local para evitar reler detalhes quando uma fonte não mudou."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from scrapy.http import Response

INTERVALO_SALVAMENTO_S = 30.0
INTERVALO_FONTE_SEM_CANDIDATOS_DIAS = 7
INTERVALO_FONTE_IMPRODUTIVA_DIAS = 30


@dataclass(frozen=True, slots=True)
class RegistroFonteIncremental:
    """Informações da última listagem recebida de uma fonte."""

    hash_conteudo: str
    etag: str | None = None
    ultima_modificacao: str | None = None


class EstadoIncrementalLocal:
    """Persiste validadores HTTP e hash por alvo, sem guardar credenciais."""

    def __init__(self, caminho: Path) -> None:
        self._caminho = caminho
        (
            self._registros,
            self._detalhes,
            self._desempenhos,
            self._agendamentos,
            self._vistos,
        ) = self._carregar()

    def cabecalhos_condicionais(self, *, alvo_id: str, url: str) -> dict[str, str]:
        """Produz cabeçalhos HTTP para uma revalidação barata, quando possível."""

        registro = self._registros.get(self._chave(alvo_id, url))
        if registro is None:
            return {}

        cabecalhos: dict[str, str] = {}
        if registro.etag:
            cabecalhos["If-None-Match"] = registro.etag
        if registro.ultima_modificacao:
            cabecalhos["If-Modified-Since"] = registro.ultima_modificacao
        return cabecalhos

    def resposta_inalterada(self, *, alvo_id: str, url: str, resposta: Response) -> bool:
        """Registra uma listagem e informa se seu conteúdo é igual ao da coleta anterior."""

        chave = self._chave(alvo_id, url)
        anterior = self._registros.get(chave)

        # 304 é a confirmação explícita do servidor de que a representação
        # condicional continua a mesma. Não substituímos os dados anteriores,
        # pois a resposta normalmente não possui corpo nem todos os cabeçalhos.
        if resposta.status == 304:
            return anterior is not None

        if not 200 <= resposta.status < 300:
            return False

        hash_conteudo = sha256(resposta.body).hexdigest()
        etag = _ler_cabecalho(resposta, "ETag")
        ultima_modificacao = _ler_cabecalho(resposta, "Last-Modified")
        self._registros[chave] = RegistroFonteIncremental(
            hash_conteudo=hash_conteudo,
            etag=etag or (anterior.etag if anterior else None),
            ultima_modificacao=ultima_modificacao
            or (anterior.ultima_modificacao if anterior else None),
        )
        self._salvar_se_passou_tempo()
        return anterior is not None and anterior.hash_conteudo == hash_conteudo

    def detalhe_conhecido(self, *, alvo_id: str, url: str) -> bool:
        """Informa se o detalhe já foi baixado com sucesso em coleta anterior."""

        return url in self._detalhes.get(alvo_id, set())

    def registrar_detalhe_sucesso(self, *, alvo_id: str, url: str) -> bool:
        """Memoriza somente detalhes recebidos com sucesso."""

        detalhes = self._detalhes.setdefault(alvo_id, set())
        if url in detalhes:
            return False
        detalhes.add(url)
        self._vistos.setdefault(alvo_id, {})[url] = date.today().isoformat()
        # Impede que uma fonte muito antiga faça o estado crescer sem limite.
        if len(detalhes) > 50_000:
            detalhes.difference_update(sorted(detalhes)[: len(detalhes) - 50_000])
        desempenho = self._desempenhos.setdefault(alvo_id, _novo_desempenho())
        desempenho["detalhes_novos"] += 1
        return True

    def registrar_urls_observadas(self, *, alvo_id: str, urls: tuple[str, ...]) -> None:
        """Registra que links continuam aparecendo na listagem pública."""

        vistos = self._vistos.setdefault(alvo_id, {})
        hoje = date.today().isoformat()
        for url in urls:
            vistos[url] = hoje

    def confirmar_listagem_inalterada(self, *, alvo_id: str) -> None:
        """Uma resposta 304 confirma que os links conhecidos continuam presentes."""

        self.registrar_urls_observadas(
            alvo_id=alvo_id,
            urls=tuple(self._detalhes.get(alvo_id, set())),
        )

    def possiveis_encerradas(self, *, alvo_id: str, hoje: date | None = None) -> tuple[str, ...]:
        """Lista candidatas a revisão; nunca fecha vaga automaticamente."""

        referencia = hoje or date.today()
        vistos = self._vistos.get(alvo_id, {})
        resultado = []
        for url in self._detalhes.get(alvo_id, set()):
            data_vista = vistos.get(url)
            try:
                ultima_vista = date.fromisoformat(data_vista) if data_vista else None
            except ValueError:
                ultima_vista = None
            if ultima_vista is None or (referencia - ultima_vista).days >= 14:
                resultado.append(url)
        return tuple(sorted(resultado))

    def deve_coletar_hoje(self, alvo_id: str, hoje: date | None = None) -> bool:
        """Mantém fontes novas/boas diárias e espaça apenas fontes sem retorno."""

        proxima = self._agendamentos.get(alvo_id, {}).get("proxima_coleta")
        if not isinstance(proxima, str):
            return True
        try:
            return date.fromisoformat(proxima) <= (hoje or date.today())
        except ValueError:
            return True

    def registrar_execucao(
        self,
        *,
        alvo_id: str,
        detalhes_novos: int,
        hoje: date | None = None,
        sem_candidatos: bool = False,
    ) -> None:
        """Define a próxima coleta conforme a recorrência de vagas novas."""

        dia = hoje or date.today()
        anterior = self._agendamentos.get(alvo_id, {})
        sem_vagas_na_primeira_passada = sem_candidatos and not anterior
        desempenho = self._desempenhos.get(alvo_id, _novo_desempenho())
        if int(desempenho["falhas_consecutivas"]) >= 3:
            # Circuit breaker: uma fonte que falhou repetidamente não ocupa a
            # fila inteira a cada execução. Ela volta no dia seguinte para um
            # novo teste, sem ser removida nem perder seu histórico.
            self._agendamentos[alvo_id] = {
                "ultima_coleta": dia.isoformat(),
                "proxima_coleta": (dia + timedelta(days=1)).isoformat(),
                "sem_novidades": int(anterior.get("sem_novidades", 0)),
            }
            return
        sem_novidades = 0 if detalhes_novos else int(anterior.get("sem_novidades", 0)) + 1
        # Fonte que já rendeu vaga: só reduz a frequência depois de três coletas sem novidade.
        intervalo = 1 if sem_novidades < 3 else (3 if sem_novidades < 7 else 7)
        if int(desempenho["detalhes_novos"]) == 0 and not detalhes_novos:
            # Fonte que NUNCA rendeu nada (só 15% das fontes do teste de 10 mil rendiam):
            # 7 dias nas primeiras tentativas e 30 dias depois de três sem resultado.
            intervalo = (
                INTERVALO_FONTE_SEM_CANDIDATOS_DIAS
                if sem_novidades < 3
                else INTERVALO_FONTE_IMPRODUTIVA_DIAS
            )
        if sem_vagas_na_primeira_passada:
            intervalo = max(intervalo, INTERVALO_FONTE_SEM_CANDIDATOS_DIAS)
        self._agendamentos[alvo_id] = {
            "ultima_coleta": dia.isoformat(),
            "proxima_coleta": (dia + timedelta(days=intervalo)).isoformat(),
            "sem_novidades": sem_novidades,
        }

    def ranking(self) -> list[dict[str, Any]]:
        """Entrega um ranking explicável para relatório e operação diária."""

        linhas = []
        for alvo_id, desempenho in self._desempenhos.items():
            sucessos = int(desempenho["sucessos"])
            falhas = int(desempenho["falhas"])
            total = sucessos + falhas
            agenda = self._agendamentos.get(alvo_id, {})
            linhas.append(
                {
                    "alvo_id": alvo_id,
                    "vagas_novas_historicas": int(desempenho["detalhes_novos"]),
                    "sucessos": sucessos,
                    "falhas": falhas,
                    "taxa_sucesso": round(sucessos / total, 3) if total else None,
                    "latencia_media_segundos": desempenho["latencia_media"],
                    "proxima_coleta": agenda.get("proxima_coleta"),
                    "coletas_sem_novidade": agenda.get("sem_novidades", 0),
                    "fila": self.fila(alvo_id),
                    "circuit_breaker_aberto": int(desempenho["falhas_consecutivas"]) >= 3,
                }
            )
        return sorted(
            linhas,
            key=lambda linha: (
                linha["vagas_novas_historicas"],
                linha["taxa_sucesso"] if linha["taxa_sucesso"] is not None else -1,
            ),
            reverse=True,
        )

    def registrar_resposta(self, *, alvo_id: str, resposta: Response) -> None:
        """Acumula desempenho sem persistir a cada página."""

        desempenho = self._desempenhos.setdefault(alvo_id, _novo_desempenho())
        if 200 <= resposta.status < 400:
            desempenho["sucessos"] += 1
            desempenho["falhas_consecutivas"] = 0
        else:
            desempenho["falhas"] += 1
            desempenho["falhas_consecutivas"] += 1
        latencia = resposta.meta.get("download_latency")
        if isinstance(latencia, (int, float)) and latencia >= 0:
            anterior = desempenho["latencia_media"]
            desempenho["latencia_media"] = (
                float(latencia)
                if anterior == 0
                else round((anterior * 0.8) + (float(latencia) * 0.2), 3)
            )

    def registrar_falha(self, *, alvo_id: str) -> None:
        desempenho = self._desempenhos.setdefault(alvo_id, _novo_desempenho())
        desempenho["falhas"] += 1
        desempenho["falhas_consecutivas"] += 1

    def fila(self, alvo_id: str) -> str:
        """Classifica a fonte para ordenar a fila sem mudar a política dela."""

        desempenho = self._desempenhos.get(alvo_id, _novo_desempenho())
        if int(desempenho["falhas_consecutivas"]) >= 3 or desempenho["latencia_media"] >= 8:
            return "lenta"
        if desempenho["detalhes_novos"] > 0 and desempenho["latencia_media"] <= 2:
            return "rapida"
        return "normal"

    def prioridade(self, alvo_id: str) -> tuple[float, float]:
        """Fontes que rendem e respondem bem iniciam a coleta diária primeiro."""

        desempenho = self._desempenhos.get(alvo_id, _novo_desempenho())
        total = desempenho["sucessos"] + desempenho["falhas"]
        confiabilidade = desempenho["sucessos"] / total if total else 0.5
        rendimento = desempenho["detalhes_novos"] / max(desempenho["sucessos"], 1)
        return (rendimento, confiabilidade)

    def configuracao_download(self, alvo_id: str) -> tuple[float, int]:
        """Define timeout e retries por histórico, com um padrão econômico."""

        desempenho = self._desempenhos.get(alvo_id, _novo_desempenho())
        total = desempenho["sucessos"] + desempenho["falhas"]
        taxa_falha = desempenho["falhas"] / total if total else 0.0
        if total >= 3 and taxa_falha >= 0.5:
            return (8.0, 0)
        if total >= 3 and desempenho["latencia_media"] >= 8:
            return (30.0, 2)
        return (15.0, 1)

    @staticmethod
    def _chave(alvo_id: str, url: str) -> str:
        return f"{alvo_id}|{url}"

    def _carregar(
        self,
    ) -> tuple[
        dict[str, RegistroFonteIncremental],
        dict[str, set[str]],
        dict[str, dict[str, float | int]],
        dict[str, dict[str, str | int]],
        dict[str, dict[str, str]],
    ]:
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, {}, {}, {}, {}
        except (OSError, ValueError, TypeError):
            # Um cache corrompido nunca deve interromper a coleta diária.
            return {}, {}, {}, {}, {}

        if not isinstance(dados, dict):
            return {}, {}, {}, {}, {}

        # Aceita o formato inicial do cache para que atualizações não obriguem
        # a apagar o estado já criado pelo usuário.
        listagens_brutas = dados.get("listagens", dados)
        detalhes_brutos = dados.get("detalhes", {})
        desempenhos_brutos = dados.get("desempenhos", {})
        agendamentos_brutos = dados.get("agendamentos", {})
        vistos_brutos = dados.get("vistos", {})
        if not isinstance(listagens_brutas, dict):
            return {}, {}, {}, {}, {}

        registros: dict[str, RegistroFonteIncremental] = {}
        for chave, valor in listagens_brutas.items():
            if not isinstance(chave, str) or not isinstance(valor, dict):
                continue
            hash_conteudo = valor.get("hash_conteudo")
            if not isinstance(hash_conteudo, str) or len(hash_conteudo) != 64:
                continue
            registros[chave] = RegistroFonteIncremental(
                hash_conteudo=hash_conteudo,
                etag=valor.get("etag") if isinstance(valor.get("etag"), str) else None,
                ultima_modificacao=(
                    valor.get("ultima_modificacao")
                    if isinstance(valor.get("ultima_modificacao"), str)
                    else None
                ),
            )
        detalhes = (
            {
                alvo_id: {
                    url
                    for url in urls
                    if isinstance(url, str) and url.startswith(("http://", "https://"))
                }
                for alvo_id, urls in detalhes_brutos.items()
                if isinstance(alvo_id, str) and isinstance(urls, list)
            }
            if isinstance(detalhes_brutos, dict)
            else {}
        )
        desempenhos: dict[str, dict[str, float | int]] = {}
        if isinstance(desempenhos_brutos, dict):
            for alvo_id, valor in desempenhos_brutos.items():
                if not isinstance(alvo_id, str) or not isinstance(valor, dict):
                    continue
                desempenho = _novo_desempenho()
                for chave in desempenho:
                    numero = valor.get(chave)
                    if isinstance(numero, (int, float)) and numero >= 0:
                        desempenho[chave] = numero
                desempenhos[alvo_id] = desempenho
        agendamentos: dict[str, dict[str, str | int]] = {}
        if isinstance(agendamentos_brutos, dict):
            for alvo_id, valor in agendamentos_brutos.items():
                if not isinstance(alvo_id, str) or not isinstance(valor, dict):
                    continue
                proxima = valor.get("proxima_coleta")
                ultima = valor.get("ultima_coleta")
                sem_novidades = valor.get("sem_novidades", 0)
                if (
                    isinstance(proxima, str)
                    and isinstance(ultima, str)
                    and isinstance(sem_novidades, int)
                ):
                    agendamentos[alvo_id] = {
                        "ultima_coleta": ultima,
                        "proxima_coleta": proxima,
                        "sem_novidades": max(sem_novidades, 0),
                    }
        vistos = (
            {
                alvo_id: {
                    url: data
                    for url, data in urls.items()
                    if isinstance(url, str) and isinstance(data, str)
                }
                for alvo_id, urls in vistos_brutos.items()
                if isinstance(alvo_id, str) and isinstance(urls, dict)
            }
            if isinstance(vistos_brutos, dict)
            else {}
        )
        return registros, detalhes, desempenhos, agendamentos, vistos

    def _salvar_se_passou_tempo(self) -> None:
        """Regravar o JSON inteiro a cada listagem era quadrático (153 mil vezes no teste)."""

        agora = time.monotonic()
        if agora - getattr(self, "_ultimo_salvamento", float("-inf")) >= INTERVALO_SALVAMENTO_S:
            self.salvar()

    def salvar(self) -> None:
        """Persiste de uma vez o estado acumulado durante a coleta."""

        self._ultimo_salvamento = time.monotonic()

        dados: dict[str, Any] = {
            "versao": 3,
            "listagens": {
                chave: {
                    "hash_conteudo": registro.hash_conteudo,
                    "etag": registro.etag,
                    "ultima_modificacao": registro.ultima_modificacao,
                }
                for chave, registro in sorted(self._registros.items())
            },
            "detalhes": {alvo_id: sorted(urls) for alvo_id, urls in sorted(self._detalhes.items())},
            "desempenhos": self._desempenhos,
            "agendamentos": self._agendamentos,
            "vistos": self._vistos,
        }
        try:
            self._caminho.parent.mkdir(parents=True, exist_ok=True)
            temporario = self._caminho.with_suffix(f"{self._caminho.suffix}.tmp")
            temporario.write_text(
                json.dumps(dados, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temporario.replace(self._caminho)
        except OSError:
            # Cache é uma otimização; falhar ao gravá-lo não pode perder vagas.
            return


def _novo_desempenho() -> dict[str, float | int]:
    return {
        "sucessos": 0,
        "falhas": 0,
        "falhas_consecutivas": 0,
        "latencia_media": 0.0,
        "detalhes_novos": 0,
    }


def _ler_cabecalho(resposta: Response, nome: str) -> str | None:
    valor = resposta.headers.get(nome.encode("ascii"))
    if not valor:
        return None
    texto = valor.decode("latin-1").strip()
    return texto or None
