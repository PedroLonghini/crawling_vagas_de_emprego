# Guia da Entrega 2 — Modelos de domínio

Este guia assume conhecimento inicial de Python. A Entrega 2 não acessa banco,
API ou sites. Ela define as regras dos objetos que o restante do sistema usará.

## Por que criar modelos antes do crawler

Cada fonte usa nomes e formatos diferentes. Se cada crawler salvar diretamente
o que recebe, teremos várias estruturas incompatíveis. Os modelos criam uma
linguagem única para o produto.

Exemplo:

- o Empregos pode usar `jobTitle`;
- a Gupy pode usar `title`;
- uma página pode usar `name`;
- internamente, todos serão convertidos para `titulo_original`.

## Ordem de criação

### 1. `common.py`

Cria tipos reutilizados pelos outros modelos:

- `TextoObrigatorio`: rejeita texto vazio;
- `Confianca`: aceita somente valores entre 0 e 1;
- `DataHora`: exige fuso horário;
- `ObjetoJson`: preserva metadados flexíveis;
- `ModeloDominio`: rejeita campos desconhecidos.

Sem essa base, cada arquivo repetiria validações e poderia aplicar regras
diferentes para o mesmo conceito.

### 2. `enums.py`

Define listas fechadas, como fontes, status, modalidades e regimes. Enumerações
evitam grafias diferentes para o mesmo valor e melhoram filtros no banco.

### 3. `empresa.py`

Define:

- `Empresa`: identidade consolidada;
- `EmpresaFonte`: maneira como a empresa aparece em uma fonte.

A separação é necessária porque uma empresa pode ter IDs e nomes diferentes no
Empregos, na Gupy e na página de carreiras.

### 4. `anuncio.py`

`AnuncioVaga` preserva o anúncio de uma fonte. Ele contém campos originais,
hash SHA-256 e referência para o conteúdo bruto.

O anúncio não é a vaga canônica. A mesma oportunidade pode produzir vários
anúncios em fontes diferentes.

### 5. `vaga.py`

Define:

- `SalarioNormalizado`: valor classificado e conversões separadas;
- `VagaCanonica`: oportunidade consolidada usada nas análises.

Salário publicado, calculado e estimado são mantidos distintos para não gerar
estatísticas enganosas.

### 6. `coleta.py`

`ExecucaoColeta` registra quando e como um conector foi executado. Métricas e
checkpoint permitem monitorar e retomar uma execução interrompida.

### 7. `historico.py`

`ObservacaoAnuncio` registra como um anúncio estava em um momento. O sistema
não sobrescreve o passado. `AlteracaoCampo` informa exatamente o que mudou.

### 8. `correspondencia.py`

Compara dois anúncios e armazena os sinais utilizados. A decisão precisa ser
explicável e versionada, principalmente quando for usada para medir cobertura
do Empregos.

### 9. `evidencias.py`

Registra de onde veio uma informação extraída. Se a modalidade foi inferida da
descrição, a evidência guarda o trecho, método, confiança e versão do extrator.

### 10. `__init__.py`

Expõe os modelos públicos do pacote. Isso permite imports mais simples sem
conhecer o arquivo exato de cada classe.

## Testes

Os testes ficam em `tests/unit/domain` e não acessam serviços reais. Eles
validam, entre outros casos:

- CNPJ numérico e alfanumérico;
- rejeição de CNPJ estruturalmente inválido;
- domínio canônico;
- campos originais do anúncio;
- hash SHA-256;
- coerência da faixa salarial;
- cronologia de coleta;
- alterações históricas;
- pontuação de correspondência;
- evidência obrigatória para inferências.

## Comandos de validação

Na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m ruff format src tests
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m pytest
```

`ruff format` altera somente a apresentação do código. `ruff check` procura
problemas. `pytest` executa as regras verificáveis do domínio.

## Resultado esperado

```text
All checks passed!
25 passed
```

## O que não pertence à Entrega 2

- banco de dados;
- migrações;
- API do Empregos;
- crawler novo;
- agendamento;
- dashboard;
- gravação de arquivos brutos.

Essas partes dependem dos modelos, mas serão implementadas em entregas
posteriores.
