# Roda, em ordem, o que ficou pendente enquanto o túnel do MongoDB estava fora.
#
#   powershell -ExecutionPolicy Bypass -File scripts\volta_do_tunel.ps1
#
# 1. Confere se o MongoDB responde (para logo se não responder).
# 2. Apaga do Mongo os anúncios da Gupy (fonte bloqueada), com backup.
# 3. Relê as coletas desde 01/10 com o código atual e grava no Mongo.
# 4. Apaga as empresas que ficaram sem anúncio e sem vaga (nomes falsos), com backup.
# 5. Gera o lote de vagas prontas para publicar (não publica nada).
#
# Cada etapa grava seu log em outputs\logs\. Nada é enviado à API do Empregos.

$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$env:PYTHONIOENCODING = 'utf-8'
$python = Join-Path $raiz '.venv\Scripts\python.exe'
$hoje = Get-Date -Format 'yyyy-MM-dd'
$logs = Join-Path $raiz 'outputs\logs'
New-Item -ItemType Directory -Force $logs | Out-Null

function Etapa($nome, [scriptblock]$comando) {
    Write-Host ""
    Write-Host "== $nome ($(Get-Date -Format HH:mm))"
    & $comando
    if ($LASTEXITCODE -ne 0) { throw "A etapa '$nome' falhou (código $LASTEXITCODE). Veja outputs\logs\." }
}

Etapa 'Conferir conexão com o MongoDB' {
    & $python -c "from observatorio_vagas.config import get_settings; from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB; print('Mongo OK:', ConexaoMongoDB(get_settings()).banco['anuncios'].estimated_document_count(), 'anuncios')"
}
Etapa 'Apagar a Gupy do Mongo' {
    & $python scripts\remover_fonte_do_mongo.py --dominio gupy.io --confirmar *> "$logs\${hoje}_remover_gupy.log"
}
Etapa 'Releitura das coletas desde 01/10 (demora)' {
    & $python scripts\processar_lote.py --catalogo config\lote_10mil_triado\catalogo_fontes.csv `
        --coletado-desde 2026-10-01T00:00:00-03:00 --cache-extracao --confirmar *> "$logs\${hoje}_releitura.log"
}
Etapa 'Apagar empresas sem uso' {
    & $python scripts\remover_empresas_orfas.py --confirmar *> "$logs\${hoje}_empresas_orfas.log"
}
$lote = "outputs\payloads\${hoje}_republicaveis"
Etapa 'Gerar o lote de vagas prontas para publicar' {
    & $python scripts\preparar_fila_empregos.py --catalogo config\lote_10mil_triado\catalogo_fontes.csv `
        --somente-catalogo --limite 50000 --saida-json "$lote\relatorio.json" --diretorio-payloads "$lote\p" `
        *> "$logs\${hoje}_lote.log"
}
Write-Host ""
Write-Host "Pronto. Lote em $lote (abra payloads_unificados.json dentro da pasta p\lote-...)."
