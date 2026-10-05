# docchat 서버 실행 (외부 공개용): 노트북 안에서만 열고(127.0.0.1:8000), 바깥 연결은 Tailscale Funnel이 맡는다.
# 서버가 멈추면 10초 뒤 다시 켠다. 기록은 deploy\logs\server-날짜.log
# 사용: powershell -ExecutionPolicy Bypass -File C:\work\docchat\deploy\start-server.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $here '..\server'
$logs = Join-Path $here 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
Set-Location $server
while ($true) {
    $log = Join-Path $logs ("server-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))
    Add-Content $log ("`n===== 서버 시작 {0} =====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8
    & .\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000 --proxy-headers *>> $log
    Add-Content $log ("===== 서버 멈춤 {0} (종료 코드 {1}) — 10초 뒤 다시 시작 =====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LASTEXITCODE) -Encoding UTF8
    Start-Sleep 10
}
