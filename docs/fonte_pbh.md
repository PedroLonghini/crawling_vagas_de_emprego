# Fonte oficial: VAGAS OFERTADAS PBH

O projeto possui um importador separado para o conjunto público **VAGAS
OFERTADAS PBH**, mantido pela Prefeitura de Belo Horizonte, pela Secretaria
Municipal de Desenvolvimento Econômico e pela Central de Vagas SINE BH.

- Página oficial: <https://dados.pbh.gov.br/pt_BR/dataset/vagas-ofertadas-pbh>
- Licença informada pelo portal: Creative Commons Attribution.
- Formato utilizado: CSV.
- Frequência declarada pelo portal: trimestral.

No catálogo central, esta é atualmente a única fonte marcada como apta à
republicação. O registro guarda o nome e a URL da licença, exige atribuição e
declara explicitamente `republicacao_permitida=true`. A coleta permanece
desativada porque a integração correta desta fonte é o importador de CSV, não o
crawler HTML.

## Por que não passa pelo crawler HTML

O arquivo já é uma tabela estruturada. Tentar tratá-lo como página HTML faria o
crawler procurar JSON-LD e links que não existem. O importador lê as colunas do
CSV e cria os mesmos objetos `AnuncioVaga` que o restante do projeto usa. Depois
disso, MongoDB, normalização, elegibilidade e fila do Empregos continuam iguais.

## Segurança dos dados

O CSV não informa uma data oficial de expiração. Por isso o importador aplica
uma validade operacional conservadora de 30 dias e grava, nos metadados, que a
data foi inferida. Registros mais antigos ficam com status `encerrado` e não
podem ser publicados. A fonte informa CNPJ, mas não o nome nem a descrição da
empresa; esses campos ainda precisam de enriquecimento antes da publicação.

## Como testar sem gravar

Baixe o CSV pela página oficial e execute:

```powershell
.\.venv\Scripts\python.exe scripts\importar_vagas_pbh.py `
  --arquivo "C:\caminho\para\planilha.csv"
```

Para mostrar apenas registros dentro da validade operacional:

```powershell
.\.venv\Scripts\python.exe scripts\importar_vagas_pbh.py `
  --arquivo "C:\caminho\para\planilha.csv" `
  --somente-vigentes
```

Somente depois de revisar a prévia, abra o túnel SSH do MongoDB e repita o
comando com `--confirmar`. O texto de atribuição e a licença são preservados em
cada anúncio para acompanhar qualquer reutilização do dado.
