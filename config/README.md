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
fonte e o limite seguro de páginas. URLs de domínios bloqueados, como Gupy,
Indeed, Catho e InfoJobs/Pandapé, são ignoradas e aparecem no relatório.

Uma URL nova fica habilitada para coleta, mas não para publicação. A publicação
só é liberada depois que a autorização da empresa for registrada no processo de
aprovação.
