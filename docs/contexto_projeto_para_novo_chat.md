# Contexto do projeto para continuar em outro chat

Escrito em 2026-10-02. Cole este arquivo no início do novo chat e diga o que quer
fazer em seguida. O repositório fica em
`C:\Users\Inspirion\Documents\Codex\2026-08-18\https-github-com-pedrolonghini-web-scrapping`
(Windows, PowerShell). O usuário vai passar a rodar também num Mac.

## 1. O que é o projeto
Crawler de vagas de emprego do Empregos.com (pacote `observatorio_vagas`).
Fluxo: catálogo de fontes (CSV com a coluna `url`) → coleta com Scrapy →
respostas brutas em `data/raw/` → extração → anúncios no MongoDB → vagas
canônicas e empresas → payloads JSON para a API do Empregos (a publicação é uma
etapa separada e nunca acontece sem confirmação explícita do usuário).

Regras do projeto: respeitar `robots.txt` e o ritmo de cada site; não contornar
login/CAPTCHA/WAF; o Empregos é destino, nunca fonte; "site público" não é
licença de republicação; credenciais só no `.env` (ignorado pelo git).

Guia detalhado do usuário: `docs/guia_completo_projeto_e_crawling.md`.
README tem a seção "Desempenho e leitura completa".

## 2. Objetivo atual do usuário
Rodar o crawler **uma vez por dia** para descobrir **vagas novas**, começando por
**~1.000 fontes** (depois, 10 mil). Prioridade: velocidade e qualidade dos 24 campos da API.

## 3. Estado do git
- Branch: `melhorias-leitura-desempenho` (a `main` não foi alterada).
- Enviados ao GitHub (`origin`): `2663bf5` (leitura completa + desempenho) e
  `3ea306a` (correções da segunda revisão).
- **Commit local ainda não enviado:** `09a1db4` (persiste o estado incremental do
  lote). Perguntei se podia enviar; sem resposta ainda.
- **Não comitados:** `scripts/dividir_catalogo.py` (pronto e testado) e
  `src/observatorio_vagas/extraction/listagem_html.py` (**rascunho, não ligado ao
  fluxo**, com falsos positivos). `config/lote_1000/` é do usuário (catálogo do lote).
- 860 testes passam (`pytest tests -q`). Ruff limpo, exceto o rascunho `listagem_html.py`.
- Remoto: https://github.com/PedroLonghini/crawling_vagas_de_emprego

## 4. O que foi feito (por tema)

### 4.1 Desempenho do lote (`scripts/processar_lote.py`)
- Pós-processamento (empresas e vagas) em processo e **em lote**, com uma conexão
  MongoDB por execução e `preparar_banco` uma vez. Antes eram 2 subprocessos Python
  por anúncio (~2 s cada). Lote de 5 alvos: ~200+ s → ~9 s.
  Funções: `resolver_e_associar_empresas_em_lote` (`extraction/resolucao_empresa.py`),
  `processar_anuncios_em_lote` (`scripts/criar_vagas_canonicas.py`),
  `buscar_por_ids` (`storage/mongodb/vagas.py`).
- Inventário: `carregar_inventario_bruto_desde` (só as pastas dos dias do lote) e
  **cadernos JSONL** gravados durante a coleta (`-s RAW_INDEX_FILE`, em
  `data/raw/cadernos/lote_*/`); ler o histórico inteiro custava ~17 min.
- Extração em paralelo (`--processos-extracao`, padrão núcleos−1, `spawn`), alvos
  grandes divididos em pedaços de 100 páginas (`extrair_parcial`/`combinar_parciais`
  em `extraction/processador.py`), fallback em série se o pool quebrar, abaixo de
  300 páginas roda em série.
- Seção `## TEMPO POR FASE` no fim de todo log.
- `--cache-extracao` (SQLite, `extraction/cache_paginas.py`): desligado por padrão;
  só ~8% das páginas repetem entre dias.
- Correção de saída no Windows (`reconfigure(errors="backslashreplace")`); um
  caractere fora do cp1252 derrubava o lote.
- **Estado incremental persistente** (`09a1db4`): o spider já era incremental, mas o
  lote gravava o estado ao lado do catálogo **temporário** e o perdia. Agora é
  `<pasta do catálogo>/.cache/lote/fila_N.json`, um por fila de coleta (não mexer
  nessa pasta e manter o mesmo `--processos-coleta`). Verificado: dias simulados
  baixam só as vagas ainda não vistas.

### 4.2 Leitura completa dos 24 campos (especificação "ler e interpretar tudo")
Pacote `src/observatorio_vagas/extraction/leitura/`:
- `camadas.py`: inventário da página (JSON-LD, JSON embutido, meta, cabeçalho,
  seções, ações, rodapé).
- `json_embutido.py`: lê `__NEXT_DATA__`, `window.__X__` e Nuxt 2 (IIFE) sem executar JS.
- `texto_livre.py`: modalidade (baseada em `modalidade_ref.py` do usuário), vínculo,
  nível, salário, CNPJ, CEP, e-mail, recrutador, expiração.
- `interpretacao.py`: `ler_vaga` (24 campos na ordem estruturado → cabeçalho → seção →
  texto), `_diagnostico` (origem, onde cada vazio foi procurado, `nao_mapeado`),
  validações (applyUrl, descrição, endereço, CNPJ com dígito verificador, datas).
  Tradutores por plataforma: Abler, Quickin, Lever.
- `aplicacao.py`: `ler_anuncio` grava `campos_estruturados["_leitura"]` na extração;
  `aplicar_leitura` aplica nos modelos antes da prontidão (em
  `integrations/empregos/preparacao.py`).
- `_diagnostico` vai no arquivo de cada payload (`scripts/preparar_fila_empregos.py`),
  **nunca** no payload da API.
- Decisões do usuário: **vaga sem nome de empresa exibido não é válida** (não herda o
  nome da conta/consultoria); `expireAt` nunca é calculado (Quickin manda +90 dias da
  própria plataforma → fica vazio); part time fica vazio por enquanto (a API aceita
  PART_TIME; mudar é uma linha, aguardando decisão); a página de detalhe da Abler
  **não** é coletada (10/10 testadas sem CNPJ/logo/CEP, 1,9 MB cada).
- Tabela de aceite na amostra de 150 vagas (`scripts/avaliar_leitura.py`, dados em
  `outputs/especificacao/`): `_diagnostico` 0→150; Abler salário/modalidade/vínculo
  100% quando existem; Quickin e Lever modalidade 100%; salário 0/0 8→0; `\n` literal
  20→0; Quickin expiração calculada 30→0; Gerente→DIRECTOR e Interno→INTERNSHIP 0.
- Duas revisões por subagente já foram feitas e corrigidas.

### 4.3 Workday
Triagem de 133 fontes sem vagas (`outputs/especificacao/triagem_fontes.md`):
Workday 45, JavaScript 16, listas HTML 14, sem vagas 12, Senior Portal 11, outros ATS
10, bloqueios/rede 17, URL não-carreiras 4. Implementado: detalhe pedido na API CXS
(`/wday/cxs/<tenant>/<site>/job/...`, em `crawling/adaptadores/empresa_direta.py`) e
extrator `extraction/workday.py`. Piloto: 46 de 49 fontes agora rendem vagas
(941 anúncios com limite de 20 por fonte). Filtro por país (Brasil) na consulta **não**
foi feito: vagas estrangeiras gastam o limite e só são descartadas depois.

### 4.4 Outras correções
Descrição com parágrafos/listas (`_limpar_html_formatado`), `javascript:` em
`url_candidatura` derrubava o alvo (Ceva Logistics perdia 444 anúncios), datas ISO sem
fuso (Essential), linha duplicada no catálogo (JACOBSDOUWEEGBERTS, removida do
`catalogo_fontes.csv` e `fontes_autorizadas.csv`), `lxml` explícito no `pyproject.toml`.

## 5. Medições e achados importantes
- **Neste notebook Windows o disco é o gargalo** (provavelmente antivírus): ~66–93
  arquivos/s; 11 processos de extração não foram mais rápidos que 1. As otimizações de
  CPU só podem ser avaliadas no Mac. Roteiro: `docs/roteiro_medicao_mac.md`.
- Extração: ~349 ms/página em média no dia real (11,4 mil páginas); páginas grandes
  (Randstad ~350 KB) chegam a ~1 s.
- Coleta é limitada pelo ritmo por site (~1 req/s por domínio). No lote de 1.000
  fontes, 91% das páginas vieram de 4–5 sites (`empregandobrasil.com.br` 48 mil,
  `emploive.com` 10 mil, `eu.dev.br`, `sportinsider.com.br`); uma coleta ficou
  ~16 h nesse site. 73.432 páginas coletadas (69.517 de detalhe) em 773 das 995 fontes.
- Com `--limite-anuncios 200` a estimativa é ~4.900 vagas de detalhe por rodada
  (9 de 312 fontes passam de 200 e somam 95% das páginas).
- Dedup entre fontes (`integrations/empregos/fila.py`) é por assinatura exata
  (título+empresa+endereço+descrição) e é instantânea; embeddings não acelerariam, só
  melhorariam a qualidade (pegariam variações). Não implementado.
- Fila de payloads: 343 s para 5.730 anúncios (causa não medida).
- Com blocos de 200 fontes por processo, o bloco seguinte só começa quando o site mais
  lento do atual termina (inefetiência conhecida).

## 6. Como rodar (PowerShell, na pasta do projeto, túnel SSH do Mongo aberto)
Registrar fontes (uma URL por linha em um .txt):
```powershell
.\.venv\Scripts\python.exe scripts\preparar_lote_urls_licenciadas.py --entrada C:\Users\Inspirion\Documents\urls_1000.txt --diretorio-saida config\lote_1000
```
(esse script marca todas as URLs como autorizadas para publicação; não confere licença.)

Rotina diária (incremental, com limite por fonte):
```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletar --catalogo config\lote_1000\catalogo_fontes.csv --confirmar --processos-coleta 3 --limite-anuncios 200 *> outputs\lote_diario.log
```
Reprocessar o que já está em disco, sem coletar (troque a data pelo início da coleta, UTC):
```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletado-desde 2026-10-01T19:00:00+00:00 --catalogo config\lote_1000\catalogo_fontes.csv --confirmar *> outputs\lote_1000_reprocesso.log
```
Payloads para revisão (não chama a API, não grava no Mongo):
```powershell
.\.venv\Scripts\python.exe scripts\preparar_fila_empregos.py --catalogo config\lote_1000\catalogo_fontes.csv --somente-catalogo --publicados-ontem --diretorio-payloads outputs\payloads\para_publicar
```
Dividir um catálogo em partes (mesmo domínio sempre na mesma parte):
```powershell
.\.venv\Scripts\python.exe scripts\dividir_catalogo.py --catalogo config\lote_1000\catalogo_fontes.csv --partes 4
```
No Mac: `python3 -m venv .venv`, `source .venv/bin/activate`,
`pip install -e ".[crawler,mongodb,dev]"`, copiar o `.env` (nunca pelo git),
`ulimit -n 10240`, `caffeinate -i` na frente de comandos longos, tirar `data/` do Spotlight.
Dica de log: o redirecionamento `*>` esconde a saída; o Python faz buffer e o log só
aparece em blocos. Acompanhar com `Get-Process python` e `Get-Content log -Wait`.

## 7. O que estava acontecendo agora
O usuário reprocessava o lote de 1.000 fontes (`--coletado-desde`, processo Python
37796, log `outputs/lote_1000_reprocesso.log`). Estava na fase de **leitura do
inventário** (invisível no log); depois vêm a extração (`Extraindo 995 alvo(s)...`) e a
gravação no Mongo. A coleta anterior foi interrompida por ser lenta (portais gigantes).
A resposta "quantos anúncios" só sai ao final (somar `Anúncios únicos` do log).
Nada foi publicado na API do Empregos.

## 8. Pendências e próximos passos (em ordem sugerida)
1. **Decisão do usuário:** enviar `09a1db4` e `dividir_catalogo.py` ao GitHub?
2. **Fazer:** permitir blocos maiores (`--alvos-por-coleta` hoje 1–200; subir para ~2.000)
   para os sites pequenos não esperarem os grandes; e **gravar no Mongo conforme cada
   processo de coleta termina** (F4). Isso foi proposto e aguarda um "pode".
3. **Decisão do usuário (autorização):** ~160 das 995 fontes são agregadores
   (`cargos.com.br` 78, `jobijoba.com.br` 72, `pt.linkedin.com` 11) e o script as marcou
   como autorizadas para publicação; o usuário precisa confirmar se tem autorização.
4. Rodar o roteiro do Mac e mandar as seções `## TEMPO POR FASE`.
5. Recuperar fontes sem vagas (ganho estimado até ~62% das 133): Senior Portal (11),
   extrator genérico de listas HTML (14; `listagem_html.py` é rascunho com falsos
   positivos), outros ATS (10), filtro de Brasil no Workday, JavaScript (16).
6. Melhorias de velocidade: ler cada página uma vez só (F6, ~2–3× na extração), balanceio
   dinâmico de filas de coleta (F5), política de retenção do `data/raw`.
7. Verificar o `encerrar_anuncios_expirados.py` para vagas que saem do ar (vagas não
   re-baixadas não são "revistas").
8. Conferência manual da especificação (5 vagas por plataforma) ainda não feita; a
   opção part time e o `--trabalhadores-posprocessamento` (hoje sem efeito) continuam abertos.
9. Rodar várias fontes/lotes em paralelo com `--confirmar` pode **duplicar empresas**
   (a resolução foi feita para rodar uma de cada vez; não testado). Preferir coleta
   paralela sem `--confirmar` e gravação única no fim.

## 9. Onde estão os arquivos
- Código novo: `src/observatorio_vagas/extraction/leitura/`, `workday.py`,
  `cache_paginas.py`; scripts `avaliar_leitura.py`, `comparar_extracao.py`
  (compara extração antes/depois, útil ao mexer em extratores), `dividir_catalogo.py`.
- Saídas de trabalho (ignoradas pelo git): `outputs/especificacao/` (amostra de 150,
  payloads antes/depois, triagem, fila), `outputs/medicao/` (benchmarks e logs).
- Testes: `tests/unit/` (leitura em `tests/unit/extraction/leitura/`).
- Preferências do usuário: respostas em português, curtas e diretas; explicar "como se
  fosse leigo" quando pedir; economizar tokens; confirmar antes de ações com efeito externo
  (git push, gravar no Mongo, publicar).
