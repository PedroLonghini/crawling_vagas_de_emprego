# Otimização e fontes — 11/09/2026

## Desempenho

- A extração do lote agora roda no mesmo Python, sem iniciar um interpretador
  para cada alvo. Coleta Scrapy e etapas posteriores de normalização continuam
  usando seus subprocessos.
- O inventário de metadados é lido uma vez, depois de concluir a coleta, e
  compartilhado entre as extrações. Não há cache permanente: um novo lote
  relê o inventário para enxergar novas respostas.
- Os filtros de alvo/data, verificação de SHA-256, tamanho e caminho dos corpos
  continuam ativos. Os contadores de páginas ignoradas preservam o significado.
- Exceções de um extrator continuam isoladas por alvo. Interrupções do usuário
  não são tratadas como falhas comuns.
- Concorrência global: 8 → 16. Por domínio: continua 1, com intervalo de 1 segundo,
  AutoThrottle e robots.txt. Muitos alvos no mesmo domínio não ganham 16 conexões.
- Não foi medido um percentual de ganho de ponta a ponta. O ganho depende da
  quantidade de alvos, dos arquivos locais e da velocidade dos sites.

## Limpeza

Removidas 132 pastas `.pytest_cache*`/`.pytest_tmp*` da raiz, após validar o caminho
e verificar ausência de links. São caches e dados de testes regeneráveis;
a remoção foi direta, sem Lixeira. Outras 33 pastas foram preservadas por falta
de acesso seguro. Dados brutos, relatórios, backups, `.env` e `.venv` não foram apagados.
Também foi removida uma declaração duplicada de `coletado_em` do inventário.

## Fontes adicionadas

### Open Knowledge Brasil

- Entrada: https://ok.org.br/noticias/
- Evidência: o rodapé declara CC BY 4.0 para conteúdo próprio, salvo exceções.
- Exemplo de contratação própria (histórico, não vaga vigente):
  https://ok.org.br/noticia/open-knowledge-brasil-abre-vaga-para-analista-de-captacao-de-recursos/
- Exige autoria/fonte, link da licença e indicação de alterações quando houver.

### InternetLab

- Entrada: https://internetlab.org.br/pt/blog/
- Evidência: rodapé do site e da página de contratação declara CC BY-SA 4.0.
- Política: https://internetlab.org.br/pt/politica-de-privacidade/
- Exemplo histórico:
  https://internetlab.org.br/pt/noticias/internetlab-abre-selecao-para-pesquisadora-e-estagio-em-comunicacao-e-pesquisa/
- Exige atribuição e compartilhamento sob a mesma licença quando aplicável.
  Não transferir automaticamente essa permissão a material de terceiros.

As entradas usam descoberta HTML existente, com limite de 10 páginas por alvo.
Notícias gerais não devem ser consideradas vagas; links de candidatura externos
não ampliam a permissão de coleta para outros domínios. Não foi demonstrada
extração de vagas vigentes dessas duas fontes nesta alteração. Conteúdo dinâmico,
anúncios com múltiplas posições ou datas em texto podem exigir adaptação adicional.
Os exemplos acima são antigos e não devem ser publicados como oportunidades abertas.

Wikimedia Brasil também foi investigada, mas a listagem respondeu HTTP 403
neste ambiente; não entrou no catálogo operacional. ARTIGO 19 e Terra de Direitos
foram descartadas desta ampliação por restrição de uso não comercial.

## Testar sem Mongo e sem publicar

No PowerShell, na raiz do projeto, execute em uma linha:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes_20260911_teste.csv --coletar
```

Esse comando acessa as duas fontes, salva respostas brutas localmente e mostra
a prévia da extração. Não grava anúncios no MongoDB e não publica no Empregos.
As duas fontes também estão nos catálogos central, diário, republicável e na
biblioteca `config/urls_fontes.txt`.
