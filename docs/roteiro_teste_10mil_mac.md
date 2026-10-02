# Teste das 10 mil fontes no Mac

Objetivo: rodar o lote inteiro uma vez e sair com o máximo de dados sobre
tempo, recursos da máquina e resultado, para decidir o que ainda vale otimizar.

Quem mede tudo é `scripts/medicao_completa.py`: ele roda o `processar_lote.py`
e, enquanto isso, anota CPU, memória, disco, rede e quantos anúncios já estão
no MongoDB a cada 10 s. No fim monta um relatório. No Mac ele também usa
`caffeinate`, para a máquina não dormir.

## 1. Preparar o Mac (uma vez)

```bash
cd caminho/do/projeto
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[crawler,mongodb,dev,medicao]"
cp /caminho/seguro/.env .env        # nunca pelo git
ulimit -n 10240                     # arquivos abertos; repita em cada terminal
```

- Tire a pasta `data/` do Spotlight: Ajustes do Sistema → Spotlight → Privacidade.
- Ligue o Mac na tomada e desligue o modo de economia de energia.
- Abra o túnel SSH do MongoDB (porta `27019`) e confirme:
  `python scripts/acompanhar_mongo.py --uma-vez`
- Espaço em disco: reserve ao menos 30 GB livres (as páginas novas ficam comprimidas).

## 2. Teste de disco (2 minutos)

```bash
python scripts/medir_disco.py --paginas 200
```

Compara a 1ª leitura de arquivos com a 2ª. Se a 1ª for dezenas de vezes mais
lenta (no Windows eram 2 arquivos/s contra 2.900/s), alguma verificação lê cada
arquivo novo. No Mac, isso costuma ser Spotlight ou antivírus.

## 3. Escada de testes (não pule para as 10 mil)

Cada degrau mostra um problema diferente, com custo bem menor.

**A. 50 fontes (cerca de 10 min): o pipeline inteiro funciona?**

```bash
python scripts/medicao_completa.py rodar --nome mac_50 -- \
  --coletar --catalogo config/catalogo_50.csv --confirmar \
  --processos-coleta 4 --limite-anuncios 50 --janela-horas 24
```

**B. 1.000 fontes (1 a 4 h): a configuração de produção.**

```bash
python scripts/medicao_completa.py rodar --nome mac_1000 -- \
  --coletar --catalogo config/lote_1000/catalogo_fontes.csv --confirmar \
  --processos-coleta 8 --alvos-por-coleta 2000 --tempo-maximo-bloco 120 \
  --limite-anuncios 200 --janela-horas 24
```

**C. 10 mil fontes: o teste absoluto.**

```bash
python scripts/medicao_completa.py rodar --nome mac_10mil -- \
  --coletar --catalogo config/catalogo_10mil.csv --confirmar \
  --processos-coleta 12 --alvos-por-coleta 2000 --tempo-maximo-bloco 180 \
  --processos-extracao 10 \
  --limite-anuncios 200 --janela-horas 24
```

Ajustes que valem a pena conhecer:

| Opção | Efeito |
|---|---|
| `--processos-coleta` (1 a 12) | Sites diferentes ao mesmo tempo. Ajuda só se houver muitos domínios grandes. |
| `--processos-extracao` | Padrão: núcleos menos 1. É CPU pura; se o disco for rápido, mais núcleos ajudam. |
| `--tempo-maximo-bloco N` | Cada bloco de coleta fecha em N minutos; o resto continua no dia seguinte. |
| `--limite-anuncios` | Teto de vagas por fonte. |
| `--janela-horas 24` | Para uma fonte quando as vagas passam de 24 h (precisa de data na página). |
| `--sem-descarte-mongo` | Não consulta o Mongo para pular vagas já gravadas. |

Ritmo por site: `config/ritmo_sites.csv` (padrão 2 requisições por segundo; o
maior site 4). Use `python scripts/ritmo_sites.py listar` para conferir.

Regras para as 10 mil:
- Registre as URLs com `scripts/preparar_lote_urls_licenciadas.py`. Esse script
  marca tudo como autorizado e **não confere licença**; isso é decisão sua.
- Rode **um lote por vez** com `--confirmar`. Dois lotes em paralelo podem
  duplicar empresas no MongoDB.
- Nada disso publica no Empregos. A publicação é uma etapa separada.

## 4. Durante o teste

```bash
tail -f outputs/medicao/*_mac_10mil/lote.log                       # o que o lote diz
python scripts/acompanhar_mongo.py                                  # anúncios, vagas e empresas
tail -n 3 outputs/medicao/*_mac_10mil/amostras.csv                 # CPU, memória e disco agora
```

`Ctrl+C` encerra o lote e ainda gera o relatório do que foi feito. Rodar de novo
com o mesmo comando continua de onde parou: o estado incremental fica em
`<pasta do catálogo>/.cache/lote/` (não apague essa pasta).

## 5. O que sai no fim

Em `outputs/medicao/<data>_<nome>/`:

| Arquivo | Conteúdo |
|---|---|
| `relatorio.md` | Resumo: tempo por fase, resultado, coleta, recursos, problemas. **Comece por ele.** |
| `resumo.json` | Os mesmos números, para comparar execuções por script. |
| `ambiente.json` | Chip, núcleos, RAM, versões, commit do git, catálogo, ritmo por site. |
| `amostras.csv` | Série no tempo: CPU, memória, disco, rede, processos e anúncios no Mongo. |
| `por_site.csv` | Páginas, bytes, detalhes e erros HTTP por site. |
| `lote.log` | A saída completa do lote. |

Para refazer o relatório depois: `python scripts/medicao_completa.py relatorio outputs/medicao/<pasta>`.

## 6. Como ler o resultado

| Sinal no relatório | Significa |
|---|---|
| CPU total média perto de 100% na extração | Limitado por processador: mais núcleos ajudam. |
| CPU baixa e leitura de disco com muitas operações por segundo | Limitado por disco ou verificação de arquivos (Spotlight, antivírus). |
| Fase Coleta domina e o maior site tem a maioria das páginas | Limitado pelo ritmo do site: veja `ritmo_sites.csv`. |
| Fase Empresas/vagas grande | Pós-processamento é o gargalo (roda em série). |
| Muitos 429/403 em `por_site.csv` | Algum site está pedindo calma: baixe o ritmo dele. |
| `Bloco com erro` ou `Traceback` > 0 | Veja o `lote.log` na linha do erro. |
| Memória dos processos do lote perto do total | Reduza `--processos-extracao`. |

## 7. O que me enviar

```bash
cd outputs/medicao
tail -n 400 <pasta>/lote.log > <pasta>/lote_final.log
zip -r mac_10mil.zip <pasta> -x '*/lote.log'
```

Mande o `mac_10mil.zip` e o `lote_final.log` (as últimas 400 linhas do log).
O `relatorio.md` sozinho já responde à maior parte das perguntas.
