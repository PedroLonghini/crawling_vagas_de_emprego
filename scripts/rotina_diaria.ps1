# Rotina diária: coleta das vagas novas (últimas 24h), lote para publicar e resumo.
#
#   powershell -ExecutionPolicy Bypass -File scripts\rotina_diaria.ps1
#
# Para agendar todo dia às 06:00 (Agendador de Tarefas do Windows):
#   schtasks /Create /TN "ObservatorioVagas\RotinaDiaria" /SC DAILY /ST 06:00 /TR "powershell -ExecutionPolicy Bypass -File \"<pasta do projeto>\scripts\rotina_diaria.ps1\""
#
# Precisa do túnel do MongoDB aberto. NÃO publica na API: só deixa o lote pronto e
# grava outputs\logs\<data>_resumo.txt com o resultado de cada etapa.

$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$env:PYTHONIOENCODING = 'utf-8'
$python = Join-Path $raiz '.venv\Scripts\python.exe'
$hoje = Get-Date -Format 'yyyy-MM-dd'
$logs = Join-Path $raiz 'outputs\logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$resumo = Join-Path $logs "${hoje}_resumo.txt"
"Rotina diária $hoje - início $(Get-Date -Format HH:mm)" | Set-Content -Encoding utf8 $resumo

function Registrar($texto) { $texto | Add-Content -Encoding utf8 $resumo; Write-Host $texto }

# 1. MongoDB acessível?
& $python -c "from observatorio_vagas.config import get_settings; from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB; ConexaoMongoDB(get_settings()).banco['anuncios'].estimated_document_count()" 2>$null
if ($LASTEXITCODE -ne 0) {
    Registrar "FALHA: o MongoDB não respondeu (túnel SSH fechado?). Nada foi coletado."
    exit 1
}

# A varredura semanal (ou outra coleta) rodando ao mesmo tempo disputaria os
# mesmos sites e o MongoDB: a diária fica para o dia seguinte.
$emAndamento = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*processar_lote.py*' -or $_.CommandLine -like '*rotina_semanal.py*' }
if ($emAndamento) {
    Registrar "PULADA: a varredura semanal (ou outra coleta) está rodando. Nada foi coletado."
    exit 0
}

# 2. Coleta das últimas 24h, até 200 vagas por fonte.
$inicio = Get-Date
& $python scripts\processar_lote.py --catalogo config\lote_10mil_triado\catalogo_fontes.csv --coletar --confirmar `
    --limite-anuncios 200 --janela-horas 24 *> "$logs\${hoje}_coleta.log"
$duracao = [int]((Get-Date) - $inicio).TotalMinutes
$fim = Get-Content "$logs\${hoje}_coleta.log" -Tail 3 -Encoding utf8
Registrar "Coleta: código $LASTEXITCODE em $duracao min | $($fim -join ' | ')"

# 3. Lote de vagas prontas para publicar.
$lote = "outputs\payloads\${hoje}_republicaveis"
& $python scripts\preparar_fila_empregos.py --catalogo config\lote_10mil_triado\catalogo_fontes.csv `
    --somente-catalogo --limite 50000 --saida-json "$lote\relatorio.json" --diretorio-payloads "$lote\p" `
    *> "$logs\${hoje}_lote.log"
if (Test-Path "$lote\relatorio.json") {
    $r = Get-Content "$lote\relatorio.json" -Raw -Encoding utf8 | ConvertFrom-Json
    Registrar ("Lote: {0} prontas para publicar, {1} barradas, {2} repetidas" -f $r.resumo.elegiveis, $r.resumo.bloqueados, $r.resumo.duplicadas)
} else {
    Registrar "FALHA: o lote não foi gerado. Veja $logs\${hoje}_lote.log"
}
Registrar "Fim $(Get-Date -Format HH:mm). Publicação na API: manual, após conferir o lote."
