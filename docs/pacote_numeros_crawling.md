

=====  ARQUIVO: docs/otimizacao_crawling_20260911.md  =====

# Otimização e fontes — 11/09/2026

## Desempenho

- A extração do lote agora roda no mesmo Python, sem iniciar um interpretador
  para cada alvo. Coleta Scrapy e etapas posteriores de normalização continuam
  usando seus subprocessos.
- O inventário de metadados é lido uma vez, depois de concluir a coleta, e
  compartilhado entre as extrações. Não há cache permanente: um novo lote
  relê o inventário para enxergar novas respostas.
- Os filtros de alvo/data, verificação de SHA-256, tamanho e caminho dos corpos
  continuam ativos. Os contadores de páginas ignoradas preservam o significado.
- Exceções de um extrator continuam isoladas por alvo. Interrupções do usuário
  não são tratadas como falhas comuns.
- Concorrência global: 8 → 16. Por domínio: continua 1, com intervalo de 1 segundo,
  AutoThrottle e robots.txt. Muitos alvos no mesmo domínio não ganham 16 conexões.
- Não foi medido um percentual de ganho de ponta a ponta. O ganho depende da
  quantidade de alvos, dos arquivos locais e da velocidade dos sites.

## Limpeza

Removidas 132 pastas `.pytest_cache*`/`.pytest_tmp*` da raiz, após validar o caminho
e verificar ausência de links. São caches e dados de testes regeneráveis;
a remoção foi direta, sem Lixeira. Outras 33 pastas foram preservadas por falta
de acesso seguro. Dados brutos, relatórios, backups, `.env` e `.venv` não foram apagados.
Também foi removida uma declaração duplicada de `coletado_em` do inventário.

## Fontes adicionadas

### Open Knowledge Brasil

- Entrada: https://ok.org.br/noticias/
- Evidência: o rodapé declara CC BY 4.0 para conteúdo próprio, salvo exceções.
- Exemplo de contratação própria (histórico, não vaga vigente):
  https://ok.org.br/noticia/open-knowledge-brasil-abre-vaga-para-analista-de-captacao-de-recursos/
- Exige autoria/fonte, link da licença e indicação de alterações quando houver.

### InternetLab

- Entrada: https://internetlab.org.br/pt/blog/
- Evidência: rodapé do site e da página de contratação declara CC BY-SA 4.0.
- Política: https://internetlab.org.br/pt/politica-de-privacidade/
- Exemplo histórico:
  https://internetlab.org.br/pt/noticias/internetlab-abre-selecao-para-pesquisadora-e-estagio-em-comunicacao-e-pesquisa/
- Exige atribuição e compartilhamento sob a mesma licença quando aplicável.
  Não transferir automaticamente essa permissão a material de terceiros.

As entradas usam descoberta HTML existente, com limite de 10 páginas por alvo.
Notícias gerais não devem ser consideradas vagas; links de candidatura externos
não ampliam a permissão de coleta para outros domínios. Não foi demonstrada
extração de vagas vigentes dessas duas fontes nesta alteração. Conteúdo dinâmico,
anúncios com múltiplas posições ou datas em texto podem exigir adaptação adicional.
Os exemplos acima são antigos e não devem ser publicados como oportunidades abertas.

Wikimedia Brasil também foi investigada, mas a listagem respondeu HTTP 403
neste ambiente; não entrou no catálogo operacional. ARTIGO 19 e Terra de Direitos
foram descartadas desta ampliação por restrição de uso não comercial.

## Testar sem Mongo e sem publicar

No PowerShell, na raiz do projeto, execute em uma linha:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes_20260911_teste.csv --coletar
```

Esse comando acessa as duas fontes, salva respostas brutas localmente e mostra
a prévia da extração. Não grava anúncios no MongoDB e não publica no Empregos.
As duas fontes também estão nos catálogos central, diário, republicável e na
biblioteca `config/urls_fontes.txt`.


=====  ARQUIVO: docs/roteiro_teste_10mil_mac.md  =====

# Teste das 10 mil fontes no Mac

Objetivo: rodar o lote inteiro uma vez e sair com o máximo de dados sobre
tempo, recursos da máquina e resultado, para decidir o que ainda vale otimizar.

Quem mede tudo é `scripts/medicao_completa.py`: ele roda o `processar_lote.py`
e, enquanto isso, anota CPU, memória, disco, rede e quantos anúncios já estão
no MongoDB a cada 10 s. No fim monta um relatório. No Mac ele também usa
`caffeinate`, para a máquina não dormir.

## 1. Preparar o Mac (uma vez)

Copie os comandos **sem comentários**: no terminal do Mac (zsh), o que vem depois de `#`
vira argumento do comando e dá "too many arguments".

```bash
cd caminho/do/projeto
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[crawler,mongodb,dev,medicao]"
cp /caminho/seguro/.env .env
ulimit -n 10240
```

- Tire a pasta `data/` do Spotlight: Ajustes do Sistema → Spotlight → Privacidade.
- Ligue o Mac na tomada e desligue o modo de economia de energia.
- Abra o túnel SSH do MongoDB (porta `27019`) e confirme:
  `python scripts/acompanhar_mongo.py --uma-vez`
- Espaço em disco: reserve ao menos 30 GB livres (as páginas novas ficam comprimidas).

### Túnel SSH para o MongoDB

O `.env` aponta para `127.0.0.1:27019`; o túnel leva essa porta local até o
MongoDB da VM (porta 27017 lá). No Windows ele é:

```
ssh -p 2222 -N -L 27019:127.0.0.1:27017 longhini@<servidor>
```

No Mac, a forma mais estável é um apelido em `~/.ssh/config`:

```
Host mongo-vm
    HostName <servidor>
    Port 2222
    User longhini
    IdentityFile ~/.ssh/id_ed25519
    LocalForward 27019 127.0.0.1:27017
    ServerAliveInterval 30
    ServerAliveCountMax 3
    ExitOnForwardFailure yes
```

Chave de acesso: gere uma nova no Mac (`ssh-keygen -t ed25519`) e peça para
adicionar `~/.ssh/id_ed25519.pub` ao `authorized_keys` do usuário na VM, ou copie
a chave que você usa no Windows (por AirDrop ou pendrive, nunca por e-mail ou git)
e rode `chmod 600 ~/.ssh/id_ed25519`. Teste uma vez com `ssh mongo-vm` para aceitar
o host.

Abrir, conferir e fechar:

```bash
ssh -f -N mongo-vm
lsof -nP -iTCP:27019 -sTCP:LISTEN
python scripts/acompanhar_mongo.py --uma-vez
pkill -f "ssh -f -N mongo-vm"
```

Para um teste de horas, prefira `brew install autossh` e
`autossh -M 0 -f -N mongo-vm`: ele religa o túnel se a conexão cair. Se o túnel cair
no meio, as gravações no MongoDB falham, e o lote registra o erro.

### As 10 mil fontes

Mantenha a lista fora do git (ela pode ser licenciada) e use o script que valida,
normaliza e remove duplicadas:

```bash
mkdir -p ~/urls && cp /onde/estiver/urls_10mil.txt ~/urls/
python scripts/preparar_lote_urls_licenciadas.py \
  --entrada ~/urls/urls_10mil.txt --diretorio-saida config/lote_10mil
cat config/lote_10mil/relatorio_importacao.json | head -40
```

Isso gera `config/lote_10mil/catalogo_fontes.csv` e `fontes_autorizadas.csv`; use o
primeiro como `--catalogo`. O script marca todas as URLs como autorizadas para
publicação e não confere licença. Isso é decisão sua.

Antes do teste grande, confira a composição do catálogo:

```bash
python scripts/ritmo_sites.py listar
python - <<'EOF'
import collections, csv
from urllib.parse import urlsplit
c = collections.Counter(urlsplit(l["url"]).hostname for l in csv.DictReader(open("config/lote_10mil/catalogo_fontes.csv")))
print(len(c), "domínios; maiores:", c.most_common(10))
EOF
```

Se um domínio tiver centenas de fontes, é ele que vai decidir o tempo da coleta
(ritmo do site × páginas); ajuste a linha dele em `config/ritmo_sites.csv` só depois
de medir (`python scripts/ritmo_sites.py sondar <site>`).

Para um ensaio, use um pedaço do catálogo: `head -n 1001 config/lote_10mil/catalogo_fontes.csv > config/catalogo_1000_do_10mil.csv`
(a primeira linha é o cabeçalho `url`).

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
tail -f outputs/medicao/*_mac_10mil/lote.log
python scripts/acompanhar_mongo.py
tail -n 3 outputs/medicao/*_mac_10mil/amostras.csv
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


=====  ARQUIVO: docs/roteiro_medicao_mac.md  =====

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
