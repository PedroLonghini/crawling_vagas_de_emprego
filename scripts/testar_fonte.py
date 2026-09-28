"""Testa uma única fonte, com arquivos isolados e resumo de coleta e extração."""

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from observatorio_vagas.crawling.catalog import carregar_alvos_csv_tolerante
from observatorio_vagas.extraction.data_publicacao import ontem_brasilia
from observatorio_vagas.extraction.processador import processar_respostas_brutas

if __package__:
    from .processar_lote import RAIZ_PROJETO, _escrever_catalogo_temporario
else:
    from processar_lote import RAIZ_PROJETO, _escrever_catalogo_temporario


def selecionar_alvo(alvos, identificador):
    encontrados = [a for a in alvos if a.alvo_id == identificador]
    if len(encontrados) != 1:
        raise ValueError("ID ausente ou duplicado no catálogo; use --listar para conferir")
    if not encontrados[0].habilitado_para_coleta:
        raise ValueError("a fonte está desativada ou sua política não permite coleta")
    return encontrados[0]


def calcular_metricas_extracao(resultado):
    """Mede campos observáveis sem confundir extração com elegibilidade final."""

    anuncios = tuple(resultado.anuncios)
    campos = {
        "titulo": sum(bool(anuncio.titulo_original.strip()) for anuncio in anuncios),
        "descricao": sum(bool(anuncio.descricao_original.strip()) for anuncio in anuncios),
        "empresa": sum(bool(anuncio.empresa_original) for anuncio in anuncios),
        "localidade": sum(bool(anuncio.localidade_original) for anuncio in anuncios),
        "endereco": sum(bool(anuncio.endereco_original) for anuncio in anuncios),
        "candidatura": sum(anuncio.url_candidatura is not None for anuncio in anuncios),
        "expiracao": sum(anuncio.expira_em is not None for anuncio in anuncios),
    }
    completos_minimos = sum(
        bool(anuncio.titulo_original.strip())
        and bool(anuncio.descricao_original.strip())
        and bool(anuncio.empresa_original)
        and bool(anuncio.localidade_original or anuncio.endereco_original)
        for anuncio in anuncios
    )
    return {
        "documentos_encontrados": getattr(resultado, "documentos_encontrados", len(anuncios)),
        "anuncios_unicos": len(anuncios),
        "anuncios_duplicados": getattr(resultado, "anuncios_duplicados", 0),
        "paginas_ignoradas": getattr(resultado, "paginas_ignoradas", 0),
        "paginas_incompativeis": getattr(resultado, "paginas_incompativeis", 0),
        "blocos_invalidos": getattr(resultado, "blocos_invalidos", 0),
        "campos_observaveis": campos,
        "anuncios_com_campos_minimos_observaveis": completos_minimos,
    }


def _argumentos_de_uma_fonte(args, alvo_id):
    """Recria os argumentos do teste para uma fonte dentro de um lote."""

    argumentos = [
        "--catalogo",
        str(args.catalogo),
        "--alvo-id",
        alvo_id,
        "--paginas",
        str(args.paginas),
        "--anuncios",
        str(args.anuncios),
        "--rodadas",
        str(args.rodadas),
    ]
    if args.javascript:
        argumentos.append("--javascript")
    if args.publicados_ontem:
        argumentos.append("--publicados-ontem")
    if args.publicados_em:
        argumentos.extend(("--publicados-em", args.publicados_em.isoformat()))
    return argumentos


def executar_todas_fontes(alvos, args):
    """Testa todas as fontes coletáveis, mantendo cada resultado isolado."""

    selecionados = [alvo for alvo in alvos if alvo.habilitado_para_coleta]
    if not selecionados:
        print("ERRO: o catálogo não possui fontes habilitadas para coleta.")
        return 2

    pasta_base = RAIZ_PROJETO / "outputs/testes_fontes"
    pasta_base.mkdir(parents=True, exist_ok=True)
    resultados = []

    print(f"Fontes selecionadas: {len(selecionados)}")
    print("Cada fonte terá pasta, respostas brutas e JSON próprios.")

    for indice, alvo in enumerate(selecionados, 1):
        antes = {pasta.resolve() for pasta in pasta_base.iterdir() if pasta.is_dir()}
        print(f"\n{'=' * 72}\n[{indice}/{len(selecionados)}] {alvo.alvo_id}\n{'=' * 72}")
        try:
            codigo = main(_argumentos_de_uma_fonte(args, alvo.alvo_id))
            erro = None
        except Exception as excecao:  # noqa: BLE001
            codigo = 1
            erro = f"{excecao.__class__.__name__}: {excecao}"
            print(f"ERRO inesperado: {erro}")

        depois = {pasta.resolve() for pasta in pasta_base.iterdir() if pasta.is_dir()}
        novas_pastas = sorted(depois - antes, key=lambda pasta: pasta.stat().st_mtime)
        resultados.append(
            {
                "alvo_id": alvo.alvo_id,
                "codigo_saida": codigo,
                "erro_inesperado": erro,
                "resultado": (
                    str(novas_pastas[-1].relative_to(RAIZ_PROJETO)) if novas_pastas else None
                ),
            }
        )

    nome_lote = datetime.now(UTC).strftime("lote_%Y%m%dT%H%M%SZ_") + uuid4().hex[:8]
    pasta_lote = pasta_base / nome_lote
    pasta_lote.mkdir()
    manifesto = {
        "versao_schema": 1,
        "gerado_em": datetime.now(UTC).isoformat(),
        "modo": "TESTE_LOCAL_SEM_MONGODB_SEM_API",
        "catalogo": str(args.catalogo),
        "fontes_selecionadas": len(selecionados),
        "resultados": resultados,
    }
    (pasta_lote / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    concluidas = sum(resultado["codigo_saida"] == 0 for resultado in resultados)
    print(f"\nFontes concluídas sem falha técnica: {concluidas}/{len(resultados)}")
    print(f"Manifesto do lote: {pasta_lote / 'manifesto.json'}")
    return 0 if concluidas == len(resultados) else 1


def main(argumentos=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogo", type=Path, default=RAIZ_PROJETO / "config/catalogo_fontes.csv"
    )
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument("--listar", action="store_true")
    modo.add_argument("--alvo-id")
    modo.add_argument("--todas", action="store_true", help="testa todas as fontes coletáveis")
    parser.add_argument("--javascript", action="store_true")
    parser.add_argument("--paginas", type=int, default=3, help="listagens (1 a 20)")
    parser.add_argument("--anuncios", type=int, default=20, help="detalhes (1 a 100)")
    parser.add_argument("--rodadas", type=int, default=5, help="expansões JavaScript (0 a 20)")
    datas = parser.add_mutually_exclusive_group()
    datas.add_argument("--publicados-ontem", action="store_true")
    datas.add_argument("--publicados-em", type=date.fromisoformat)
    args = parser.parse_args(argumentos)
    publicado_em = ontem_brasilia() if args.publicados_ontem else args.publicados_em
    if not 1 <= args.paginas <= 20 or not 1 <= args.anuncios <= 100 or not 0 <= args.rodadas <= 20:
        parser.error("limites: paginas 1..20, anuncios 1..100, rodadas 0..20")
    try:
        catalogo = carregar_alvos_csv_tolerante(args.catalogo)
        if args.listar:
            for alvo in catalogo.alvos:
                if alvo.habilitado_para_coleta:
                    print(f"{alvo.alvo_id} | {alvo.politica.status.value} | {alvo.url_inicial}")
            return 0
        if args.todas:
            return executar_todas_fontes(catalogo.alvos, args)
        alvo = replace(selecionar_alvo(catalogo.alvos, args.alvo_id), limite_paginas=args.paginas)
    except ValueError as erro:
        print(f"ERRO: {erro}")
        return 2
    nome = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    pasta = RAIZ_PROJETO / "outputs/testes_fontes" / nome
    pasta.mkdir(parents=True)
    csv = pasta / "catalogo.csv"
    cobertura = pasta / "cobertura.json"
    raw = pasta / "raw"
    _escrever_catalogo_temporario(csv, (alvo,))
    comando = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "catalogo_fontes",
        "-a",
        f"catalogo={csv}",
        "-a",
        f"limite_anuncios={args.anuncios}",
        "-a",
        f"relatorio_cobertura={cobertura}",
        "-s",
        f"RAW_STORAGE_DIRECTORY={raw}",
        "-s",
        f"JAVASCRIPT_ENABLED={args.javascript}",
        "-s",
        f"JAVASCRIPT_MAX_PAGES_PER_TARGET={args.paginas + args.anuncios}",
        "-s",
        f"JAVASCRIPT_MAX_ROUNDS={args.rodadas}",
        "-s",
        "CLOSESPIDER_TIMEOUT=300",
    ]
    print(f"Fonte: {alvo.alvo_id}\nURL: {alvo.url_inicial}\nResultado: {pasta}", flush=True)
    codigo = subprocess.run(comando, cwd=RAIZ_PROJETO, check=False).returncode
    if not raw.exists():
        print("Nenhuma resposta bruta salva. Consulte o log da coleta.")
        return codigo or 1
    resultado = processar_respostas_brutas(raw, alvo_id=alvo.alvo_id, publicado_em=publicado_em)
    coleta = json.loads(cobertura.read_text(encoding="utf-8")) if cobertura.exists() else None
    if coleta:
        for fonte in coleta.get("fontes", []):
            print(f"Páginas recebidas: {fonte['paginas_recebidas']}")
            print(f"Listagens agendadas: {fonte.get('listagens_agendadas', 0)}")
            print(f"Candidatos únicos: {fonte['candidatos_unicos']}")
            evidencias = fonte.get("candidatos_por_evidencia", {})
            if evidencias:
                texto_evidencias = ", ".join(
                    f"{nome}={quantidade}" for nome, quantidade in evidencias.items()
                )
                print(f"Mecanismos de descoberta: {texto_evidencias}")
            print(f"Detalhes agendados: {fonte.get('detalhes_agendados', 0)}")
            print(f"Detalhes com HTTP OK: {fonte['detalhes_http_ok']}")
            print(f"Candidatos não agendados: {len(fonte['candidatos_nao_agendados'])}")
            print(f"Detalhes sem resposta: {len(fonte.get('detalhes_sem_resposta', []))}")
            print(f"Erros de download: {len(fonte['erros'])}")
    else:
        print("Relatório de cobertura indisponível; confira se o crawler foi interrompido.")
    resumo = {
        "alvo_id": alvo.alvo_id,
        "codigo_coleta": codigo,
        "coleta": coleta,
        "anuncios_unicos": len(resultado.anuncios),
        "publicados_em": publicado_em.isoformat() if publicado_em else None,
        "outras_datas": resultado.anuncios_fora_data_publicacao,
        "sem_data_publicacao": resultado.anuncios_sem_data_publicacao,
        "paginas_analisadas": resultado.paginas_analisadas,
        "paginas_sem_vaga": resultado.paginas_sem_json_ld,
        "falhas_extracao": [asdict(f) for f in resultado.falhas],
        "anuncios_com_descricao": sum(
            bool(a.descricao_original.strip()) for a in resultado.anuncios
        ),
        "anuncios_com_empresa": sum(bool(a.empresa_original) for a in resultado.anuncios),
        "metricas_extracao": calcular_metricas_extracao(resultado),
    }
    (pasta / "resumo.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pasta / "anuncios.json").write_text(
        json.dumps(
            [a.model_dump(mode="json") for a in resultado.anuncios], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    for campo in (
        "paginas_analisadas",
        "anuncios_unicos",
        "anuncios_com_descricao",
        "anuncios_com_empresa",
    ):
        print(f"{campo}: {resumo[campo]}")
    print(f"Falhas de extração: {len(resultado.falhas)}")
    print(f"Outras datas: {resumo['outras_datas']}; sem data: {resumo['sem_data_publicacao']}")
    print(f"Relatório completo: {pasta / 'resumo.json'}")
    print("Teste salvo localmente. MongoDB e API não foram alterados.")
    return codigo or (1 if resultado.falhas else 0)


if __name__ == "__main__":
    raise SystemExit(main())
