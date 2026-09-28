# Primeira publicação-teste

Este roteiro publica no máximo **uma** vaga elegível, originada de uma fonte
aprovada no catálogo. Ele não altera a permissão de nenhuma fonte e não envia
vaga alguma sem a confirmação explícita no último comando.

## 1. Preencher a API

No arquivo local `.env`, preencha somente os valores oficiais recebidos do
Empregos. O arquivo já está no `.gitignore`; não envie a chave por mensagem,
e-mail ou commit.

```dotenv
# Use staging se o parceiro fornecer homologação. Caso só exista produção,
# use production para liberar o teste controlado.
OBS_ENVIRONMENT=staging

# Copie exatamente o host e o caminho informados pela documentação da API.
OBS_EMPREGOS_API_BASE_URL=https://api.fornecida-pelo-empregos.com.br
OBS_EMPREGOS_API_PUBLICATION_PATH=/caminho/oficial/de/publicacao

# Exemplos: Authorization + Bearer, ou X-API-Key sem prefixo.
OBS_EMPREGOS_API_AUTH_HEADER=Authorization
OBS_EMPREGOS_API_AUTH_PREFIX=Bearer
OBS_EMPREGOS_API_KEY=COLE_A_CHAVE_AQUI

# A última trava. Só use true imediatamente antes do POST de teste.
OBS_EMPREGOS_PUBLICACAO_HABILITADA=true
```

Se a documentação disser que a API usa `X-API-Key`, substitua o cabeçalho por
`X-API-Key` e deixe `OBS_EMPREGOS_API_AUTH_PREFIX` vazio. Não adivinhe URL,
caminho ou formato de autenticação.

## 2. Conferir a configuração

Este comando não usa MongoDB e não chama a API. Ele confirma se endpoint,
autenticação, ambiente e a trava estão prontos, sem imprimir a chave.

```powershell
.\.venv\Scripts\python.exe scripts\verificar_configuracao_publicacao_empregos.py
```

O resultado deve terminar em `PRONTO PARA UMA PUBLICAÇÃO-TESTE DE UMA VAGA`.

## 3. Simular a escolha da vaga

O comando abaixo seleciona candidatos das fontes presentes no catálogo e mostra
o que seria enviado. Não faz POST nem grava histórico.

```powershell
.\.venv\Scripts\python.exe scripts\publicar_lote_empregos.py `
  --limite 100 `
  --maximo-envios 1 `
  --saida-json outputs\publicacao_teste_simulada.json
```

Ele precisa mostrar `Elegíveis: 1` ou mais e `Chamadas HTTP: 0`. Se não houver
elegível, a coleta, normalização ou os campos obrigatórios ainda precisam ser
completados; não force a publicação.

## 4. Fazer o único POST de teste

Repita o mesmo comando acrescentando a confirmação explícita:

```powershell
.\.venv\Scripts\python.exe scripts\publicar_lote_empregos.py `
  --limite 100 `
  --maximo-envios 1 `
  --confirmar-publicacao `
  --saida-json outputs\publicacao_teste_real.json
```

O processo registra uma chave idempotente antes do POST. Por isso, se o comando
for executado novamente, uma publicação que já recebeu sucesso não é enviada
de novo. Em falha de rede ou resposta inconclusiva, ele também não repete o
POST automaticamente.
