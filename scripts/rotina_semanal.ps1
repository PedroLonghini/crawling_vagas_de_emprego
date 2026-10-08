# Varredura semanal: atalho para scripts/rotina_semanal.py (que roda também no Mac).
#
#   powershell -ExecutionPolicy Bypass -File scripts\rotina_semanal.ps1
#
# Para agendar todo domingo às 02:00 (Agendador de Tarefas do Windows):
#   schtasks /Create /TN "ObservatorioVagas\RotinaSemanal" /SC WEEKLY /D SUN /ST 02:00 /TR "powershell -ExecutionPolicy Bypass -File \"<pasta do projeto>\scripts\rotina_semanal.ps1\""
#
# O que ela faz está no topo de scripts/rotina_semanal.py. No Mac:
#   .venv/bin/python scripts/rotina_semanal.py   (agendar com launchd ou cron)

$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $raiz '.venv\Scripts\python.exe') scripts\rotina_semanal.py @args
exit $LASTEXITCODE
