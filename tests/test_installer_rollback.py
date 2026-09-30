"""Synthetic installer transactions: no real install, processes, ACLs or shortcuts."""
import os
from pathlib import Path
import subprocess
import pytest

INSTALLER=Path(__file__).resolve().parents[1]/'scripts/install-local.ps1'


@pytest.mark.parametrize('scenario',['check-failure','health-failure','success-seed','success-existing','success-no-seed'])
def test_installer_transaction_and_background(tmp_path,scenario):
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    script=tmp_path/'check.ps1'
    script.write_text(r'''
param($Installer,$TestRoot,$Scenario)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell syntax error'}
# Load functions only, never the installer's entrypoint or UI.
foreach($node in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){
    $text=$node.Extent.Text
    if($node.Name -eq 'Install-Local'){
        # Platform integration is excluded explicitly; transaction code is real.
        $text=[regex]::Replace($text,'(?s)    [$]acl=New-Object Security.AccessControl.DirectorySecurity.*?    [$]guard=', '    $guard=')
        $text=[regex]::Replace($text,'(?s)        [$]shell=New-Object -ComObject WScript.Shell.*?        Remove-Item -LiteralPath', '        Remove-Item -LiteralPath')
        $text=$text.Replace('$failure=$_.Exception.Message','$script:originalError=$_;$failure=$_.Exception.Message')
    }
    Invoke-Expression $text
}
$base=Join-Path $TestRoot 'state';$source=Join-Path $TestRoot 'package';$Background=$true
$previousName='release-'+('a'*32)
$old=Join-Path (Join-Path $base 'releases') $previousName
foreach($path in @($source,(Join-Path $base 'data'),(Join-Path $old 'scripts'))){New-Item -ItemType Directory -Force -Path $path | Out-Null}
[IO.File]::WriteAllText((Join-Path $base 'active.txt'),$previousName)
[IO.File]::WriteAllText((Join-Path $base 'data/app.db'),'OLD-DATABASE')
[IO.File]::WriteAllText((Join-Path $old 'scripts/local-launch.ps1'),'throw "Browser must not open"')
foreach($dir in @('app','runtime','tools','agent-windows','scripts','licenses-local')){New-Item -ItemType Directory -Force -Path (Join-Path $source $dir) | Out-Null}
foreach($file in @('run.py','local_runtime.py','local_updates.py','local_backups.py','scripts/local-launch.ps1')){[IO.File]::WriteAllText((Join-Path $source $file),'SYNTHETIC')}
$hash=(Get-FileHash -LiteralPath (Join-Path $source 'local_updates.py') -Algorithm SHA256).Hash
@{files=@(@{path='local_updates.py';sha256=$hash})} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $source 'local-manifest.json')
if($Scenario -ne 'success-no-seed'){[IO.File]::WriteAllText((Join-Path $source 'update-source.json'),'{"source":"test-channel","automatic":true}')}
if($Scenario -eq 'success-existing'){
    New-Item -ItemType Directory -Force -Path (Join-Path $base 'updates') | Out-Null
    [IO.File]::WriteAllText((Join-Path $base 'updates/settings.json'),'{"source":"custom-channel","automatic":false}')
}
$script:operations=New-Object 'Collections.Generic.List[string]'
$script:starts=New-Object 'Collections.Generic.List[string]'
$script:shortcutSaves=0
# Startup shortcut is created after health confirmation, outside the desktop
# shortcut block excluded above. Stub COM in memory; never write a real .lnk.
$shell=[pscustomobject]@{}
$shell | Add-Member -MemberType ScriptMethod -Name CreateShortcut -Value {
    param($path)
    $shortcut=[pscustomobject]@{TargetPath='';Arguments='';WorkingDirectory=''}
    $shortcut | Add-Member -MemberType ScriptMethod -Name Save -Value {$script:shortcutSaves++}
    return $shortcut
}
function Invoke-LocalPython($Python,$Runner,$Operation,$Directory,$LogBase){
    $script:operations.Add($Operation)
    if($Operation -eq 'backup'){
        $backup=Join-Path $base 'backups/synthetic'
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        Copy-Item -LiteralPath (Join-Path $base 'data/app.db') -Destination (Join-Path $backup 'app.db')
    }
    if($Operation -eq 'check' -and $Scenario -eq 'check-failure'){throw 'Synthetic check failure'}
}
function Start-LocalRelease($Directory){
    $script:starts.Add($Directory)
    if($Directory -ne $old){
        [IO.File]::WriteAllText((Join-Path $base 'data/app.db'),'NEW-SCHEMA-DATABASE')
        [IO.File]::WriteAllText((Join-Path $base 'data/app.db-wal'),'NEW-WAL')
    }
    $mock=[pscustomobject]@{Directory=$Directory}
    $mock | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {param($timeout) return $true}
    return $mock
}
function Wait-LocalHealthy($State,$Directory,$Process){
    if($Directory -ne $old -and $Scenario -eq 'health-failure'){throw 'Synthetic health failure'}
}
$failed=$false
try {Install-Local | Out-Null} catch {$failed=$true;$message=$_.Exception.Message+' '+$script:originalError.InvocationInfo.PositionMessage}
if(($script:operations -join ',') -ne 'backup,check'){throw 'Backup must precede check'}
$active=[IO.File]::ReadAllText((Join-Path $base 'active.txt'))
if($Scenario -like '*failure'){
    if(!$failed){throw 'Failure expected'}
    if($active -ne $previousName){throw ('Active release not restored: '+$message)}
    if($script:starts[$script:starts.Count-1] -ne $old){throw ('Old release not restarted: '+$message)}
    if([IO.File]::ReadAllText((Join-Path $base 'data/app.db')) -ne 'OLD-DATABASE'){throw ('Database not restored: '+$message)}
    if($Scenario -eq 'health-failure'){
        $preserved=@(Get-ChildItem -LiteralPath (Join-Path $base 'backups') -Directory -Filter 'rollback-failed-*')
        if($preserved.Count -ne 1){throw 'Failed database not retained'}
        if([IO.File]::ReadAllText((Join-Path $preserved[0].FullName 'app.db')) -ne 'NEW-SCHEMA-DATABASE'){throw 'Wrong failed DB snapshot'}
        if(!(Test-Path -LiteralPath (Join-Path $preserved[0].FullName 'app.db-wal'))){throw 'Failed WAL not retained'}
        if(Test-Path -LiteralPath (Join-Path $base 'data/app.db-wal')){throw 'Stale WAL survived restore'}
    }
}else{
    if($failed){throw $message}
    if($active -eq $previousName){throw 'New release not activated'}
    $release=Join-Path (Join-Path $base 'releases') $active
    foreach($file in @('local_updates.py','local_backups.py','local-manifest.json')){if(!(Test-Path -LiteralPath (Join-Path $release $file))){throw ('Missing '+$file)}}
    $settings=Join-Path $base 'updates/settings.json'
    if($Scenario -eq 'success-no-seed'){if(Test-Path -LiteralPath $settings){throw 'Settings invented without seed'}}
    else{
        $saved=Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json
        $expected=if($Scenario -eq 'success-existing'){'custom-channel'}else{'test-channel'}
        if($saved.source -ne $expected){throw 'User settings overwritten or seed not applied'}
    }
}
if(Test-Path -LiteralPath (Join-Path $base 'stop.request')){throw 'Stop flag leaked'}
$expectedSaves=if($Scenario -like '*failure'){0}else{1}
if($script:shortcutSaves -ne $expectedSaves){throw 'Startup shortcut must be saved only after successful health check'}
'PASS '+$Scenario
''',encoding='utf-8')
    test_env=os.environ.copy()
    test_env['PSModulePath']=str(powershell.parent/'Modules')
    result=subprocess.run([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),str(INSTALLER),str(tmp_path),scenario],capture_output=True,text=True,timeout=30,env=test_env)
    assert result.returncode==0,result.stdout+'\n'+result.stderr


def test_installer_health_identity_and_hidden_process_contract():
    text=INSTALLER.read_text(encoding='utf-8')
    assert "$status.local_instance -eq $ready.instance" in text
    assert "$ready.root -eq $Directory" in text
    assert "$request.Proxy=$null" in text
    assert '-WindowStyle Hidden -PassThru' in text
    assert 'Stop-Process' not in text
    assert 'if($Worker -or $Background)' in text
    assert 'if(!$Background)' in text
