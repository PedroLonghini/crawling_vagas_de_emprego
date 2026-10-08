# Fontes verificadas em 08/09/2026

> **Documento histórico (marcado em 07/10/2026).** O arquivo
> `config/catalogo_fontes_republicaveis.csv` citado abaixo não existe mais; não há
> catálogos paralelos. Os comandos com ele não funcionam. O catálogo de coleta atual
> é `config/lote_10mil_triado/catalogo_fontes.csv` (ver README.md).

## Fonte nova cadastrada

UnB — Editais de Concurso: https://dados.unb.br/dataset/editais-de-concurso

A página oficial informa Creative Commons Attribution. Cadastro no catálogo
central: `unb_editais_concurso`, com atribuição obrigatória e `ativa=false`.
O endereço sem `www` e o domínio `dados.unb.br` foram acessados com HTTPS válido.
O robots permite as páginas e downloads, bloqueia `/api/` e pede intervalo de 10 s.

O CSV consultado possui `data_edital`, `data_dou`, `numero_edital`, `id_concurso`,
`ano_edital`, `titulo`, `id_edital`, `data_publicacao`, `numero_dou`,
`tipo_concurso` e `qtd_vagas_edital`. Contém editais de abertura e retificações,
inclusive de anos anteriores, sem prazo de inscrição. Ainda precisa de conversor,
deduplicação por concurso e confirmação da vigência antes de ativação.
Não foi acrescentado ao catálogo operacional nem às URLs operacionais.

## Resultados descartados nesta pesquisa

- UnB / processos-seletivos: o CSV trata de vestibular e ingresso em cursos.
- UFC / mapeamento-de-oportunidades: editais de fomento à cultura, não empregos.
- AGEHAB / concursos-publicos-e-selecoes: o conjunto informa
  "Nenhuma Licença Fornecida"; o rodapé não identifica a variante Creative Commons.
- UFAM / concursos TAE: o endereço do conjunto retornou HTTP 404; uma página
  de grupos indexada indica somente "Outra (Aberta)", sem termos específicos.

O catálogo operacional permanece com 515 alvos de 6 provedores. Esta pesquisa
adicionou uma fonte licenciada ao inventário, mas nenhum novo alvo operacional.

## Comandos PowerShell na raiz do projeto

Coletar todas as fontes operacionais, extrair e mostrar a prévia:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes_republicaveis.csv --coletar --alvos-por-coleta 10 --limite 1000
```

Coletar e gravar anúncios, empresas e vagas canônicas no MongoDB:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes_republicaveis.csv --coletar --alvos-por-coleta 10 --limite 1000 --confirmar
```

Avaliar as vagas já gravadas e salvar o relatório de elegibilidade:

```powershell
.\.venv\Scripts\python.exe scripts\preparar_fila_empregos.py --catalogo config\catalogo_fontes_republicaveis.csv --somente-catalogo --limite 10000 --saida-json outputs\aptidao_republicaveis.json
```

`--alvos-por-coleta 10` divide as fontes em grupos de dez por processo.
`--limite 1000` limita os anúncios processados por alvo; não limita páginas.
O orçamento de páginas está no CSV (`limite_paginas`, atualmente dez).
Na preparação, `--limite 10000` limita os anúncios consultados no MongoDB.
A etapa de gravação e a consulta exigem MongoDB acessível. Nenhum desses comandos
publica na API do Empregos. Executar ambos os primeiros comandos faz duas coletas;
use a prévia para testar ou o segundo comando quando quiser gravar diretamente.
