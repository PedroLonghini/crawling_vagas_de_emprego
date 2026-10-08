# Contexto de Continuidade — Observatório de Vagas

> Documento criado em 16/09/2026 para que qualquer pessoa — ou uma nova conversa
> com o assistente — consiga continuar o projeto sem precisar reconstruir todo o
> histórico. Ele registra decisões e funcionamento; não é uma cópia literal do chat.

## 1. Objetivo principal

O projeto lê vagas públicas de fontes externas, guarda a evidência bruta da leitura,
transforma o conteúdo em dados organizados, armazena os dados no MongoDB e prepara
somente as vagas legalmente e tecnicamente aptas para a API do Empregos.

O sistema **não deve**:

- ler o Empregos como fonte de produção, pois ele é o destino de publicação;
- republicar uma vaga apenas porque ela é pública na internet;
- inventar campos que não aparecem na fonte;
- publicar concursos públicos, editais ou contratações de serviços;
- ignorar `robots.txt`, limites por domínio ou a política cadastrada para a fonte.

O objetivo de volume é alto (centenas ou milhares de vagas por dia), mas o volume
não pode superar autorização, qualidade de dados ou respeito aos sites.

## 2. Ideia em linguagem simples

Pense no sistema como uma esteira:

```text
Catálogo de fontes
        ↓
Crawler baixa listagens e páginas de vagas
        ↓
Armazenamento bruto guarda exatamente o que foi recebido
        ↓
Extratores encontram título, descrição, empresa, local, datas etc.
        ↓
MongoDB guarda anúncio, empresa, vaga canônica e histórico
        ↓
Validador confere política + data + campos da API do Empregos
        ↓
Payload JSON é preparado localmente
        ↓
Futuro cliente da API do Empregos publica somente os elegíveis
```

Cada etapa é separada de propósito. Se um extrator estiver errado, é possível
reprocessar o HTML/JSON bruto sem baixar o site novamente. Se a regra de publicação
mudar, é possível reavaliar o MongoDB sem repetir a coleta.

## 3. Estrutura das pastas

| Local | Responsabilidade |
|---|---|
| `config/catalogo_fontes.csv` | Único catálogo operacional. Possui somente a coluna `url`. |
| `data/raw/` | Corpos HTML/JSON/CSV baixados e seus metadados auditáveis. |
| `src/observatorio_vagas/crawling/` | Spider Scrapy, descoberta, paginação, política e armazenamento bruto. |
| `src/observatorio_vagas/extraction/` | Extratores, enriquecimento e conversão para o modelo interno. |
| `src/observatorio_vagas/domain/` | Regras de negócio e modelos: anúncio, empresa, vaga, política e prontidão. |
| `src/observatorio_vagas/storage/` | Repositórios MongoDB. |
| `src/observatorio_vagas/integrations/empregos/` | Geração de payload e futura integração com a API do Empregos. |
| `scripts/` | Comandos operacionais: coletar, processar, resolver empresas, criar vagas e avaliar. |
| `outputs/` | Relatórios, testes isolados e payloads preparados localmente. |
| `tests/unit/` | Testes automatizados do comportamento esperado. |
| `docs/` | Documentação do projeto, incluindo este arquivo. |

## 4. Conceitos importantes

### Fonte, alvo e política

- **Fonte**: tipo de origem, como página de carreira, API licenciada, CKAN ou Gupy.
- **Alvo**: uma linha do catálogo. Junta empresa, URL inicial, domínio, limite e política.
- **Política da fonte**: informa se a coleta é permitida e se a republicação é permitida.
- **Somente coleta**: o crawler pode guardar e analisar a vaga, mas ela jamais entra na fila
  de publicação enquanto a política estiver assim.
- **Aprovada**: significa que o catálogo possui uma evidência de licença/termo que permite a
  republicação dentro das condições registradas. Não é uma autorização automática para
  qualquer domínio ou site parecido.

Uma política sempre pertence a um domínio específico. A autorização de um site não pode ser
emprestada para outro.

### Resposta bruta

É a fotografia do que o crawler recebeu: URL solicitada, URL final, status HTTP, corpo,
data/hora, hash SHA-256, tipo da página e alvo. Ela fica em `data/raw/` antes de qualquer
interpretação. Isso permite auditoria e reprocessamento.

### Anúncio e vaga canônica

- **Anúncio**: observação de uma vaga em uma fonte, contendo texto e evidências originais.
- **Empresa**: organização associada ao anúncio. Pode ser resolvida/enriquecida depois.
- **Vaga canônica**: forma normalizada e estável da vaga de uma empresa; evita duplicar a
  mesma vaga em várias coletas.

### IDs

- `alvo_id`: nome configurado no CSV, por exemplo `nic_br_vagas`.
- `id_externo`: identificador que a própria fonte disponibiliza, ou um valor determinístico
  derivado da URL quando não houver identificador explícito.
- `anuncio_id`: UUID criado para a observação persistida no MongoDB.
- `vaga_id`: UUID determinístico associado à empresa e à identidade normalizada da vaga.
- hash SHA-256: identifica o conteúdo bruto, não é o ID da vaga.

## 5. Fluxo detalhado da coleta

1. O Scrapy lê o catálogo e só inicia alvos ativos cuja política permite coleta.
2. A fábrica de requisições valida domínio, esquema HTTP/HTTPS, política, limite e bloqueios.
3. A página inicial é classificada como **listagem**.
4. O crawler descobre URLs de detalhes usando, nesta ordem aproximada:
   - JSON-LD `JobPosting`;
   - estado público de JavaScript/Next/Vue já presente no HTML;
   - atributos públicos de cards, como `data-job-url` e `data-detail-url`;
   - `onclick` literal como `router.push('/jobs/123')`;
   - links HTML usuais;
   - sitemap, quando aplicável;
   - adaptadores específicos para plataformas conhecidas.
5. A paginação reconhece `rel=next`, números dentro de controles de paginação, botões com URL
   explícita e links “carregar mais”. Nunca inventa uma URL baseada somente em `data-page=2`.
6. O crawler salva listagens e detalhes em `data/raw/`.
7. Falhas HTTP, páginas sem vagas e links que não puderam ser agendados aparecem no relatório
   de cobertura; uma fonte com erro não interrompe as outras.

### Limites

- `limite_paginas` no catálogo: número de **listagens** permitido por alvo.
- `--limite-anuncios`: orçamento separado de **detalhes de vagas** por fonte.
- `--limite` em `processar_lote.py`: máximo de anúncios que serão processados após a coleta.

Para leituras maiores, use `--limite-anuncios`. Sem esse argumento, o comportamento legado
compartilha o orçamento de páginas entre listagens e detalhes, o que pode reduzir o total de
vagas lidas.

O crawler mantém baixa pressão: por padrão há uma requisição simultânea por domínio, atraso,
AutoThrottle, tentativas apenas para erros temporários e respeito a `robots.txt`.

## 6. Melhorias de leitura já implementadas

Em 16/09/2026 foram aplicadas estas melhorias gerais:

1. URLs escondidas no estado JavaScript agora entendem escapes como `\u002F` e `\u003A`.
2. Cards sem `<a>` podem ser reconhecidos por `data-job-url`, `data-job-detail-url`,
   `data-detail-url` e `data-posting-url`.
3. Rotas literais em `location.assign`, `location.replace`, `router.push` e `router.replace`
   são tratadas como candidatos, sem executar JavaScript.
4. Paginação reconhece `data-next-page-url`, `data-load-more-url`, `data-pagination-url` e
   rótulos acessíveis como “Página 2”.
5. Quando há `--limite-anuncios`, páginas irmãs já mostradas no controle de paginação podem ser
   colocadas na fila juntas. A concorrência por domínio continua limitada.
6. O extrator HTML genérico entende pares estruturados em `dt/dd`, `th/td` e `data-label`.
7. Ele consegue extrair local, modalidade, contrato, senioridade, salário e prazo mesmo quando
   o site não usa `Local: valor` com dois-pontos.
8. Datas explícitas nos formatos ISO, `dd/mm/aaaa` e `14 de setembro de 2026` são convertidas
   para ISO.
9. O relatório de cobertura passou a informar listagens/detalhes agendados, itens sem resposta
   e os mecanismos que encontraram os cards.
10. O leitor avulso normaliza URLs com caracteres acentuados antes de baixá-las.
11. Paginação em botões `onclick` que carregam uma URL literal (`loadMore`, `fetch` e
    equivalentes) é reconhecida sem executar JavaScript nem inventar parâmetros.

Essas melhorias ampliam descoberta e preenchimento, mas continuam conservadoras: um campo só
é salvo quando há valor explícito no HTML/JSON da própria vaga.

## 7. Extração e os 24 campos da API do Empregos

O relatório de prontidão avalia 24 campos:

```text
company.applyUrl
company.name
company.logoUrl
company.description
company.industries
company.companyId
company.recruiterId
company.recruiterName
company.recruiterEmail
company.nationalRegister
externalJobPostingId
jobPostingOperationType
title
description
location.address
location.postalCode
location.geolocation
salary.min
salary.max
workplaceTypes
employmentStatus
experienceLevel
trackingPixelUrl
expireAt
```

Campos obrigatórios atuais para criar o payload são:

```text
company.name
externalJobPostingId
title
description
location.address
```

Os demais são opcionais; sua ausência cria alerta, não bloqueio. Uma vaga não precisa ter
24/24 campos para ser publicável, mas precisa ter todos os obrigatórios válidos, política de
republicação permitida e data de expiração ainda válida.

## 8. Filtros que bloqueiam publicação

Uma vaga pode estar bloqueada por:

- fonte sem permissão de republicação;
- domínio da URL diferente do domínio autorizado no alvo;
- vaga expirada;
- empresa ainda não associada;
- URL da vaga/fonte ou outro campo obrigatório ausente. CNPJ e descrição institucional são
  opcionais; o link direto de candidatura, quando existir, tem preferência sobre a URL de
  origem;
- conteúdo classificado como concurso público, edital ou contratação de serviço.

Concursos públicos não fazem parte do produto e são descartados antes da extração/publicação.

## 9. Fontes e decisões conhecidas

- **Gupy** está bloqueada na coleta e na publicação (`gupy.io` em
  `domain/politica_fonte.py`, decisão de 06/10/2026). **Adzuna, Pandapé e portais
  similares** podem ser tecnicamente coletáveis, mas não devem ser marcados como
  republicáveis sem licença/termo documentado.
- **Empregos.com.br** é o destino do produto, não fonte de produção. Ele pode ser usado como
  página controlada apenas em teste manual isolado; não deve entrar no catálogo operacional.
- **NIC.br** possui adaptador dedicado (`extraction/nic_br.py`) e foi usado em teste isolado
  com sete vagas encontradas. Cinco ficaram localmente elegíveis e duas foram bloqueadas por
  expiração. O adaptador extrai CNPJ, descrição institucional, cidade, modalidade/contrato
  quando explícitos e prazo.
- Fontes novas só entram ativas no catálogo depois de registrar domínio, política, licença,
  URL da evidência e comportamento de extração.

## 10. Comandos de trabalho mais usados

### Ver fontes ativas

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py --listar
```

### Testar uma única fonte sem MongoDB e sem API

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py `
  --catalogo config\catalogo_fontes.csv `
  --alvo-id nic_br_vagas `
  --paginas 10 `
  --anuncios 100
```

O resultado é salvo em `outputs/testes_fontes/.../`. Veja `resumo.json`, `cobertura.json`,
`anuncios.json` e a pasta `raw/`.

### Coletar e processar várias fontes

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py `
  --catalogo config\catalogo_fontes.csv `
  --coletar `
  --alvos-por-coleta 10 `
  --limite-anuncios 100 `
  --limite 1000
```

Acrescente `--confirmar` somente quando desejar gravar anúncios, empresas e vagas no MongoDB.
Esse comando não publica na API do Empregos.

### Ver prontidão de uma vaga já persistida

```powershell
.\.venv\Scripts\python.exe scripts\diagnosticar_vaga_empregos.py `
  --catalogo config\catalogo_fontes.csv `
  --anuncio-id UUID_DO_ANUNCIO `
  --vaga-id UUID_DA_VAGA
```

### Gerar relatório de candidatos para revisão

```powershell
.\.venv\Scripts\python.exe scripts\preparar_fila_empregos.py `
  --catalogo config\catalogo_fontes.csv `
  --somente-catalogo `
  --limite 1000 `
  --saida-json outputs\aptidao\atual.json
```

### Testar uma URL fora do catálogo, sem MongoDB

```powershell
.\.venv\Scripts\python.exe scripts\ler_url.py "https://exemplo.com/vagas" --limite 50
```

Use `--salvar-raw --alvo-id teste_controlado` apenas quando quiser preservar o conteúdo bruto
do teste. Essa ferramenta não transforma a URL em fonte autorizada.

## 11. MongoDB e ambiente

Durante o desenvolvimento o MongoDB foi usado por túnel SSH para uma VM. No futuro ele deve
ir para um servidor próprio. A URI fica nas configurações/variáveis locais, nunca neste
documento e nunca em arquivos versionados.

Antes de executar etapas que gravam no MongoDB, valide a conexão com o comando já configurado
no ambiente. Não misture dados de teste com o catálogo operacional sem `alvo_id` diferente.

## 12. Estado atual e próximos passos recomendados

O pipeline base está funcionando: coleta, armazenamento bruto, extração, resolução de empresa,
criação de vaga canônica, prontidão e fila local de payloads.

Prioridades seguras:

1. Rodar testes isolados nas fontes aprovadas e comparar `candidatos_unicos`,
   `detalhes_http_ok`, `anuncios_unicos` e campos preenchidos.
2. Criar adaptadores específicos apenas para fontes que tenham licença de republicação clara e
   que apresentem muitas vagas reais.
3. Melhorar adaptadores para páginas que ainda retornem muitos detalhes sem descrição.
4. Enriquecer empresa por fontes próprias/licenciadas para obter CNPJ e descrição institucional.
5. Configurar o cliente real da API do Empregos com credenciais fora do repositório, idempotência,
   logs e modo de simulação.
6. Manter rotina diária: coletar → extrair → comparar com observações anteriores → preparar fila
   → revisão/publicação apenas dos elegíveis.

## 13. Regras para alterações futuras

- Sempre escrever ou ajustar testes antes/depois de mudar descoberta, paginação ou extração.
- Não alterar a política para “aprovada” sem evidência documentada da licença/termo.
- Não apagar dados brutos para “limpar” sem plano de retenção aprovado.
- Não executar publicação real apenas porque um payload foi gerado localmente.
- Ao adicionar uma URL em `config/catalogo_fontes.csv`, não criar ID, política
  ou lista paralela: o crawler gera a configuração de coleta automaticamente.
- Preferir adaptadores pequenos por tipo/plataforma a uma regra genérica perigosa que possa
  coletar páginas erradas.
- Medir sempre: páginas recebidas, candidatos, detalhes HTTP 2xx, anúncios extraídos, campos
  obrigatórios completos, bloqueios e duplicados.

## 14. Validação mais recente

Em 16/09/2026, após as melhorias gerais de leitura:

```text
ruff check src/observatorio_vagas/crawling src/observatorio_vagas/extraction scripts tests/unit/crawling tests/unit/extraction
→ aprovado

testes de crawling, paginação, adaptadores, fábrica de requisições e extrator HTML
→ 100 testes aprovados
```

Há um aviso não bloqueante do pytest sobre a opção `cache_dir` no `pyproject.toml`; ele não
interfere na execução dos testes.
