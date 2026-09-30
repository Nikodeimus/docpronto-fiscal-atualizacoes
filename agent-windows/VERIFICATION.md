# Verificação — correção A1 em Windows, 29/09/2026

Publicação Windows x64: sucesso, SDK .NET 10.0.401, self-contained, PublishSingleFile. Sem erros ou avisos apresentados.

Executável: 116181236 bytes. SHA-256: 09873eae463fb5e94c763f9d8f93c454edeba26a7bad74dec74bde64ed478987.

Testes executados em Windows com certificados sintéticos, sem consultar a SEFAZ:

- `tests/agent-pfx`: falha reproduzida com EphemeralKeySet; aprovado com UserKeySet. Assinatura com chave privada, rejeição de senha incorreta, remoção da chave temporária após Dispose, ausência de instalação no repositório e autenticação mútua TLS 1.2 local.
- `tests/agent-diagnostics`: classificações de erros e perfil TLS de produção preservados (validação normal do servidor, revogação ativa, sem redirecionamentos).
- Executável publicado, `protocol-test`: 26 verificações aprovadas.

O helper A1 usa UserKeySet sem PersistKeySet no Windows e EphemeralKeySet fora do Windows. A importação explícita pelo usuário na interface mantém seu comportamento existente. Não houve homologação de token A3 nem consulta fiscal externa nesta alteração.

## Persistência de resultados, 29/09/2026

- `outbox-test`: 9 verificações sintéticas aprovadas no build e no executável publicado: reinício após gravação, falha de rede, ACK ausente/perdido, exclusão somente após ACK, isolamento por pareamento/servidor, ausência de PFX/senha/token no arquivo e ACL individual Windows.
- `protocol-test`: 26 verificações aprovadas.
- Publicação Windows x64 self-contained/single-file: zero avisos e erros. SHA-256 do instalador: ee5ae34130be5ca5ea2209e9a959083353e4a6e609ac5bae919a56b5eaa44020.
- O resultado é salvo atomicamente antes do POST e reenviado antes do próximo poll. Enquanto a central não confirma `ok:true`, o arquivo permanece e novas consultas deste perfil aguardam. O backend deve aceitar resultados atrasados e ACK idempotente após validar agente/vínculo.
- Limite: queda antes de a resposta chegar por completo ou falha física do disco antes da gravação não são cobertas. Uma falha de gravação mantém o resultado em memória enquanto o processo continuar vivo, sem nova consulta.
- Nenhuma consulta fiscal real ou instalação foi feita nesta verificação.
