# Observatório de Vagas

Pipeline para coletar vagas públicas de fontes autorizadas, preservar a
resposta original, extrair e normalizar informações, armazenar os resultados
no MongoDB e preparar vagas elegíveis para a API do Empregos.

O Empregos é somente o destino. O crawler não coleta vagas do site Empregos.

## Fluxo principal

```text
catálogo de fontes
        ↓
Scrapy + política + robots.txt
        ↓
respostas brutas em data/raw
        ↓
extração de JSON-LD/HTML/PDF
        ↓
anúncio → empresa → vaga canônica
        ↓
MongoDB
        ↓
24 campos + elegibilidade + payload
        ↓
API do Empregos (somente com confirmação explícita)
```

Uma falha em um alvo é isolada: o processamento em lote registra o problema e
continua nos demais sites. Coletar uma página não significa que sua vaga pode
ser republicada. A decisão final também considera a política da fonte, a
expiração e os campos realmente obrigatórios da API.

## Estrutura

```text
config/catalogo_fontes.csv       biblioteca canônica de URLs e políticas
dashboard/                       telas simples de inspeção
data/                            catálogos auxiliares e dados locais
docs/                            documentação funcional
scripts/                         comandos operacionais e diagnósticos
src/observatorio_vagas/
  crawling/                      coleta, descoberta e armazenamento bruto
  domain/                        modelos e regras de negócio
  extraction/                    extração, enriquecimento e normalização
  integrations/empregos/         payload, cliente e publicação idempotente
  storage/                       contratos de persistência
  storage/mongodb/               implementações e índices do MongoDB
tests/                            testes automatizados
```

## Requisitos e instalação

- Python 3.11 ou superior
- MongoDB acessível pela URI configurada

No Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,crawler,mongodb,demo]"
```

Copie `.env.example` para `.env` e ajuste os valores do ambiente. O MongoDB
atual pode estar na VM; futuramente basta trocar `OBS_MONGODB_URI` e
`OBS_MONGODB_DATABASE` para usar o servidor definitivo.

Nunca envie `.env`, chaves, tokens ou credenciais para o repositório.

## Catálogo e política das fontes

As fontes ficam em [`config/catalogo_fontes.csv`](config/catalogo_fontes.csv).
Esse é o único CSV operacional: ele tem apenas a coluna `url`. Adicione uma URL
de página de carreiras por linha e rode normalmente o crawler. O sistema cria
o identificador, o nome provisório e a configuração de coleta automaticamente.

URLs de Gupy, Indeed, Catho e InfoJobs/Pandapé são recusadas. Uma URL válida é
coletável, mas a publicação continua bloqueada até a autorização da empresa ser
registrada. O guia rápido está em [`config/README.md`](config/README.md).

Cada alvo possui uma política independente. Estados como `somente_coleta`
permitem testes e preservação da página, mas impedem a republicação. Uma
fonte bloqueada ou não cadastrada não deve ser contornada.

## Operação segura

Os comandos de transformação usam modo de prévia por padrão. Revise a saída
antes de acrescentar `--confirmar`.

Coletar e processar todo o catálogo:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletar
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletar --confirmar
```

Reprocessar respostas já armazenadas:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py `
  --coletado-desde 2026-08-26T14:00:00+00:00
```

Resolver empresas e criar vagas canônicas de um alvo:

```powershell
.\.venv\Scripts\python.exe scripts\resolver_empresas_anuncios.py `
  --alvo-id ID_DO_ALVO
.\.venv\Scripts\python.exe scripts\criar_vagas_canonicas.py `
  --alvo-id ID_DO_ALVO
```

Listar e diagnosticar os 24 campos da API:

```powershell
.\.venv\Scripts\python.exe scripts\diagnosticar_vaga_empregos.py --listar
.\.venv\Scripts\python.exe scripts\diagnosticar_vaga_empregos.py `
  --anuncio-id UUID_DO_ANUNCIO --vaga-id UUID_DA_VAGA
```

Preparar uma fila consolidada, sem gravar nem publicar:

```powershell
.\.venv\Scripts\python.exe scripts\preparar_fila_empregos.py `
  --limite 100 --saida-json outputs\fila_empregos.json `
  --diretorio-payloads outputs\preparacao_empregos
```

Para exportar somente as vagas publicadas ontem no horário de Brasília, acrescente
`--publicados-ontem`. Para uma data específica, use `--publicados-em YYYY-MM-DD`.

A fila separa vagas elegíveis, bloqueadas e operações já registradas no
histórico. Ela fornece os UUIDs necessários para revisar uma vaga no comando
individual de diagnóstico ou publicação. Quando `--diretorio-payloads` é
informado, ela cria uma pasta de lote com `manifesto.json`, um JSON por vaga
elegível e `payloads_unificados.json`, uma lista com todos os corpos de payload
do lote. Essa preparação é local: não chama a API e não grava nada no MongoDB.

Publicar uma vaga exige `--confirmar-publicacao`, fonte aprovada, vaga
elegível, configuração oficial da API e o kill switch habilitado. Sem essa
opção, o comando apenas simula:

```powershell
.\.venv\Scripts\python.exe scripts\publicar_vaga_empregos.py `
  --anuncio-id UUID_DO_ANUNCIO --vaga-id UUID_DA_VAGA
```

O histórico de publicação usa uma chave idempotente para impedir o reenvio
acidental da mesma operação.

Simular um lote controlado de até dez vagas elegíveis:

```powershell
.\.venv\Scripts\python.exe scripts\publicar_lote_empregos.py `
  --limite 100 --maximo-envios 10 `
  --saida-json outputs\publicacao_lote_empregos.json
```

Para realizar os POSTs do mesmo lote, revise a simulação e acrescente
`--confirmar-publicacao`. O modo real também exige ambiente de produção,
endpoint, autenticação e kill switch configurados no `.env`. Uma falha fica
registrada no item correspondente e não interrompe as vagas seguintes. Itens
bloqueados, excedentes ou com histórico nunca são enviados automaticamente.

O roteiro direto para configurar a API e enviar exatamente uma vaga de teste
está em [`docs/publicacao_teste_api.md`](docs/publicacao_teste_api.md). Antes
do POST, execute `scripts/verificar_configuracao_publicacao_empregos.py`: ele
verifica somente a configuração, sem acessar MongoDB ou a API.

## Dashboard

As telas são ferramentas de inspeção, não o produto final:

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

Os testes unitários usam objetos falsos para validar os repositórios; a suíte
não deve publicar vagas nem alterar o MongoDB real.

## Responsabilidade operacional

O projeto deve respeitar autorizações, `robots.txt`, limites de requisição,
termos aplicáveis, privacidade e retenção. A permissão de coleta e a permissão
de republicação são decisões diferentes e ficam registradas no catálogo.
