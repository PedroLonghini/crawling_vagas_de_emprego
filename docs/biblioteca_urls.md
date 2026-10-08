# Fontes do crawler

O único catálogo operacional é `config/catalogo_fontes.csv`.

Ele possui uma coluna:

```csv
url
https://empresa.com.br/trabalhe-conosco
https://jobs.lever.co/empresa
```

Adicione uma URL completa por linha. Não preencha identificador, nome da
empresa, tipo de site, status ou limite de páginas: o crawler calcula esses
dados automaticamente.

## Coletar

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --coletar --javascript
```

Para testar somente uma fonte, use o identificador informado no resumo do
crawler:

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py --listar
```

## Regras automáticas

- Toda URL válida entra como página de carreiras, ativa para coleta, com limite
  de dez páginas.
- O nome e o identificador internos são provisórios e não exigem edição manual.
- Domínios bloqueados (lista vigente em
  `src/observatorio_vagas/domain/politica_fonte.py`; inclui Gupy, Indeed, Catho,
  InfoJobs/Pandapé, Vagas.com e empregandobrasil.com.br) são rejeitados sem
  interromper as outras URLs.
- A coleta não autoriza publicação. A fonte só pode gerar payload de publicação
  depois da autorização da empresa ser registrada.

Não há catálogos operacionais paralelos. Use somente
`config/catalogo_fontes.csv`.
