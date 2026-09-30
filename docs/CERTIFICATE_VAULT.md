# Cofre A1 local

No Windows, a chave Fernet do cofre é protegida por DPAPI da conta Windows
atual. Arquivos legados são migrados no primeiro uso, após validar a chave e
testar proteção e recuperação. A troca é atômica e preserva a chave lógica:
os certificados já cifrados no banco não precisam ser regravados. Falhas não
geram uma chave substituta nem apagam o arquivo existente.

Backups novos do cofre dependem do perfil Windows original para abrir os A1.
Ao restaurar em outra máquina ou conta, os documentos fiscais permanecem
restauráveis, mas os A1 precisam ser cadastrados novamente. Não substitua a
chave de uma instalação existente sem planejar o recadastro dos certificados.
Backups anteriores podem conter a chave Fernet sem proteção DPAPI; mantenha-os
em armazenamento restrito. A atualização não remove esses backups.

Fora do Windows, novos cofres exigem a variável secreta
`DOCPRONTO_CERTIFICATE_VAULT_KEY`, uma chave Fernet válida gerenciada fora do
diretório de dados. A aplicação não grava essa variável em arquivo. Cofres
legados não Windows continuam legíveis; sua chave precisa ser protegida pelo
administrador. DPAPI não protege contra processos maliciosos executados na
mesma conta Windows.

O PFX e sua senha continuam necessários em memória para encaminhamento ao
conector autorizado. A proteção em repouso não substitui HTTPS quando o
conector está em outro computador, nem o controle de vínculo com a empresa.
