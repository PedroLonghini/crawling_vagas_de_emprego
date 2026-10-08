# Processo de prospecção de fontes

A fila em `config/fila_prospeccao_fontes.csv` é um inventário de pesquisa, não
um catálogo de coleta. Nenhuma linha dela pode ser usada pelo crawler ou pela
publicação enquanto estiver `pending`, `restritiva` ou `bloqueada`.

## Portões obrigatórios

1. **Triagem de escopo.** A fonte precisa conter vagas de emprego atuais, não
   concurso, edital, licitação ou mera estatística agregada.
2. **Política.** Registrar uma URL oficial e o trecho que concede uma licença
   ou autorização de republicação para aquele conteúdo. "Portal público",
   "uso público" ou uma licença de outro produto do mesmo órgão não bastam.
   Termos não comerciais, não derivados ou com login obrigatório bloqueiam a
   promoção automática.
3. **Teste técnico isolado.** Somente depois da política aprovada, rodar
   `scripts/testar_fonte.py` contra um catálogo temporário com limites baixos.
   Registrar HTTP, `robots.txt`, paginação, candidatos, extração e duplicação.
4. **Aprovação.** Revisar uma amostra de anúncios extraídos: título, empresa,
   localidade, descrição, data e URL de candidatura; confirmar que não há
   concursos/editais nem dados pessoais indevidos.
5. **Ativação rastreável.** Só então colar a URL no catálogo de coleta
   (`config/catalogo_fontes.csv`, que hoje tem só a coluna `url`) e em
   `config/fontes_autorizadas.csv`, e registrar a evidência (licença, URL oficial
   e trecho) e a decisão em `config/fila_prospeccao_fontes.csv` (colunas
   `url_termos_ou_licenca`, `evidencia_observada`, `decisao`). As colunas
   `ativa`, `status_politica` e `republicacao_permitida` citadas em versões
   antigas deste documento não existem mais nos CSVs. Quem decide a
   autorização é o usuário; a política de domínios bloqueados continua valendo
   (`domain/politica_fonte.py`).

## Estados da fila

- `pending`: falta evidência inequívoca; não testar nem coletar.
- `restritiva`: os termos encontrados não permitem o uso necessário; não
  coletar nem publicar.
- `bloqueada`: decisão final até que haja novos termos oficiais.
- `aprovada_para_teste`: todos os requisitos jurídicos documentados; pode
  receber teste técnico isolado.
- `aprovada`: passou nos quatro portões e já possui linha operacional aprovada.

## Ordem de trabalho

Trabalhar por `prioridade`, começando por fontes oficiais de alto volume. Para
cada uma, procurar primeiro o portal de dados ou os termos do mesmo domínio;
se não houver licença aplicável, registrar a ausência e encerrar a rodada. Não
contornar login, CAPTCHA, `robots.txt` ou limite de acesso.

As evidências iniciais e decisões desta rodada estão em
`docs/pesquisa_fontes_republicaveis_20260916.md`. A fila registra a URL
específica e o motivo para que a pesquisa seja repetível.
