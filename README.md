# Crawling de vagas de emprego

Sistema para descobrir vagas em páginas de carreira, preservar a evidência da
coleta, extrair dados estruturados, gravar anúncios no MongoDB e preparar
payloads para o Empregos.

O projeto não coleta vagas no Empregos. O Empregos é somente o destino final
de publicação, acionado separadamente e com confirmação explícita.

## Como funciona

```text
Página de carreiras
        ↓
Crawler: descobre listagens e vagas
        ↓
data/raw: preserva a resposta original
        ↓
Extrator: título, empresa, descrição, local e candidatura
        ↓
MongoDB: anúncio → empresa → vaga canônica
        ↓
Fila: valida os dados necessários
        ↓
Payload JSON: revisão e futura publicação no Empregos
```

O crawler foi pensado para rodar diariamente. Ele guarda histórico, isola
falhas por fonte, identifica conteúdo incompatível e prioriza novidades quando
há informações suficientes para isso.

## Regras importantes

- O crawler respeita `robots.txt`, timeouts, limites por domínio e bloqueios
  técnicos. Ele não tenta contornar CAPTCHA, WAF ou proibições explícitas.
- Coletar uma vaga não significa que ela pode ser republicada. A publicação
  depende da política e da autorização registrada para a fonte.
- URLs de domínios bloqueados (Empregos, Indeed, InfoJobs/Pandapé, Catho, Bluy,
  Cia de Estágios, Vagas.com, NIC.br, empregandobrasil.com.br e Gupy) são
  recusadas na coleta e na publicação. A lista completa e vigente está em
  `src/observatorio_vagas/domain/politica_fonte.py`.
- Nenhum comando publica no Empregos por acidente. A publicação exige uma
  confirmação própria e configuração válida no `.env`.

## Instalação

Requisitos: Windows, PowerShell, Python 3.11 ou superior e MongoDB acessível.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,crawler,mongodb,demo]"
Copy-Item .env.example .env
```

Edite o `.env` com a URI e o banco MongoDB. Nunca envie esse arquivo ao
GitHub: ele contém configurações privadas e já está no `.gitignore`.

## Adicionar uma fonte

Edite [`config/catalogo_fontes.csv`](config/catalogo_fontes.csv). Ele possui
uma única coluna chamada `url`:

```csv
url
https://empresa.com.br/trabalhe-conosco/
https://jobs.lever.co/empresa
```

Adicione uma página de carreiras por linha. O sistema cria automaticamente o
identificador, nome provisório, tipo de fonte e limite inicial seguro. Uma nova
fonte pode ser coletada, mas só entra nos payloads após sua aprovação.

Fluxo atual de autorização (07/10/2026): a aprovação não é feita fonte a fonte.
O usuário cola as URLs que autoriza no fim de `catalogo_fontes.csv` e de
`fontes_autorizadas.csv` (hoje em `config/lote_10mil/`, ~14.450 URLs) e roda
`scripts/triar_catalogo.py`, que gera o catálogo usado nas coletas
(`config/lote_10mil_triado/catalogo_fontes.csv`, ~12.140 URLs). A fila de
publicação usa `--somente-catalogo` com esse catálogo triado. Quem decide o que
é autorizado é o usuário; domínios bloqueados são sempre recusados.

Mais detalhes: [config/README.md](config/README.md).

## Rotina diária

### Receber uma lista grande de URLs já licenciadas

Quando receber a lista, salve-a como TXT (uma URL por linha) ou CSV com a
coluna `url`. Este comando cria um catálogo isolado, remove URLs repetidas,
valida a política técnica e registra como licenciadas apenas as URLs aceitas:

```powershell
.\.venv\Scripts\python.exe scripts\preparar_lote_urls_licenciadas.py `
    --entrada C:\caminho\urls_licenciadas.csv `
    --diretorio-saida outputs\lote_10000_urls
```

Depois, execute o catálogo recém-gerado no Mac com mais paralelismo:

```bash
./.venv/bin/python scripts/processar_lote.py \
  --catalogo outputs/lote_10000_urls/catalogo_fontes.csv \
  --coletar \
  --confirmar \
  --somente-republicaveis \
  --limite-anuncios 10000 \
  --alvos-por-coleta 200 \
  --processos-coleta 8 \
  --trabalhadores-posprocessamento 32
```

O relatório `relatorio_importacao.json` explica cada URL recusada. O comando
de preparação não coleta páginas e não grava no MongoDB.

### 1. Coletar e gravar no MongoDB

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py `
    --coletar `
    --confirmar `
    --somente-republicaveis `
    --limite-anuncios 500 `
    --processos-coleta 3 `
    --trabalhadores-posprocessamento 16
```

| Opção | Significado |
| --- | --- |
| `--coletar` | Busca páginas novas na internet. |
| `--confirmar` | Autoriza gravação no MongoDB; não publica no Empregos. |
| `--somente-republicaveis` | Considera apenas fontes aprovadas para publicação. |
| `--limite-anuncios 500` | Limite de detalhes por fonte; evita leituras longas demais. |
| `--processos-coleta 3` | Executa três filas de domínios em paralelo, sem dividir um mesmo domínio entre processos. |
| `--trabalhadores-posprocessamento 16` | Usa mais capacidade do computador ao criar vagas canônicas. |

Para muitas fontes, também é possível dividir o catálogo por domínio e rodar
três crawlers em paralelo. Os três apenas salvam em `data/raw`; depois, execute
uma única vez `processar_lote.py --coletado-desde ... --confirmar` para gravar
o lote completo no MongoDB.

### 2. Gerar payloads para revisão

Este comando não publica nada. Ele cria JSONs locais para conferência:

```powershell
.\.venv\Scripts\python.exe scripts\preparar_fila_empregos.py `
    --catalogo config\catalogo_fontes.csv `
    --somente-catalogo `
    --limite 10000 `
    --saida-json outputs\relatorios\fila_atual.json `
    --diretorio-payloads outputs\payloads\para_publicar
```

O arquivo principal é:

```text
outputs/payloads/para_publicar/lote-.../payloads_unificados.json
```

Ele contém todos os payloads do lote. O `manifesto.json` explica o conteúdo e
a pasta `payloads/` permite revisar uma vaga individualmente.

### 3. Publicar no Empregos

Publicação é a última etapa. Primeiro valide somente a configuração:

```powershell
.\.venv\Scripts\python.exe scripts\verificar_configuracao_publicacao_empregos.py
```

Depois siga [docs/publicacao_teste_api.md](docs/publicacao_teste_api.md).
Comece com uma única vaga de teste e mantenha o modo de simulação até conferir
o resultado.

## Desempenho e leitura completa

- `processar_lote.py` extrai em paralelo (`--processos-extracao`, padrão: núcleos − 1;
  abaixo de 300 páginas roda em série). Alvos grandes são divididos em pedaços.
- Depois de `--coletar`, o inventário vem dos cadernos JSONL do lote
  (`data/raw/cadernos/`), não de um JSON por resposta. Com `--coletado-desde`, só as
  pastas dos dias do período são lidas.
- Empresas e vagas são gravadas em lote, numa conexão MongoDB por execução.
- O fim do log mostra `## TEMPO POR FASE` (coleta, inventário, extração, MongoDB).
- `--cache-extracao` reaproveita a análise de páginas idênticas (útil ao reprocessar
  o mesmo dia; desligado por padrão).
- Cada anúncio guarda `campos_estruturados["_leitura"]`: os campos da API lidos de
  todas as camadas da página e o `_diagnostico` (origem de cada campo, onde cada vazio
  foi procurado, o que não foi usado). O `_diagnostico` vai nos arquivos da fila, ao
  lado do payload, nunca na API. Vaga sem nome de empresa exibido não é publicada.
- `scripts/avaliar_leitura.py` e `scripts/comparar_extracao.py` medem a leitura e
  comparam extrações antes/depois de mudar um extrator.
- Para medir no Mac: [docs/roteiro_medicao_mac.md](docs/roteiro_medicao_mac.md).

## Onde ficam os resultados

| Pasta | Conteúdo |
| --- | --- |
| `data/raw/` | Páginas e respostas originais coletadas. |
| `outputs/relatorios/` | Filas, ranking e resumos por fonte. |
| `outputs/payloads/para_publicar/` | Próximos lotes JSON para revisão. |
| `outputs/cobertura/` | Métricas de coleta: páginas lidas, falhas e duração. |
| `outputs/logs/` | Logs para investigar falhas. |
| `outputs/testes_fontes/` | Testes individuais e evidências de fontes. |
| `outputs/historico/` | Lotes e relatórios antigos preservados. |

No projeto local, `outputs/LEIA_PRIMEIRO.md` também resume essa organização.

## Estrutura do código

```text
config/                       URLs das fontes
dashboard/                    Painéis de inspeção em Streamlit
docs/                         Guias técnicos e procedimentos
scripts/                      Comandos de operação e diagnóstico
src/observatorio_vagas/
  crawling/                   Spider, adaptadores, limites e armazenamento bruto
  extraction/                 Leitura de HTML/JSON e normalização
  domain/                     Regras de negócio e modelos
  storage/mongodb/            Persistência no MongoDB
  integrations/empregos/      Fila, payload e publicação idempotente
tests/                        Testes automatizados
```

## Diagnóstico rápido

Quantidade de anúncios por fonte:

```powershell
Get-Content outputs\relatorios\anuncios_por_fonte.csv
```

Ranking atualizado das fontes:

```powershell
.\.venv\Scripts\python.exe scripts\gerar_ranking_fontes.py
```

Teste de uma fonte isolada:

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py --url "https://empresa.com.br/trabalhe-conosco/"
```

Painéis locais:

```powershell
.\.venv\Scripts\streamlit.exe run dashboard\coletas.py
.\.venv\Scripts\streamlit.exe run dashboard\app.py
```

## Testes e qualidade

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests scripts dashboard
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts dashboard
.\.venv\Scripts\python.exe -m pytest
```

Os testes não publicam vagas nem devem alterar o MongoDB real.

## GitHub

O repositório guarda código, documentação, testes e o catálogo de URLs. Ele
não guarda `.env`, dados brutos, payloads, logs, resultados de coleta ou caches
locais.
