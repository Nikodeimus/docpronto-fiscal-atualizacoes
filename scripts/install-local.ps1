param([switch]$Worker,[switch]$Background)
$ErrorActionPreference='Stop'
$source=Split-Path -Parent $PSScriptRoot
$base=Join-Path $env:LOCALAPPDATA 'DocProntoLocal'
function Invoke-LocalPython([string]$Python,[string]$Runner,[string]$Operation,[string]$Directory,[string]$LogBase,[int]$TimeoutSeconds=120) {
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$Python
    $info.Arguments='-u "'+$Runner+'" '+$Operation
    $info.WorkingDirectory=$Directory
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true
    $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process
    $process.StartInfo=$info
    $outputTask=$null;$errorTask=$null
    try {
        [void]$process.Start()
        $process.StandardInput.Close()
        $outputTask=$process.StandardOutput.ReadToEndAsync()
        $errorTask=$process.StandardError.ReadToEndAsync()
        $clock=[Diagnostics.Stopwatch]::StartNew()
        while(!$process.HasExited) {
            if($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                $process.Kill()
                throw ('Etapa '+$Operation+' excedeu '+$TimeoutSeconds+' segundos. Consulte '+$LogBase+'-error.log')
            }
            Start-Sleep -Milliseconds 200
            $process.Refresh()
        }
        $code=$process.ExitCode
        $stdout=$outputTask.GetAwaiter().GetResult()
        $stderr=$errorTask.GetAwaiter().GetResult()
        [IO.File]::WriteAllText(($LogBase+'.log'),$stdout)
        [IO.File]::WriteAllText(($LogBase+'-error.log'),$stderr)
        if($stdout){Write-Output $stdout.TrimEnd()}
        if($code -ne 0){throw ('Etapa '+$Operation+' falhou (codigo '+$code+'). '+$stderr)}
    } catch {
        [IO.File]::WriteAllText(($LogBase+'-error.log'),$_.Exception.Message)
        throw
    } finally {$process.Dispose()}
}
function Stop-LocalInstance([string]$State,[int]$TimeoutSeconds=60) {
    [IO.File]::WriteAllText((Join-Path $State 'stop.request'),'stop')
    for ($n=0;$n -lt $TimeoutSeconds;$n++) {
        try { return [IO.File]::Open((Join-Path $State 'running.lock'),'OpenOrCreate','ReadWrite','None') }
        catch { Start-Sleep -Seconds 1 }
    }
    throw 'A instalacao local ainda esta em execucao. Nenhum processo externo foi encerrado; aguarde e tente novamente.'
}
function Set-ActiveRelease([string]$State,[string]$Name) {
    $active=Join-Path $State 'active.txt'
    if (!$Name) { if(Test-Path -LiteralPath $active){Remove-Item -LiteralPath $active};return }
    if($Name -notmatch '^release-[a-f0-9]{32}$'){throw 'Identificador de release invalido.'}
    $pending=Join-Path $State 'active.pending'
    [IO.File]::WriteAllText($pending,$Name)
    if(Test-Path -LiteralPath $active){[IO.File]::Replace($pending,$active,(Join-Path $State 'active.previous'))}
    else{[IO.File]::Move($pending,$active)}
}
function Start-LocalRelease([string]$Directory) {
    return Start-Process -FilePath (Join-Path $Directory 'runtime\pythonw.exe') -ArgumentList @(('"'+(Join-Path $Directory 'local_runtime.py')+'"'),'serve') -WorkingDirectory $Directory -WindowStyle Hidden -PassThru
}
function Wait-LocalHealthy([string]$State,[string]$Directory,$Process,[int]$TimeoutSeconds=90) {
    for($n=0;$n -lt $TimeoutSeconds;$n++) {
        $Process.Refresh()
        if($Process.HasExited){break}
        try {
            $ready=Get-Content -LiteralPath (Join-Path $State 'ready.json') -Raw | ConvertFrom-Json
            if($ready.root -eq $Directory -and $ready.instance) {
                $request=[Net.WebRequest]::Create('http://127.0.0.1:8080/api/status')
                $request.Proxy=$null;$request.Timeout=2000
                $response=$request.GetResponse()
                try {
                    $reader=New-Object IO.StreamReader($response.GetResponseStream())
                    try {$status=$reader.ReadToEnd() | ConvertFrom-Json} finally {$reader.Dispose()}
                    if($status.local_instance -eq $ready.instance -and $status.mode -eq 'windows-local'){return}
                } finally {$response.Dispose()}
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw 'O site e a fila nao confirmaram a inicializacao. Consulte a pasta logs.'
}
function Restore-DatabaseSnapshot([string]$State,[string]$Snapshot) {
    if(!(Test-Path -LiteralPath $Snapshot -PathType Leaf)){throw 'Backup anterior indisponivel; restauracao automatica interrompida.'}
    $data=Join-Path $State 'data'
    $failed=Join-Path (Join-Path $State 'backups') ('rollback-failed-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $failed -Force | Out-Null
    # Caller holds running.lock: supervisor and children are stopped. Preserve
    # failed schema/data and sidecars before restoring the pre-update snapshot.
    foreach($file in @('app.db','app.db-wal','app.db-shm')) {
        $path=Join-Path $data $file
        if(Test-Path -LiteralPath $path){Copy-Item -LiteralPath $path -Destination (Join-Path $failed $file)}
    }
    $pending=Join-Path $data 'app.db.rollback-pending'
    Copy-Item -LiteralPath $Snapshot -Destination $pending -Force
    $database=Join-Path $data 'app.db'
    if(Test-Path -LiteralPath $database){[IO.File]::Replace($pending,$database,(Join-Path $failed 'app.db-before-restore'))}else{[IO.File]::Move($pending,$database)}
    foreach($file in @('app.db-wal','app.db-shm')) {
        $path=Join-Path $data $file
        if(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path}
    }
    Write-Output ('Banco anterior restaurado; dados da tentativa malsucedida preservados em '+$failed)
}
function Install-Local {
    if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'Este pacote requer Windows x64 (Intel ou AMD).' }
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    (Get-Item -LiteralPath $base).SetAccessControl($acl)
    $guard=[IO.File]::Open((Join-Path $base 'install.lock'),'OpenOrCreate','ReadWrite','None')
    $running=$null;$previous=$null;$shutdownRequested=$false;$newStarted=$false;$snapshot=$null;$proc=$null
    try {
        $active=Join-Path $base 'active.txt'
        if(Test-Path -LiteralPath $active){
            $previous=[IO.File]::ReadAllText($active).Trim()
            if($previous -notmatch '^release-[a-f0-9]{32}$'){throw 'Instalacao anterior invalida; preserve os dados e contate o suporte.'}
        }
        Write-Output 'Conferindo integridade do pacote...'
        $manifest=Get-Content -LiteralPath (Join-Path $source 'local-manifest.json') -Raw | ConvertFrom-Json
        if(!$manifest.files){throw 'Manifesto de integridade vazio.'}
        foreach ($entry in $manifest.files) {
            $path=[IO.Path]::GetFullPath((Join-Path $source $entry.path))
            $prefix=[IO.Path]::GetFullPath($source).TrimEnd('\')+'\'
            if(!$path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Caminho invalido no manifesto.'}
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) { throw ('Arquivo incompleto ou alterado: '+$entry.path+'. Extraia o ZIP novamente.') }
        }
        Write-Output 'Encerrando somente a instalacao local anterior, se estiver aberta...'
        $shutdownRequested=$true
        $running=Stop-LocalInstance -State $base
        $name='release-'+[Guid]::NewGuid().ToString('N')
        $dest=Join-Path (Join-Path $base 'releases') $name
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Write-Output 'Copiando programa, Python, leitor de PDF e OCR...'
        foreach ($item in @('app','runtime','tools','agent-windows','scripts','run.py','local_runtime.py','local_updates.py','local_backups.py','local-manifest.json','licenses-local')) {
            Copy-Item -LiteralPath (Join-Path $source $item) -Destination $dest -Recurse -Force
        }
        if(Test-Path -LiteralPath (Join-Path $source 'update-source.json')){
            Copy-Item -LiteralPath (Join-Path $source 'update-source.json') -Destination $dest -Force
        }
        $python=Join-Path $dest 'runtime\python.exe'
        $runner=Join-Path $dest 'local_runtime.py'
        # Backup precedes check/configure and all schema initialization in serve.
        Write-Output 'Salvando copia do banco local antes de atualizar...'
        $hadDatabase=Test-Path -LiteralPath (Join-Path $base 'data\app.db')
        $backupRoot=Join-Path $base 'backups'
        $before=@(Get-ChildItem -LiteralPath $backupRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {$_.FullName})
        Invoke-LocalPython -Python $python -Runner $runner -Operation backup -Directory $dest -LogBase (Join-Path $base 'install-backup')
        if($hadDatabase){
            $newBackups=@(Get-ChildItem -LiteralPath $backupRoot -Directory | Where-Object {$_.FullName -notin $before -and (Test-Path -LiteralPath (Join-Path $_.FullName 'app.db'))})
            if($newBackups.Count -ne 1){throw 'Nao foi possivel confirmar o backup anterior. Atualizacao interrompida.'}
            $snapshot=Join-Path $newBackups[0].FullName 'app.db'
        }
        Write-Output 'Verificando componentes...'
        Invoke-LocalPython -Python $python -Runner $runner -Operation check -Directory $dest -LogBase (Join-Path $base 'install-check')
        Set-ActiveRelease -State $base -Name $name
        Copy-Item -LiteralPath (Join-Path $dest 'scripts\local-launch.ps1') -Destination (Join-Path $base 'local-launch.ps1') -Force
        $shell=New-Object -ComObject WScript.Shell
        $desktop=[Environment]::GetFolderPath('Desktop')
        foreach($pair in @(@('DocPronto Local','start'),@('Encerrar DocPronto Local','stop'))) {
            $shortcut=$shell.CreateShortcut((Join-Path $desktop ($pair[0]+'.lnk')))
            $shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $shortcut.Arguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+(Join-Path $base 'local-launch.ps1')+'" -Mode '+$pair[1]
            $shortcut.WorkingDirectory=$base;$shortcut.Save()
        }
        $shortcut=$shell.CreateShortcut((Join-Path $desktop 'Dados do DocPronto Local.lnk'))
        $shortcut.TargetPath=$base;$shortcut.Save()
        $shortcut=$shell.CreateShortcut((Join-Path $desktop 'Instalar conector DocPronto.lnk'))
        $shortcut.TargetPath=Join-Path $dest 'agent-windows\DocPronto-Instalar.exe';$shortcut.WorkingDirectory=Split-Path $shortcut.TargetPath;$shortcut.Save()
        Remove-Item -LiteralPath (Join-Path $base 'stop.request') -ErrorAction SilentlyContinue
        $running.Dispose();$running=$null
        Write-Output 'Iniciando site e fila local...'
        $newStarted=$true
        $proc=Start-LocalRelease -Directory $dest
        Wait-LocalHealthy -State $base -Directory $dest -Process $proc
        # Start the fiscal server at sign-in without opening a browser. The
        # stable launcher resolves active.txt after every future update.
        $startup=[Environment]::GetFolderPath('Startup')
        $autostart=$shell.CreateShortcut((Join-Path $startup 'DocPronto Fiscal Local.lnk'))
        $autostart.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $autostart.Arguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+(Join-Path $base 'local-launch.ps1')+'" -Mode serve'
        $autostart.WorkingDirectory=$base;$autostart.Save()
        $seed=Join-Path $dest 'update-source.json'
        $settings=Join-Path $base 'updates\settings.json'
        if((Test-Path -LiteralPath $seed) -and !(Test-Path -LiteralPath $settings)){
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $settings) | Out-Null
            # FileMode.CreateNew prevents overwriting a concurrent user setting.
            try {
                $settingsFile=[IO.File]::Open($settings,'CreateNew','Write','None')
                try {$seedBytes=[IO.File]::ReadAllBytes($seed);$settingsFile.Write($seedBytes,0,$seedBytes.Length)} finally {$settingsFile.Dispose()}
            } catch [IO.IOException] {if(!(Test-Path -LiteralPath $settings)){throw}}
        }
        if(!$Background){& (Join-Path $base 'local-launch.ps1') -Mode start}
        Write-Output 'CONCLUIDO. Use o atalho DocPronto Local. No primeiro acesso, o Bloco de Notas mostra seu codigo de instalacao.'
    } catch {
        $failure=$_.Exception.Message
        if($shutdownRequested){
            try {
                if($null -eq $running){$running=Stop-LocalInstance -State $base}
                # Do not restart the previous release while a delayed new
                # supervisor could still acquire the lock and initialize schema.
                if($null -ne $proc -and !$proc.WaitForExit(10000)){throw 'O novo supervisor ainda nao encerrou; restauracao interrompida com seguranca.'}
                if($newStarted -and $snapshot){Restore-DatabaseSnapshot -State $base -Snapshot $snapshot}
                Set-ActiveRelease -State $base -Name $previous
                if($previous){
                    $old=Join-Path (Join-Path $base 'releases') $previous
                    Copy-Item -LiteralPath (Join-Path $old 'scripts\local-launch.ps1') -Destination (Join-Path $base 'local-launch.ps1') -Force
                    Remove-Item -LiteralPath (Join-Path $base 'stop.request') -ErrorAction SilentlyContinue
                    $running.Dispose();$running=$null
                    $oldProcess=Start-LocalRelease -Directory $old
                    Wait-LocalHealthy -State $base -Directory $old -Process $oldProcess
                    Write-Output 'Versao anterior reiniciada e validada.'
                } else {
                    Remove-Item -LiteralPath (Join-Path $base 'stop.request') -ErrorAction SilentlyContinue
                }
            } catch {
                throw ($failure+' Recuperacao automatica incompleta: '+$_.Exception.Message+' Preserve os dados e backups; nenhum processo de outra aplicacao foi encerrado.')
            }
        }
        throw $failure
    } finally {
        if($null -ne $running){$running.Dispose()}
        $guard.Dispose()
    }
}
if($Worker -or $Background){try{Install-Local}catch{Write-Output ('FALHA: '+$_.Exception.Message);exit 1};exit 0}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form=New-Object Windows.Forms.Form
$form.Text='DocPronto Local - instalar sem Docker (correcao 1)';$form.Size=New-Object Drawing.Size(720,440);$form.StartPosition='CenterScreen'
$label=New-Object Windows.Forms.Label
$label.Text='Instalacao local para Windows x64. Dados separados do Docker. Nao migra cadastros antigos.'
$label.Location=New-Object Drawing.Point(15,15);$label.Size=New-Object Drawing.Size(670,40);$form.Controls.Add($label)
$button=New-Object Windows.Forms.Button
$button.Text='Instalar / Atualizar';$button.Location=New-Object Drawing.Point(15,60);$button.Size=New-Object Drawing.Size(190,40);$form.Controls.Add($button)
$box=New-Object Windows.Forms.TextBox
$box.Multiline=$true;$box.ReadOnly=$true;$box.ScrollBars='Vertical';$box.Location=New-Object Drawing.Point(15,115);$box.Size=New-Object Drawing.Size(670,270);$box.Anchor='Top,Bottom,Left,Right';$form.Controls.Add($box)
$script:job=$null
$installerPath=$PSCommandPath
$button.Add_Click({
    $button.Enabled=$false
    $script:job=Start-Job -ScriptBlock {param($p) & $p -Worker} -ArgumentList $installerPath
})
$timer=New-Object Windows.Forms.Timer;$timer.Interval=500
$timer.Add_Tick({
    if($null -ne $script:job){
        Receive-Job $script:job -ErrorAction Continue 2>&1 | ForEach-Object {$box.AppendText($_.ToString()+[Environment]::NewLine)}
        if($script:job.State -in @('Completed','Failed','Stopped')){
            if($script:job.State -eq 'Failed'){$box.AppendText('Falha ao executar instalador: '+$script:job.ChildJobs[0].JobStateInfo.Reason)}
            Remove-Job $script:job;$script:job=$null;$button.Enabled=$true
        }
    }
})
$form.Add_FormClosing({param($sender,$event) if($null -ne $script:job){$event.Cancel=$true;[Windows.Forms.MessageBox]::Show('Aguarde a instalacao terminar.','DocPronto')|Out-Null}})
$timer.Start();[void]$form.ShowDialog();$timer.Dispose()
