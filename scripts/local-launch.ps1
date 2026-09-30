param([ValidateSet('start','stop','serve')][string]$Mode='start')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $base=Join-Path $env:LOCALAPPDATA 'DocProntoLocal'
    $active=[IO.File]::ReadAllText((Join-Path $base 'active.txt')).Trim()
    if ($active -notmatch '^release-[a-f0-9]{32}$') { throw 'Instalacao local invalida. Execute o instalador novamente.' }
    $root=Join-Path (Join-Path $base 'releases') $active
    $python=Join-Path $root 'runtime\pythonw.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'Instale o DocPronto Local primeiro.' }
    Start-Process -FilePath $python -ArgumentList @(('"'+(Join-Path $root 'local_runtime.py')+'"'),$Mode) -WorkingDirectory $root -WindowStyle Hidden
} catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'DocPronto Local') | Out-Null }
