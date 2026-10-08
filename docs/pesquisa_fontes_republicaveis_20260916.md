# Pesquisa de fontes republicáveis — 16/09/2026

Esta rodada procurou vagas de emprego diretas, com atualização recorrente e
autorização verificável de reutilização. Fontes públicas sem licença explícita,
sem termo de reutilização ou sem confirmação técnica não foram ativadas.

## Fonte aprovada já existente

- **PBH SINE / Vagas ofertadas**: permanece ativa como `pbh_sine_vagas_abertas`.
  O conjunto é CSV, trata de vagas divulgadas pelo SINE de Belo Horizonte e
  declara Creative Commons Attribution. A atualização indicada pelo portal é
  trimestral.

- **IFTM / Vagas de Estágio e Emprego**: permanece ativa como
  `iftm_vagas_estagio_emprego`. O catálogo aberto do Instituto Federal do
  Triângulo Mineiro declara Creative Commons Attribution e publica CSVs de
  vagas. Teste isolado em 17/09/2026: os dois recursos responderam HTTP 200;
  o arquivo continha somente vagas vencidas ou sem prazo final. O extrator
  aceita exclusivamente linhas com `dt_vigencia_limite` igual ou posterior à
  data de coleta, para não publicar oportunidades sem vigência comprovada.

## Candidatas não ativadas

As quatro candidatas abaixo ficam apenas no inventário de pesquisa
`config/fila_prospeccao_fontes.csv` (coluna `decisao`). O
`config/catalogo_fontes.csv` tem só a coluna `url` e não distingue estado:
**não cole a URL de uma candidata no catálogo** antes da confirmação indicada em
cada caso, porque ela passaria a ser coletável.

### SETE Amapá — Secretaria de Estado do Trabalho e Empreendedorismo

- URL: `https://sete.portal.ap.gov.br/`
- Conteúdo encontrado: notícias recentes de mutirões do SINE com 50 e mais de
  100 vagas, incluindo estágio e aprendizagem.
- Licença exibida no rodapé: `Creative Commons 3.0 International`.
- Decisão: **pendente**. O texto exibido não identifica a variante da licença
  (por exemplo, CC BY versus restritiva) e a consulta a `robots.txt` retornou
  HTTP 403. Não é seguro inferir autorização de republicação ou contornar essa
  resposta. Reavaliar somente se a SETE publicar link para a licença completa e
  uma política de acesso automatizado verificável.

### Secretaria de Trabalho e Renda do Rio de Janeiro

- URL: `https://www.rj.gov.br/trabalho/dados_abertos`
- Conteúdo encontrado: página estatal que referencia vagas SINE, mas somente
  arquivos de 2020, 2021 e 2022.
- Decisão: **descartada por ora**. A página não declara uma licença de
  reutilização e não oferece vagas atuais; portanto não aumenta a cobertura de
  anúncios vigentes.

### Ministério do Trabalho e Emprego — IMO/SINE

- Evidência encontrada: o Plano de Dados Abertos 2025–2027 menciona a Base de
  Gestão da Intermediação de Mão de Obra (IMO), mas a evidência disponível não
  oferece um recurso público atual de vagas individuais com licença e endpoint
  de acesso para o crawler.
- Decisão: **acompanhar**, sem cadastrar. Quando o conjunto for publicado com
  metadados de licença e recurso aberto, priorizar um adaptador específico.

## Regra aplicada

Uma fonte só entra ativa quando houver, ao mesmo tempo: licença ou autorização
de republicação inequívoca, acesso permitido, conteúdo de vagas não ligado a
concursos/editais e compatibilidade técnica confirmada. Acesso público isolado
não é prova suficiente.

## Prospecção de alto volume

A fila rastreável está em `config/fila_prospeccao_fontes.csv` e aplica o
processo documentado em `docs/processo_prospeccao_fontes.md`.

- **Sine Fortaleza**: notícia oficial de 11/09/2026 registra 2.623
  oportunidades em 284 empresas. É prioridade máxima de licença, mas a licença
  CC BY 4.0 encontrada pertence à IDE-SEFIN, uma plataforma de dados espaciais,
  e não ao portal de notícias do Sine. Portanto não foi extrapolada.
- **Sine Maceió**: notícia oficial de 30/06/2026 registra 1.135 vagas na
  semana. Sem termos de reutilização aplicáveis localizados, permanece pendente.
- **Sine João Pessoa**: notícia oficial de 14/06/2026 registra 425 vagas na
  semana. Sem licença aplicável localizada, permanece pendente.
- **Sine Contagem**: notícia oficial registra mais de 1.200 vagas em um dia,
  mas os termos do próprio portal proíbem reprodução para fins comerciais.
  Resultado: bloqueada, apesar do volume.

Essas fontes não foram ativadas nem submetidas a crawler porque ainda não
passaram pelo portão jurídico.

## Agência de Notícias do Paraná

- URL de descoberta: `https://www.parana.pr.gov.br/aen/noticias?combine=vagas&sort_by=created&sort_order=DESC`
- Política: a própria Agência de Notícias informa que todas as notícias são
  licenciadas em CC0, isto é, domínio público. A página da licença é registrada
  no catálogo como evidência.
- Escopo observado: notícias recentes relatam milhares de oportunidades nas
  Agências do Trabalhador; algumas detalham cargo e regional, mas podem não
  identificar empregador ou URL de candidatura por vaga.
- Decisão: cadastrada no catálogo central como `parana_aen_vagas_cc0`, porém
  **inativa** até o teste técnico confirmar que há anúncios individuais
  extraíveis e suficientes para o modelo do Empregos. A licença permite o
  reuso; ela não transforma um resumo agregado em vaga individual.
- Teste técnico em 16/09/2026: `robots.txt` retornou 200 e a página respondeu
  200. Foram feitas duas tentativas limitadas (HTML e JavaScript), ambas com
  zero links candidatos, zero detalhes agendados e zero anúncios extraídos.
  A página de notícias expõe os totais em conteúdo dinâmico, mas não fornece ao
  adaptador atual detalhes individuais de vaga. Não ativar sem um feed ou uma
  adaptação que produza anúncios verificáveis.
