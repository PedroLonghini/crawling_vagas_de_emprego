# Renderização JavaScript

## Teste isolado de uma fonte

Para conferir os IDs habilitados no catálogo:

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py --listar
```

Exemplo com um alvo cadastrado, três listagens e até vinte detalhes:

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py --alvo-id dataprivacy_oportunidades --javascript --paginas 3 --anuncios 20
```

O comando salva catálogo isolado, respostas brutas, cobertura, resumo e anúncios
em uma pasta nova de `outputs/testes_fontes`. Mostra as contagens de páginas,
candidatos, downloads, anúncios extraídos, descrições e empresas encontradas.
Não acessa MongoDB nem a API. É um teste de coleta/extração, não de elegibilidade
dos 24 campos para publicação. O limite de encerramento do crawler é de cinco
minutos; downloads em andamento podem levar algum tempo adicional para terminar.

Se um ID não existir ou estiver bloqueado, o comando retorna erro sem selecionar
outra fonte. Linhas inválidas de outras fontes não impedem o teste.

## Coleta em lote

Ative no lote com `--javascript`. O Chromium executa os scripts das páginas HTML
recebidas e entrega o DOM renderizado aos mesmos extratores e ao armazenamento bruto.
JSON, CSV, PDF e respostas HTTP de erro não são renderizados.

Instalação:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[crawler,javascript]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\testar_renderizacao_javascript.py
```

Coleta e prévia de extração:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes.csv --coletar --somente-republicaveis --javascript --limite-anuncios 100 --limite 1000 --alvos-por-coleta 5
```

Acrescente `--confirmar` para gravar no MongoDB. O modo de renderização não altera
a política de republicação. Em `outputs/cobertura`, cada resultado possui o campo
`javascript`: renderizado, falha, limite ou desativado.

Limites padrão: duas renderizações simultâneas, 20 páginas por alvo, 30 segundos
de navegação e espera de 2 segundos após o carregamento. Uma falha preserva o HTML
original. Nas listagens, rola até o final e clica em botões explícitos de
"carregar mais"/"mostrar mais"/"load more" fora de formulários. Faz até cinco
rodadas e para após duas rodadas sem novas descobertas. Não segue botões que
possuem href: esses links ficam com a paginação normal do crawler.
Páginas de detalhes não recebem cliques nem rolagem.

Links e JobPosting JSON-LD removidos por listas virtualizadas são preservados em
uma seção identificada no HTML salvo. Ao esgotar o tempo durante a expansão,
mantém os snapshots concluídos. A rolagem avança em passos de 80% da altura
visível, incluindo até cinco componentes internos com overflow e altura maior
que 80 pixels. Componentes fora desses critérios e controles sem rótulos
reconhecidos ainda precisam de adaptador específico.

O relatório por página inclui `javascript_diagnostico`: rodadas, cliques,
movimentos de rolagem, motivo de encerramento, recursos permitidos/bloqueados
e contagem por domínio externo bloqueado. Isso permite identificar as CDNs
e APIs que necessitam de configuração, sem armazenar suas queries no diagnóstico.

Configurações Scrapy: `JAVASCRIPT_ENABLED`, `JAVASCRIPT_MAX_PAGES_PER_TARGET`,
`JAVASCRIPT_TIMEOUT`, `JAVASCRIPT_WAIT_SECONDS`, `JAVASCRIPT_MAX_ROUNDS` (máximo 20).
Podem ser passadas com `-s` ao
comando `python -m scrapy crawl catalogo_fontes`.

Os recursos adicionais são limitados a 60 GETs de scripts, estilos e consultas
XHR/fetch do mesmo domínio e porta. Respeitam robots.txt e DOWNLOAD_DELAY.
Redirecionamentos de recursos, service workers, WebSockets, imagens, formulários
e domínios externos são bloqueados. Sites dependentes de CDN ou APIs externas
precisam de configuração específica futura. Ainda não há garantia de que todos
os cards de todas as fontes serão encontrados.

No Windows, o Playwright usa ProactorEventLoop numa thread independente do Scrapy.
Referência: https://playwright.dev/python/docs/library
