# DocPronto Agente 1.2 — Windows x64

## Instalar e conectar

Extraia o ZIP e abra INSTALAR-AGENTE.cmd. O programa é instalado na pasta local do seu usuário e recebe um atalho na área de trabalho. Não precisa instalar .NET. Encerre a versão anterior pelo ícone na bandeja antes de atualizar. O binário ainda não possui assinatura Authenticode.

1. No DocPronto, escolha a empresa e abra Certificados → Conectar computador.
2. No aplicativo, informe o endereço, o código temporário e o nome do computador.
3. Clique Conectar nova empresa. Repita para outras empresas: cada conexão é salva separadamente.
4. Use Conectar selecionada ou Pausar selecionada. Marque Abrir ao entrar no Windows se desejar.

Fechar a janela mantém o aplicativo na bandeja. Use Sair e desconectar para encerrá-lo. Start-Agent.cmd ou o executável também abrem a janela, sem instalar. Configurações ficam em %LOCALAPPDATA%\DocPronto\profiles e são protegidas por ACL do usuário. A antiga configuração padrão agent.json também é carregada. Configurações manuais em outros caminhos não são importadas automaticamente.

## Certificados e consulta

O token/cartão precisa do driver CSP/KSP adequado e deve estar conectado. A1 deve estar instalado no repositório Windows. O provedor pode pedir PIN na sessão do usuário; o programa não recebe o PIN por parâmetro nem exporta a chave privada.

O agente recebe duas operações específicas da central pareada: teste de posse de chave e distribuição NF-e via SEFAZ. Para distribuição, usa SOAP/mTLS em endpoint fixo de produção e retorna a resposta para importação. Não aceita endereço remoto arbitrário nessa tarefa. Não faz manifestação automática. Consulta real SEFAZ, GUI, PIN e drivers precisam de validação com o certificado da empresa; a compilação e testes portáteis não comprovam essas integrações.

HTTP só é permitido para localhost/loopback. Central em outro computador requer HTTPS e domínio configurado. Certificados públicos, inventário, assinaturas de teste e documentos recebidos são enviados à central vinculada. Token de conexão não é passado na linha de comando. Os arquivos de configuração têm ACL exclusiva do usuário; isso não substitui proteção da conta e do computador.

## Linha de comando opcional

Ainda é possível usar:

```powershell
.\DocPronto.CertificateAgent.exe pair --config empresa-2.json
.\DocPronto.CertificateAgent.exe connect --config empresa-2.json
```

`connect` sozinho não é um comando PowerShell: precisa do nome do executável. O modo pair oferece pasta monitorada de XML/PDF opcional. A interface gráfica desta versão não configura essa pasta. O comando avançado fetch continua separado, exige allowlist de hosts/caminhos e não é acessível como tarefa remota.

## Compilar e testar

Use SDK .NET 10 e Build-Windows.ps1. Test-Agent.ps1 executa testes Windows com certificado temporário. `protocol-test` valida contratos, URLs, RSA/ECDSA e envelope SOAP sem contactar a SEFAZ. O projeto gráfico usa Windows Forms; o teste portátil de desenvolvimento exclui apenas a janela gráfica.

Binário x64 self-contained. Licenças do runtime em licenses. Tokens/senhas/certificados privados não estão incluídos no pacote.
