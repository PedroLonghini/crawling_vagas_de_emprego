# Pacote para solicitar feeds grandes de vagas

Este pacote serve para SINE, secretarias de trabalho e prefeituras. Ele não
autoriza coleta por si só: a fonte só entra no catálogo após resposta escrita,
validação da permissão e teste técnico isolado.

## Mensagem-modelo — API ou CSV

**Assunto:** Solicitação de acesso a dados de vagas e autorização de
redistribuição — [ÓRGÃO]

Olá, [NOME/EQUIPE],

Somos responsáveis pelo Observatório de Vagas, iniciativa que organiza e
direciona pessoas a oportunidades de trabalho. Gostaríamos de incluir as vagas
divulgadas por [ÓRGÃO] com a atribuição e o link de candidatura para a fonte
oficial.

Para isso, solicitamos, se disponível:

1. API, CSV, feed ou outro recurso oficial com as vagas vigentes;
2. documentação de campos, paginação, atualização e limites de uso;
3. uma licença aberta ou autorização escrita que permita acessar, armazenar e
   redistribuir os dados das vagas, inclusive título, empresa, localidade,
   descrição, requisitos, data de publicação/expiração e URL de candidatura;
4. a forma de atribuição exigida e qualquer restrição de uso;
5. contato técnico para homologação e aviso de alterações no feed.

Não coletamos currículos, dados de candidatos, documentos pessoais ou áreas
autenticadas. Exibiremos a origem da vaga e direcionaremos a candidatura ao
canal oficial. Também removeremos ou atualizaremos itens expirados conforme a
orientação de [ÓRGÃO].

Se for possível conceder a autorização por e-mail, pedimos que ela identifique
o recurso autorizado, o escopo dos dados e se a redistribuição é permitida. Um
texto de referência está abaixo.

Obrigado,

[NOME]
[CARGO/ORGANIZAÇÃO]
[E-MAIL]
[TELEFONE]
[SITE DO PROJETO]

## Mensagem-modelo — autorização para página existente

**Assunto:** Pedido de autorização para republicar vagas divulgadas pelo
[ÓRGÃO]

Olá, [NOME/EQUIPE],

O Observatório de Vagas gostaria de divulgar as vagas publicadas em
[URL OFICIAL], sempre com atribuição a [ÓRGÃO], link para a vaga original e
remoção/atualização quando a oportunidade expirar.

Poderiam confirmar por escrito se é permitido acessar de forma automatizada e
redistribuir os dados públicos das vagas? Caso positivo, pedimos que indiquem:

- as URLs e os campos autorizados;
- se há API/CSV/feed preferencial, limite de requisições e regras de acesso;
- a licença ou os termos aplicáveis;
- a forma obrigatória de atribuição;
- regras para atualização, expiração e retirada de conteúdo.

Não acessaremos áreas com login, não coletaremos dados de candidatos e não
alteraremos o sentido da informação divulgada pelo órgão.

Obrigado,

[ASSINATURA]

## Texto de confirmação sugerido ao órgão

> [ÓRGÃO] autoriza [ORGANIZAÇÃO/PROJETO] a acessar o recurso
> [URL/API/CSV] e redistribuir as informações públicas de vagas nele
> disponibilizadas, incluindo [CAMPOS]. A autorização vale para
> [TERRITÓRIO/PERÍODO], sob [LICENÇA/TERMOS], mediante a atribuição
> [TEXTO/URL DE ATRIBUIÇÃO]. O limite técnico é [LIMITE], e vagas removidas ou
> expiradas devem ser atualizadas/removidas em até [PRAZO].

Esse texto é uma referência operacional; o órgão deve usar a redação que seu
setor jurídico aprovar. Uma resposta que apenas diga "o portal é público" não
é suficiente.

## Checklist jurídico e de política

- [ ] Identificar órgão, área responsável e contato que tem autoridade para
  conceder a permissão.
- [ ] Registrar URL oficial da licença, dos termos ou da autorização escrita.
- [ ] Confirmar que a permissão cobre **vagas**, e não outro produto do órgão.
- [ ] Confirmar acesso automatizado, armazenamento e redistribuição.
- [ ] Registrar os campos permitidos e excluir dados pessoais/currículos.
- [ ] Registrar atribuição, link obrigatório e eventual aviso de fonte.
- [ ] Confirmar se há limites territoriais, temporais ou de finalidade.
- [ ] Registrar regra de correção, expiração e remoção.
- [ ] Verificar se termos, login, CAPTCHA ou `robots.txt` contradizem a
  permissão; pedir esclarecimento escrito se houver divergência.
- [ ] Guardar a resposta original e data da confirmação no dossiê da fonte.

## Checklist técnico

- [ ] URL estável de API/CSV/feed; não depender de página autenticada.
- [ ] Documentação de autenticação, quota, paginação, filtros e versão.
- [ ] Identificador estável da vaga e data de criação/atualização.
- [ ] Título, empresa, localidade, descrição/requisitos, URL de candidatura e
  data de expiração disponíveis ou documentados como ausentes.
- [ ] Atualização incremental (por data/ETag/`updated_since`) ou frequência
  acordada.
- [ ] Ambiente de homologação ou amostra segura para o primeiro teste.
- [ ] Respostas 2xx, codificação UTF-8, schema consistente e erros previsíveis.
- [ ] Limites, atraso entre requisições, retentativas e contato de suporte
  registrados.
- [ ] Teste isolado com limite baixo salvo em `outputs/testes_fontes/`.
- [ ] Amostra revisada: sem concurso/edital, sem dados pessoais e com
  candidatura direcionada à fonte.

## Critério de ativação

Só promover para `config/catalogo_fontes.csv` quando todos os itens jurídicos
e técnicos relevantes estiverem marcados, a evidência estiver arquivada e a
amostra tiver sido aprovada. A linha deve conter licença, URL de comprovação,
atribuição e `republicacao_permitida=true`; antes disso permanece fora da
coleta e da publicação.
