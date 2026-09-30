param([ValidateSet('win-x64', 'win-arm64')][string]$Runtime = 'win-x64')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw 'Instale o SDK .NET 10 da Microsoft para compilar.' }
    dotnet publish '.\DocPronto.CertificateAgent.csproj' -c Release -r $Runtime --self-contained true -p:PublishSingleFile=true -o '.\dist'
    if ($LASTEXITCODE -ne 0) { throw 'Compilação/publicação falhou.' }
    Copy-Item '.\INSTALAR-AGENTE.cmd' '.\dist\INSTALAR-AGENTE.cmd' -Force
    Copy-Item '.\Start-Agent.cmd' '.\dist\Start-Agent.cmd' -Force
    Copy-Item '.\config.example.json' '.\dist\config.example.json' -Force
    Copy-Item '.\README.md' '.\dist\README.md' -Force
    Copy-Item '.\licenses' '.\dist\licenses' -Recurse -Force
    Write-Host 'Executável independente e iniciador disponíveis em dist.'
} finally { Pop-Location }
