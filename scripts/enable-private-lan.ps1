# Optional: run as administrator only on the chosen central Windows computer.
# Does not enable LAN in DocPronto; that remains a separate administrator setting.
#Requires -RunAsAdministrator
param([switch]$Disable)
$ErrorActionPreference='Stop'
$ruleName='DocPronto-Local-Private-LAN-8080'
if($Disable) {
    Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Output 'Regra de rede local do DocPronto removida.'
    exit 0
}
$existing=Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if($existing){$existing | Remove-NetFirewallRule}
New-NetFirewallRule -Name $ruleName -DisplayName 'DocPronto Local - rede privada da empresa' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -RemoteAddress LocalSubnet -Profile Private -EdgeTraversalPolicy Block | Out-Null
Write-Output 'Acesso TCP 8080 permitido somente para LocalSubnet no perfil Privado. Nenhum roteador ou acesso pela internet foi configurado.'
