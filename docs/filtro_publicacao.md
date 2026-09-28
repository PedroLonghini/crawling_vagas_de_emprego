# Selecionar vagas publicadas ontem

Use `--publicados-ontem` para selecionar o dia anterior no fuso
America/Sao_Paulo. O dia é fixado no início do comando, inclusive em lotes
que atravessam a meia-noite. Não é uma janela móvel de 24 horas.

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes.csv --coletar --somente-republicaveis --javascript --publicados-ontem --limite-anuncios 100 --limite 1000 --alvos-por-coleta 5
```

Adicione `--confirmar` para gravar os anúncios selecionados no MongoDB.
Também funciona em `scripts/testar_fonte.py` e
`scripts/processar_e_salvar_anuncios.py`. Para repetir o mesmo período outro
dia, use `--publicados-em 2026-09-15` em vez de `--publicados-ontem`.

O filtro usa `publicado_em` extraído da fonte. A data de coleta não substitui
a data de publicação. Horários com fuso são convertidos para Brasília;
datas sem horário mantêm o dia informado. Datas ausentes/indeterminadas são
excluídas e contadas separadamente. O resumo informa anúncios selecionados,
de outras datas e sem data.

O crawler ainda visita páginas para descobrir as datas e preserva os dados
brutos. A seleção acontece após extração/deduplicação e antes da gravação
dos anúncios. Não garante encontrar todas as vagas de ontem: os limites de
navegação e a qualidade dos extratores continuam se aplicando. Anúncios já
existentes no MongoDB não são apagados por esse filtro.

Sem esses argumentos, o comportamento continua sem filtro de publicação.
