# docchat 외부 접속(HTTPS) 시작: server\.env 의 DUCKDNS_DOMAIN 으로 Caddy를 실행한다.
# 사용: PowerShell에서  .\deploy\run-caddy.ps1   (docchat 서버는 따로 켜 둔다)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $here '..\server\.env'
$line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match '^\s*DUCKDNS_DOMAIN\s*=' } | Select-Object -First 1
$domain = if ($line) { ($line -split '=', 2)[1].Trim() -replace '\.duckdns\.org$', '' } else { '' }
if (-not $domain) { throw "server\.env 에 DUCKDNS_DOMAIN 을 먼저 적어 주세요 (예: DUCKDNS_DOMAIN=myteam)" }
$env:DOCCHAT_DOMAIN = "$domain.duckdns.org"
New-Item -ItemType Directory -Force (Join-Path $here 'logs') | Out-Null
Write-Host "Caddy 시작: https://$env:DOCCHAT_DOMAIN  (끝내려면 Ctrl+C)"
Push-Location $here
try { caddy run --config Caddyfile --adapter caddyfile } finally { Pop-Location }
