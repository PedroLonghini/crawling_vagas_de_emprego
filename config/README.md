# Fontes do crawler

Edite somente `catalogo_fontes.csv`.

O arquivo possui uma única coluna chamada `url`. Adicione uma URL completa por
linha, por exemplo:

```csv
url
https://empresa.com.br/trabalhe-conosco
https://jobs.lever.co/empresa
```

O crawler cria automaticamente o identificador, o nome provisório, o tipo de
fonte e o limite seguro de páginas. URLs de domínios bloqueados (a lista vigente
está em `src/observatorio_vagas/domain/politica_fonte.py`; inclui Gupy, Indeed,
Catho, InfoJobs/Pandapé, Vagas.com e empregandobrasil.com.br) são ignoradas e
aparecem no relatório.

Uma URL nova fica habilitada para coleta, mas não para publicação. A publicação
só é liberada depois que a autorização da empresa for registrada no processo de
aprovação.

## Quando aparece um tipo novo de plataforma

Adicionar mais uma empresa em uma plataforma que o crawler já conhece (Abler,
Sólides, Workday, SmartRecruiters, Quickin...) continua sendo só colar a URL
no `catalogo_fontes.csv`.

As regras de rede de cada plataforma (APIs externas permitidas, redirecionamentos
oficiais, política de sitemap) ficam em um único arquivo:
`src/observatorio_vagas/crawling/plataformas.toml`. Ele só precisa mudar quando
surge um tipo novo de plataforma ou quando uma plataforma troca de host de API.
O arquivo se valida ao carregar e `tests/unit/crawling/test_plataformas.py`
confere que cada regra passa pela barreira e pela fábrica de requisições.
