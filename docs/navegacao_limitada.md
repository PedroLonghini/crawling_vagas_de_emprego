# Navegação entre listagens e detalhes

O spider `catalogo_fontes` segue links explícitos de próxima página e controles
numéricos de paginação HTML, além dos links encontrados pelos adaptadores.
Listagens reconhecidas podem descobrir novas vagas; páginas de detalhe não
iniciam navegação recursiva. URLs repetidas são descartadas por alvo.

`limite_paginas` é um orçamento total por alvo: com 10, a página inicial,
listagens seguintes e detalhes compartilham essas dez posições. Um
redirecionamento não gasta uma segunda posição do orçamento. Tentativas HTTP
e robots.txt não representam novas posições de conteúdo. Para fontes HTML genéricas,
até metade do orçamento (no máximo dez páginas) é usada para alcançar páginas de
listagem posteriores; o restante é distribuído entre os detalhes descobertos. Isso
evita que muitos cards da primeira tela escondam as vagas das páginas 2, 3 e seguintes.
O limite não garante dez resultados.

Continuam ativas as verificações de domínio, política, robots.txt e bloqueios.
Não são executados botões JavaScript nem acessados documentos em domínios
externos usando indiscriminadamente a autorização do domínio inicial. A única
exceção de armazenamento adicionada é o CSV SETADES no host oficial do ES:
dataset, UUID e nome do arquivo precisam coincidir com o redirect do portal.
A paginação do Querido Diário é sequencial; uma resposta pode conter vários diários.
Um JSON com zero resultados encerra a busca, mesmo que o limite seja dez.

Para testar com fontes reais, execute novamente o comando de coleta já utilizado.
Os registros antigos não ganham páginas adicionais sem nova coleta. O log
`Navegação` mostra páginas agendadas, limite e links de paginação encontrados.
O resumo da extração distingue arquivos de outros alvos/períodos de respostas
HTTP ou formatos incompatíveis. A coleta não libera automaticamente republicação.

O log `Resumo da fonte` mostra agendadas, recebidas e falhas de download.
`Fim da navegação` distingue limite atingido, ausência de links e links
repetidos/fora do domínio. Querido Diário também informa diários na página e
total da busca. Mil arquivos “de outros alvos/períodos” não representam mil
links ignorados dessa fonte: são registros antigos do inventário bruto.

Não há teto global implícito de 100 respostas na execução direta deste spider;
o orçamento de cada alvo continua obrigatório. `processar_lote.py` ainda aplica
uma trava global calculada para cada bloco. `--alvos-por-coleta` limita o número
de fontes por processo, não o número de páginas de cada fonte.

Validação em 08/09/2026: teste de regressão percorreu dez páginas do Querido
Diário; teste integrado de índice CKAN + dois CSVs extraiu quatro anúncios.
Na amostra real SETADES, três páginas produziram 58 anúncios e zero falhas de
extração. Não foram gravados no MongoDB nem enviados ao Empregos; aptidão para
publicação é uma avaliação separada.

Verificação do código: Ruff check/format aprovados; 507 testes passaram. A suíte
completa ainda apresenta quatro erros antigos em `tests/unit/demo/test_analytics.py`
porque `data/demo/vagas_demo.csv` não existe. Não foram ocultados nem substituídos
por dados fictícios nesta alteração. Logs da amostra real estão em
`outputs/validacao_fontes_20260908/coleta_final.log`.
