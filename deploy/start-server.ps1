# docchat 서버 실행 (외부 공개용): 노트북 안에서만 열고(127.0.0.1:8000), 바깥 연결은 Tailscale Funnel이 맡는다.
# 서버가 멈추면 10초 뒤 다시 켠다. 기록은 deploy\logs\server-날짜.log (UTF-8)
# 사용: powershell -ExecutionPolicy Bypass -File C:\work\docchat\deploy\start-server.ps1
# 자동 실행: 작업 스케줄러 'docchat 서버' (로그인할 때)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = (Resolve-Path (Join-Path $here '..\server')).Path
$logs = Join-Path $here 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$utf8 = New-Object Text.UTF8Encoding $false
Set-Location $server
function Stop-OldServer {
    # 작업을 멈춰도 파이썬 서버가 남아 8000번을 잡고 있을 수 있다 → docchat 서버일 때만 정리
    foreach ($c in @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
        if ($p -and $p.CommandLine -match 'uvicorn server:app') { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    }
}
while ($true) {
    Stop-OldServer
    $log = Join-Path $logs ("server-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))
    [IO.File]::AppendAllText($log, ("`r`n===== 서버 시작 {0} =====`r`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')), $utf8)
    # cmd로 실행해야 파이썬이 쓰는 UTF-8 글자가 그대로 파일에 남는다 (PowerShell 5.1 리디렉션은 UTF-16으로 바꿈)
    cmd /c "`"$server\.venv\Scripts\python.exe`" -m uvicorn server:app --host 127.0.0.1 --port 8000 --proxy-headers >> `"$log`" 2>&1"
    [IO.File]::AppendAllText($log, ("===== 서버 멈춤 {0} (종료 코드 {1}) — 10초 뒤 다시 시작 =====`r`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LASTEXITCODE), $utf8)
    Start-Sleep 10
}
