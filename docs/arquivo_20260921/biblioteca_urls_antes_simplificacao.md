# Biblioteca de URLs do crawler

O arquivo canônico das fontes é `config/catalogo_fontes.csv`. Ele fica fora de
`data/` porque essa pasta armazena respostas brutas e está ignorada pelo Git.

Cada linha representa um ponto inicial independente. Uma linha inválida ou um
site indisponível é relatado e isolado; os demais alvos continuam no lote.

## Cadastro simplificado: somente URLs

Para cadastrar muitas fontes sem editar as onze colunas do CSV, cole uma URL
por linha em `config/urls_fontes.txt` e execute:

```powershell
.\.venv\Scripts\python.exe scripts\importar_urls_fontes.py
```

O importador reconhece plataformas conhecidas, cria um identificador estável,
remove fragmentos, padroniza domínio e booleanos e ignora URLs repetidas. Cada
fonte nova recebe o limite conservador de dez páginas. Uma
linha inválida é relatada sem impedir a importação das demais.

A URL não prova autorização. Por isso, toda fonte nova fica `ativa=false`, com
política `pendente` e `republicacao_permitida=false`. Um domínio da lista
central de proibições entra como `bloqueada`. As decisões já existentes sobre
licenças e republicação nunca são substituídas por uma nova importação.

Para conferir sem alterar o CSV, use `--simular`. Depois da revisão jurídica e
técnica, a fonte pode ser ativada manualmente no catálogo adequado.

## Catálogo operacional republicável

O arquivo `config/catalogo_fontes_republicaveis.csv` é um espelho do catálogo
canônico. Ele contém somente fontes que atendem simultaneamente a três condições:
licença aberta verificável, coleta implementada e republicação permitida. Após a
limpeza de 16 de setembro de 2026, são **522 alvos**: 510 buscas municipais no
Querido Diário, 3 conjuntos CKAN públicos e 9 páginas institucionais licenciadas.
As URLs municipais não
significam que cada município terá vagas novas todos os dias. Gupy, Pandapé e
páginas pendentes não entram no catálogo operacional.

Os municípios disponíveis mudam com o tempo. O comando abaixo consulta
`/cities`, cria uma busca combinada por município com ID IBGE estável e só
substitui os arquivos depois da validação. Também alinha os catálogos diário,
central, Querido Diário e `urls_fontes.txt`. Há backup em `outputs/backup_catalogos`:

```powershell
.\.venv\Scripts\python.exe scripts\sincronizar_catalogo_republicavel.py --por-municipio --sincronizar-biblioteca
```

Cada coleta do Querido Diário recebe automaticamente uma janela móvel dos sete
dias anteriores. A paginação solicita uma página por vez até esgotar os
resultados ou chegar ao limite de dez páginas. Cada município tem uma única
busca combinada. O catálogo canônico não mantém fontes inativas; candidatos e
histórico ficam fora da rotina de coleta. A descrição extraída conserva atribuição
visível ao Querido Diário/Open Knowledge Brasil, ao diário municipal, à licença
CC BY 4.0 e ao documento original.

CKAN agora aceita a URL normal da página do conjunto e segue os downloads CSV.
Isso permite usar os caminhos públicos do ES/UFVJM sem acessar `/api/`, que
seus `robots.txt` bloqueiam. O recurso SETADES pode redirecionar para o
armazenamento oficial `one.s3.es.gov.br`; o código exige o mesmo dataset,
UUID e nome do CSV. Outros destinos continuam bloqueados.

PBH reutiliza o conversor CSV e a trava temporal existente; arquivos trimestrais
antigos podem produzir zero anúncios vigentes. UFVJM só gera oportunidades
quando a situação informa **inscrições abertas**: concurso “em andamento” ou
“válido até” pode já estar homologado e não aceitar candidatos.

Evidências oficiais consultadas em 08/09/2026:

- [Querido Diário: licença CC BY 4.0](https://docs.queridodiario.ok.org.br/pt-br/latest/),
  com ressalva de materiais que indiquem licença específica.
- [API pública: referência de 60 requisições/minuto](https://docs.queridodiario.ok.org.br/pt-br/latest/utilizando/api-publica.html).
  O crawler usa concorrência 1 e intervalo mínimo de 1,1 s nesse domínio.
- [Conjunto SETADES/ES e seus recursos](https://dados.es.gov.br/dataset/c43d6653-634a-4e6e-bd66-c9ca3f6ee106).
- [Vagas PBH: licença do conjunto](https://dados.pbh.gov.br/pt_BR/dataset/vagas-ofertadas-pbh).
- [Vagas de estágio e emprego do IFTM](https://dadosabertos.iftm.edu.br/dataset/relacao-de-vagas-de-estagio-e-emprego).

Não foi incluído o NIC.br: apesar do rodapé CC BY-SA, seus
[termos específicos restringem uso comercial](https://nic.br/politica-de-privacidade-e-termos-de-uso/).
Não presumimos que um site público ou governamental autoriza qualquer republicação.

Para testar toda a biblioteca sem gravar no MongoDB:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py `
  --catalogo config\catalogo_fontes_republicaveis.csv `
  --coletar --alvos-por-coleta 10 --limite 1000
```

Depois de revisar a simulação, acrescente `--confirmar` para gravar anúncios,
empresas e vagas canônicas. Isso ainda não envia nada para a API do Empregos;
os campos obrigatórios, a vigência e a elegibilidade continuam sendo avaliados
separadamente antes de qualquer publicação.

## Colunas

- `alvo_id`: identidade estável e única da configuração. Depois da primeira
  coleta, não deve ser renomeada.
- `empresa_nome`: nome usado nos relatórios e como evidência inicial.
- `fonte`: escolhe o adaptador. Use `ckan` para conjuntos oficiais com recursos CSV,
  `pagina_carreiras` para páginas próprias e `outra` quando a plataforma ainda
  não for conhecida. Gupy não é uma fonte autorizada para este projeto e não
  deve ser cadastrada como `gupy`.
- `url_inicial`: URL pública completa, usando HTTP ou HTTPS.
- `ativa`: `true` executa o alvo; `false` o mantém catalogado sem acessá-lo.
- `limite_paginas`: orçamento máximo da página inicial mais detalhes. O valor
  protege contra navegação ilimitada.
- `status_politica`: autorização independente da tecnologia da página.
- `licenca_nome`: nome da licença aberta comprovada, como `CC BY 4.0`.
- `licenca_url`: página oficial em que a licença pode ser conferida.
- `atribuicao_obrigatoria`: `true` quando a origem precisa ser citada.
- `republicacao_permitida`: confirmação explícita de que a licença permite
  republicar os dados.

## Estados de política

- `pendente`: não coleta e não publica.
- `somente_coleta`: pode coletar para validação interna, mas nunca republica.
- `aprovada`: a fonte foi revisada; a publicação ainda exige licença completa
  e `republicacao_permitida=true`.
- `bloqueada`: não acessa a fonte.
- `desativada`: desligamento operacional temporário.

Uma fonte nova deve começar como `pendente`. Use `somente_coleta` apenas após
confirmar que a coleta interna é permitida. Use `aprovada` somente quando houver
base jurídica ou autorização documentada para republicar. O site Empregos não
deve ser incluído: ele é destino futuro dos dados, nunca fonte do crawler.

O texto `aprovada` sozinho não libera mais uma fonte. A publicação somente é
habilitada quando `licenca_nome` e `licenca_url` estão preenchidos e
`republicacao_permitida` é `true`. Catálogos antigos continuam legíveis, mas
uma linha antiga sem essas provas fica automaticamente sem permissão de
publicação.

## Domínios proibidos antes da rede

Existe uma lista defensiva central no código. Ela é aplicada no modelo de
política, na fábrica de requisições e no middleware anterior ao download. Assim,
uma configuração incorreta ou um adaptador novo não consegue acessar estes
domínios nem seus subdomínios:

| Domínio | Motivo operacional |
| --- | --- |
| `empregos.com.br` | É o destino futuro dos dados, nunca uma fonte. |
| `indeed.com` e `indeed.com.br` | A coleta automatizada exige autorização expressa por escrito. |
| `infojobs.com.br` | Os termos públicos proíbem o uso de robot ou crawler. |
| `catho.com.br` | A fonte não autoriza coleta por crawler para este projeto. |

Referências oficiais: [termos do Indeed](https://br.indeed.com/legal?hl=pt),
[aviso legal do InfoJobs](https://www.infojobs.com.br/legal/aviso-legal-para-empresas__15726.aspx)
e [portal de integração da Catho](https://desenvolvedores.catho.com.br/developers-integration-portal).

Se uma dessas URLs for incluída em um catálogo grande com uma política que
tentaria habilitar a coleta, somente aquela linha será rejeitada e explicada; os
outros alvos continuarão. Um registro de auditoria pode permanecer no CSV apenas
com `status_politica=bloqueada`. A liberação futura exige revisão formal e uma
alteração explícita da lista central, não apenas a edição do CSV.

## Exemplos de linhas

Página própria ainda sem adaptador específico:

```csv
empresa_carreiras,Empresa Exemplo,pagina_carreiras,https://empresa.example/carreiras,true,20,pendente,,,false,false
```

Conjunto de dados com licença aberta comprovada:

```csv
pbh_sine,PBH SINE,outra,https://dados.pbh.gov.br/pt_BR/dataset/vagas-ofertadas-pbh,false,1,aprovada,CC BY 4.0,https://creativecommons.org/licenses/by/4.0/deed.pt-br,true,true
```

## Fontes públicas licenciadas cadastradas

O catálogo central e `config/catalogo_fontes_diarias.csv` são espelhos da
rotina diária: somente fontes de vagas, ativas, com licença aberta compatível e
extrator já disponível.

| Alvo | Conteúdo | Base de reutilização | Integração necessária |
| --- | --- | --- | --- |
| `es_setades_vagas_agencias` | Agências do Trabalhador do ES | Creative Commons Attribution | CKAN integrado; `robots.txt` pode impedir a coleta |
| `iftm_vagas_estagio_emprego` | Vagas de estágio e emprego do IFTM | Creative Commons Attribution | CKAN integrado; só vigências abertas |
| `pbh_sine_vagas_abertas` | Vagas ofertadas pela PBH | Creative Commons Attribution | Importador CSV próprio |
| `querido_diario_ibge_*_oportunidades` | Vagas, SINE, estágios e aprendizagem em diários municipais | CC BY 4.0 | API integrada; até dez páginas por execução |

Esses registros autorizam a reutilização da fonte, não a publicação automática
de qualquer documento encontrado. O conteúdo ainda precisa representar uma
oportunidade vigente, conter os campos obrigatórios do Empregos e passar pelas
travas de elegibilidade.

Permissões textuais sem licença e licenças NC/ND não fazem parte dos catálogos
operacionais nem da lista organizada de URLs.

## Execução em lote

O catálogo central já é o padrão. Esta prévia coleta as páginas e prepara os
resultados, mas não grava anúncios, empresas ou vagas no MongoDB:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletar
```

Para limitar o impacto de um catálogo grande, o orquestrador divide os alvos em
grupos. O tamanho pode ser ajustado sem alterar o CSV:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletar --alvos-por-coleta 25
```

Acrescentar `--confirmar` autoriza gravações no MongoDB. Essa opção não autoriza
e não executa envio para a API do Empregos.

## Adaptadores

O adaptador é escolhido pela coluna `fonte`:

- `pandape`: reconhece os cartões públicos `/Detail/<id>`, normaliza parâmetros
  de rastreamento e usa a descoberta HTML genérica como fallback. As páginas
  individuais já publicam JSON-LD `JobPosting`, consumido pelo extrator comum.
  O adaptador está pronto para uma integração oficialmente autorizada, mas os
  domínios públicos continuam bloqueados porque os termos do InfoJobs proíbem
  robôs e crawlers.
- `querido_diario`: lê e pagina o JSON de `/gazettes`, remove a marcação dos
  excertos e conserva somente menções que combinem seleção pública com vaga,
  cargo ou inscrição. Não segue os arquivos externos retornados pela API.
- `ckan`: lê `package_show`, segue apenas recursos CSV ativos e, quando o
  conjunto mantém um arquivo por ano, escolhe somente o ano mais recente. Os
  CSVs do ES e da UFPE têm conversores separados e auditáveis.
- demais fontes: usam o adaptador HTML genérico até existir uma integração de
  plataforma.

O adaptador apenas descobre candidatos. A fábrica de requisições continua
responsável por domínio autorizado, lista central de proibições, política,
deduplicação e limite de páginas. A elegibilidade de publicação é avaliada mais
tarde.

## Teste do Querido Diário

A linha central respeita o limite configurado por alvo e consulta páginas de cinquenta diários recentes que
mencionem processo seletivo, concurso público ou contratação temporária. Isso
permite analisar até mil diários por execução, respeitando o mesmo domínio
e os limites de requisição. Esta execução coleta a API e prepara os anúncios em
modo de simulação:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py `
  --catalogo config\catalogo_querido_diario.csv `
  --coletar --alvos-por-coleta 1
```

O extrator não inventa cargo, CNPJ, prazo nem link de candidatura. Quando o
excerto não informa um cargo com segurança, usa um título genérico de processo
seletivo. A ausência dos campos obrigatórios continua aparecendo no diagnóstico
e bloqueia a publicação automática até existir evidência suficiente.

O endpoint atual é `https://api.queridodiario.org.br/gazettes`. O endereço
antigo sob `api.queridodiario.ok.org.br` não deve ser usado: o próprio portal
oficial redireciona sua documentação para o novo domínio.

## Coleta diária em grande volume

O catálogo diário reúne somente as fontes licenciadas. O comando abaixo coleta
todas elas, grava ou reutiliza os registros no MongoDB e continua para a próxima
quando uma delas falha:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py `
  --catalogo config\catalogo_fontes_diarias.csv `
  --coletar --alvos-por-coleta 3 --limite 1000 --confirmar
```

Os IDs externos são determinísticos. Ler a mesma vaga no dia seguinte não cria
uma cópia: o repositório reutiliza a vaga conhecida e registra alterações. A
UFPE também exige que a data atual esteja entre o início e o fim das inscrições.
O crawler obedece `robots.txt`; uma fonte recusada é relatada e as demais seguem.

O relatório restrito a essas fontes é gerado sem chamadas HTTP ao Empregos:

```powershell
.\.venv\Scripts\python.exe scripts\preparar_fila_empregos.py `
  --catalogo config\catalogo_fontes_diarias.csv `
  --somente-catalogo --limite 1000 `
  --saida-json outputs\aptidao_diaria.json `
  --diretorio-payloads outputs\preparacao_empregos
```

O diretório de preparação recebe um subdiretório novo para cada execução, com
um `manifesto.json`, um JSON para cada vaga elegível e `payloads_unificados.json`.
Este último contém uma lista com todos os corpos de payload do lote. Esses arquivos
são a caixa de saída para revisão humana e futura integração: não executam POSTs e
não alteram o MongoDB.
