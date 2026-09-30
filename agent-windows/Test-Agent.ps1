$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$testCertificate = $null
$temporaryConfig = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString() + '.json')
try {
    dotnet build -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Compilação falhou.' }
    dotnet run -c Release --no-build -- protocol-test
    if ($LASTEXITCODE -ne 0) { throw 'Verificação de protocolo falhou.' }
    $testCertificate = New-SelfSignedCertificate -Subject 'CN=DocPronto Temporary Agent Test' -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 -KeyUsage DigitalSignature -NotAfter (Get-Date).AddDays(1)
    dotnet run -c Release --no-build -- test --thumbprint $testCertificate.Thumbprint --store CurrentUser
    if ($LASTEXITCODE -ne 0) { throw 'Teste de posse da chave falhou.' }
    '{"allowedHosts":[],"allowedPathPrefixes":[]}' | Set-Content -Path $temporaryConfig -Encoding utf8
    # Destinos devem ser recusados antes de selecionar certificado ou abrir rede.
    dotnet run -c Release --no-build -- fetch --thumbprint $testCertificate.Thumbprint --config $temporaryConfig --url 'http://example.com/file' --output 'never-created.xml'
    if ($LASTEXITCODE -ne 2) { throw 'HTTP inseguro não foi recusado.' }
    dotnet run -c Release --no-build -- fetch --thumbprint $testCertificate.Thumbprint --config $temporaryConfig --url 'https://example.com/file' --output 'never-created.xml'
    if ($LASTEXITCODE -ne 2) { throw 'Host fora da lista não foi recusado.' }
    if (Test-Path 'never-created.xml') { throw 'Arquivo inesperado foi criado.' }
    Write-Host 'PASS: compilação, desafio RSA e bloqueios HTTP/host. A3 e mTLS remoto continuam pendentes.'
} finally {
    if ($null -ne $testCertificate) { Remove-Item -Path ('Cert:\CurrentUser\My\' + $testCertificate.Thumbprint) -DeleteKey }
    if (Test-Path $temporaryConfig) { Remove-Item $temporaryConfig }
    Pop-Location
}
