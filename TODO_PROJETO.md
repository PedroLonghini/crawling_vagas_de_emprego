# TODO — Crawling de vagas de emprego

Este arquivo é o plano de trabalho único do projeto. Ele separa o que já está
pronto, o que precisa ser validado em coletas reais e o que deve ser feito para
o objetivo de longo prazo: aumentar diariamente a quantidade de vagas
brasileiras com candidatura válida e aptas à publicação no Empregos.

## Objetivo do produto

Coletar páginas de carreira de empresas e consultorias brasileiras, preservar a
URL de candidatura, transformar os dados em anúncios canônicos no MongoDB e
gerar payloads JSON revisáveis para a API do Empregos.

Não é objetivo:

- publicar automaticamente sem revisão e confirmação;
- burlar login, CAPTCHA, WAF, `robots.txt` ou limite de sites;
- seguir domínios bloqueados (lista vigente em `domain/politica_fonte.py`: Gupy,
  Catho, Indeed, InfoJobs/Pandapé, Vagas.com, empregandobrasil etc.) ou fontes com proibição
  explícita de republicação;
- publicar concursos públicos ou vagas fora do Brasil.

## Situação atual

- [x] Catálogo simplificado por URL em `config/catalogo_fontes.csv`.
- [x] Lista lateral de fontes aprovadas em `config/fontes_autorizadas.csv`.
- [x] Coleta incremental com histórico, isolamento de falhas, limites e
  relatórios por fonte.
- [x] Persistência de resposta bruta em `data/raw/` e anúncios no MongoDB.
- [x] Normalização de empresa, título, local, descrição e URL de candidatura.
- [x] Filtro de vagas fora do Brasil e de conteúdos não empregatícios.
- [x] Geração de fila e payload JSON local para o Empregos.
- [x] Separação de payloads individuais, unificados e relatórios de bloqueio.
- [x] Dashboard e relatórios de cobertura, velocidade e anúncios por fonte.
- [x] Adaptadores para Abler, Lever, Sólides, Senior, SmartRecruiters,
  Randstad, CSOD/Bradesco, Empregare/Sicoob, portal LG/INGOH e Workday.
- [x] Testes unitários para adaptadores, política, extração, MongoDB e payload.

## Prioridade 0 — validar a leva recém-adicionada

Estas são as tarefas que vêm antes de ampliar novamente o catálogo.

- [ ] Rodar uma coleta de teste somente para as fontes adicionadas na última
  prospecção.
- [ ] Confirmar quantas vagas cada nova fonte entrega antes da deduplicação.
- [ ] Confirmar quantas permanecem elegíveis após filtros de Brasil, URL de
  candidatura, descrição, duplicidade e política.
- [ ] Conferir se a URL `company.applyUrl` leva a uma candidatura real da vaga
  ou ao portal correto da empresa.
- [ ] Verificar manualmente uma amostra de 10 payloads por adaptador novo:
  Workday, LG/INGOH, CSOD/Bradesco e Empregare/Sicoob.
- [ ] Registrar fontes que retornem zero vagas, bloqueio técnico ou HTML sem
  cards no relatório de prospecção.
- [ ] Corrigir somente os adaptadores que falharem em coleta real; não criar
  regras genéricas para resolver um caso isolado.

### Fontes novas para validar

| Grupo | Fontes | Leitor esperado |
| --- | --- | --- |
| Abler | Racional, Liga+, Vincular, Vendor Force, CCCR, Go Winners | Adaptador Abler |
| Quickin | BM Energia, IDG, Qintess, Winnin, Inspiração RH | Genérico, com possível adaptador Quickin se necessário |
| Página própria | Enterprise Logistics, SX Lighting, Pestana Leilões | Genérico |
| Portal LG | INGOH | Adaptador LG |
| Workday | Renault, Mosaic, Alcoa, Kimberly-Clark, Air Liquide, Sandoz, Dell, Concentrix | Adaptador Workday |

## Prioridade 1 — qualidade de anúncios e payloads

- [ ] Criar um relatório diário com total: lido, extraído, elegível, bloqueado,
  duplicado, sem candidatura e sem local brasileiro.
- [ ] Exibir a razão de descarte por anúncio no relatório JSON e no dashboard.
- [ ] Verificar se títulos genéricos como “Banco de talentos” devem ser
  publicados ou ficar em uma fila específica.
- [ ] Melhorar a identificação de cidade, estado e modalidade remota/híbrida.
- [ ] Melhorar o preenchimento de setor/indústria da empresa.
- [ ] Manter `company.applyUrl` obrigatório e usar sempre a melhor URL de
  candidatura disponível.
- [ ] Ampliar a captura de “Sobre a empresa” nos títulos, cards e seções da
  página de vaga, sem inventar descrição quando ela não existir.
- [ ] Adicionar validação de URL de candidatura quebrada antes de gerar
  payload, respeitando limites e sem testar logins.
- [ ] Criar uma amostragem automática de payloads para revisão humana antes da
  primeira publicação de cada fonte nova.

## Prioridade 2 — fontes e volume diário

Meta intermediária: aumentar fontes de alto giro sem perder qualidade ou
repetir vagas.

- [ ] Medir a contribuição diária de cada fonte durante pelo menos 14 dias.
- [ ] Priorizar fontes que tenham vagas novas recorrentes, não apenas muitos
  anúncios antigos.
- [ ] Manter uma fila de prospecção em `config/fila_prospeccao_fontes.csv` com:
  URL, empresa, setor, plataforma, volume visto, termos consultados, resultado
  técnico e decisão.
- [ ] Pesquisar continuamente empresas de logística, saúde, varejo, indústria,
  call center, tecnologia, energia e consultorias de RH.
- [ ] Priorizar páginas corporativas, redes e grupos com presença nacional.
- [ ] Registrar e testar novos locatários Abler, Sólides, Senior, Quickin,
  SmartRecruiters e Workday quando não houver restrição explícita.
- [ ] Tratar consultorias como fontes de possível duplicidade alta e comparar
  título, empresa, cidade, descrição e URL antes de publicar.
- [ ] Não adicionar URL que redirecione para plataforma bloqueada.
- [ ] Não descartar uma fonte apenas porque os termos não falam de
  republicação; registrar a ausência de proibição e manter a autorização da
  empresa como requisito operacional do projeto.

### Metas de medição

- [ ] Definir baseline diário de vagas novas elegíveis.
- [ ] Definir percentual máximo aceitável de duplicidade por fonte.
- [ ] Definir percentual mínimo de vagas com URL de candidatura válida.
- [ ] Acompanhar fontes que rendem zero vagas por sete coletas consecutivas.
- [ ] Desativar ou colocar em revisão fontes sem retorno consistente, sem apagar
  seu histórico.
- [ ] Produzir ranking semanal de fontes por vagas novas, elegíveis e taxa de
  falha.

## Prioridade 3 — adaptadores e extração

- [ ] Validar em produção o adaptador Workday para todos os locatários
  cadastrados; manter somente a consulta pública CXS e sua paginação indicada.
- [ ] Criar adaptador Quickin somente se o genérico falhar em fontes reais.
- [ ] Criar adaptador para páginas próprias que tenham cards sem links,
  endpoints JSON públicos declarados ou paginação não reconhecida.
- [ ] Manter adaptadores específicos pequenos, com escopo de domínio/empresa
  restrito e testes de regressão.
- [ ] Não inferir IDs de vaga, rotas privadas ou endpoints administrativos.
- [ ] Tratar listas e páginas de detalhes como etapas diferentes para não
  transformar página institucional ou formulário de currículo em vaga.
- [ ] Adicionar fixtures públicas desidentificadas para toda plataforma nova.
- [ ] Criar teste de contrato para cada adaptador: listagem, detalhe,
  paginação, URLs externas e item inválido.
- [ ] Revisar adaptadores que gerarem muitas páginas lidas com poucas vagas.

## Prioridade 4 — desempenho e estabilidade do crawling

- [ ] Medir duração total, páginas por minuto e vagas por minuto por execução.
- [ ] Identificar os domínios que mais consomem tempo e separar lentos dos
  rápidos.
- [ ] Usar filas separadas para fontes rápidas, lentas e com falhas recentes.
- [ ] Ajustar concorrência por domínio, sem exceder limites razoáveis do site.
- [ ] Manter circuit breaker para interromper temporariamente fontes que
  estejam falhando repetidamente.
- [ ] Usar limites adaptativos de páginas por fonte com base no histórico.
- [ ] Manter três processos paralelos somente quando CPU, RAM e rede estiverem
  disponíveis; cada processo deve receber subconjunto distinto de fontes.
- [ ] Garantir que processos paralelos salvem em `data/raw/` sem sobrescrever
  resultados e que a gravação no MongoDB ocorra depois em uma etapa única.
- [ ] Evitar reler detalhes que já estejam inalterados quando a fonte oferecer
  data de publicação ou atualização confiável.
- [ ] Investigar fontes com timeout, erro HTTP ou código de saída 4 usando o
  log da fonte, sem tentar contornar proteções.

## Prioridade 5 — dados, MongoDB e ciclo de vida

- [ ] Garantir índices do MongoDB para ID externo, URL canônica, fonte, data de
  coleta e status de publicação.
- [ ] Revisar o deduplicador para unir a mesma vaga vista em mais de uma fonte
  sem apagar a melhor URL de candidatura.
- [ ] Registrar fonte principal e fontes alternativas de uma vaga deduplicada.
- [ ] Definir regra de expiração: encerrar vaga que desapareceu em várias
  coletas consecutivas, sem apagar o histórico.
- [ ] Gerar relatório de vagas abertas, expiradas, atualizadas e novas por dia.
- [ ] Detectar mudança significativa de descrição, título, local ou URL de
  candidatura para republicar somente quando necessário.
- [ ] Fazer backup periódico do MongoDB e testar restauração em banco local.
- [ ] Documentar a URI via túnel SSH e o procedimento de conexão sem expor
  senha ou dados sensíveis em arquivos versionados.

## Prioridade 6 — publicação no Empregos

- [ ] Confirmar o contrato definitivo dos campos da API do Empregos.
- [ ] Manter `company.applyUrl` como campo obrigatório e não depender de CNPJ.
- [ ] Validar lote pequeno de teste antes do primeiro lote grande.
- [ ] Usar simulação e relatório de validação antes de chamadas reais.
- [ ] Implementar publicação idempotente por `externalJobPostingId`.
- [ ] Registrar resposta da API, horário, lote e erro de cada tentativa.
- [ ] Reenviar somente itens que falharam de modo recuperável.
- [ ] Separar criação, atualização e encerramento de vagas na fila.
- [ ] Não publicar itens bloqueados, incompletos, duplicados ou fora do Brasil.
- [ ] Criar tela/relatório de aprovação final: quantidade de payloads, fontes,
  erros e URLs de candidatura.

## Prioridade 7 — operação diária simples

- [ ] Consolidar em um guia curto os comandos de: coletar, gravar, verificar
  quantidade por fonte, gerar payload e publicar teste.
- [ ] Criar um script único de rotina diária que apenas orquestre etapas já
  confirmadas, com modo seco por padrão.
- [ ] Gerar ao final da coleta um resumo em português, pronto para copiar:
  duração, fontes concluídas, falhas, novas vagas e elegíveis.
- [ ] Atualizar `outputs/LEIA_PRIMEIRO.md` quando a organização de saídas mudar.
- [ ] Padronizar o nome dos arquivos de saída com data e hora UTC.
- [ ] Arquivar relatórios antigos sem removê-los automaticamente.
- [ ] Manter logs curtos no console e detalhes técnicos em `outputs/logs/`.
- [ ] Criar checklist pré-publicação para evitar envio do arquivo ou lote errado.

## Prioridade 8 — dashboard e acompanhamento

- [ ] Exibir total de anúncios por fonte, dia e estado.
- [ ] Exibir taxa de extração e taxa de elegibilidade por fonte.
- [ ] Exibir motivos de bloqueio e erro técnico em linguagem simples.
- [ ] Exibir tempo médio por fonte e páginas por minuto.
- [ ] Exibir vagas novas versus vagas já conhecidas.
- [ ] Exibir fontes sem retorno, com circuit breaker ativo ou que precisam de
  adaptador.
- [ ] Exibir fila de payloads pronta para revisão e publicação.
- [ ] Incluir filtros por plataforma: Abler, Quickin, Workday, Senior, Sólides,
  página própria e outras.

## Prioridade 9 — documentação e manutenção

- [ ] Manter este TODO atualizado ao concluir ou abandonar uma tarefa.
- [ ] Atualizar `README.md` sempre que um comando operacional mudar.
- [ ] Documentar cada adaptador novo com domínio, formato, exemplo e teste.
- [ ] Registrar decisões de política de fontes em `docs/` com URL e evidência.
- [ ] Manter segredos apenas no `.env`; revisar `git status` antes de enviar ao
  GitHub.
- [ ] Executar `ruff`, `pytest` e validação do catálogo antes de commits.
- [ ] Criar changelog simples para mudanças de catálogo e adaptadores.

## Checklist de uma nova fonte

- [ ] A URL é de uma empresa, grupo, rede ou consultoria identificada.
- [ ] Há vaga ativa ou portal público de candidatura.
- [ ] A candidatura não direciona para plataforma bloqueada.
- [ ] Não foi encontrada proibição explícita de reprodução/redistribuição.
- [ ] A fonte foi incluída em `catalogo_fontes.csv`.
- [ ] A aprovação operacional foi incluída em `fontes_autorizadas.csv` quando
  aplicável.
- [ ] A fonte passou por `scripts/testar_fonte.py`.
- [ ] O tipo de extrator foi registrado: existente, genérico ou adaptador novo.
- [ ] Uma coleta real confirmou título, descrição, localização e URL de
  candidatura.
- [ ] Uma amostra de payload foi revisada antes de publicação.

## Critério de sucesso

O projeto estará operacionalmente maduro quando conseguir, todos os dias:

1. coletar fontes aprovadas de modo previsível e respeitoso;
2. identificar novidades sem repetir anúncios desnecessariamente;
3. preservar uma candidatura válida por vaga;
4. gerar payloads completos, revisáveis e idempotentes;
5. explicar claramente por que cada anúncio entrou, foi bloqueado ou falhou;
6. aumentar volume por evidência de retorno diário, não apenas por quantidade
   de URLs cadastradas.
