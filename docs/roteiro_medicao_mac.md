# Roteiro de medição no Mac

Objetivo: descobrir onde está o gargalo na máquina de produção. As medições
feitas no notebook Windows foram limitadas pelo disco (antivírus), então não
valem para o Mac.

## 0. Preparar o terminal

```bash
cd caminho/do/projeto
source .venv/bin/activate
ulimit -n 10240
```

Tire `data/` do Spotlight: Ajustes do Sistema → Spotlight → Privacidade.

## 1. Extração em série e em paralelo (sem rede, sem MongoDB)

Use um dia que já esteja em `data/raw` (troque a data):

```bash
caffeinate -i python scripts/processar_lote.py --coletado-desde 2026-09-30T00:00:00+00:00 --processos-extracao 1 > medicao_1.log
```

```bash
caffeinate -i python scripts/processar_lote.py --coletado-desde 2026-09-30T00:00:00+00:00 > medicao_n.log
```

No fim de cada log, a seção `## TEMPO POR FASE` mostra inventário e extração.
Se a extração com N processos for perto de (tempo com 1) ÷ N, o gargalo é CPU.
Se quase não cair, o gargalo é disco.

## 2. Custo por página e por extrator

```bash
python scripts/comparar_extracao.py gravar --desde 2026-09-30T00:00:00+00:00 --saida outputs/medicao/referencia.pkl
```

A linha `CPU somada ... ms/página` dá o custo médio por página.

## 3. Lote real pequeno com gravação

Com o túnel do MongoDB aberto, num catálogo de 5 a 20 alvos:

```bash
caffeinate -i python scripts/processar_lote.py --coletar --catalogo config/coleta_5_fontes.csv --confirmar > medicao_lote.log
```

`## TEMPO POR FASE` separa coleta, inventário, extração e empresas/vagas.

## 4. O que me enviar

As seções `## TEMPO POR FASE` dos três logs, o modelo do Mac (chip, núcleos e
RAM) e a linha `CPU somada` do passo 2.
