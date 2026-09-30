# Plano de implementação — pendências do DocPronto Fiscal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a operação local recuperável e verificável, encerrando as pendências reais do parecer sobre a versão 1.8.19 sem regredir as correções da 1.8.33.

**Architecture:** Manter o servidor fiscal e o conector Windows separados. Centralizar atualização no fluxo local que já confere hashes, realiza backup e verifica saúde; preservar o cofre vinculado ao perfil Windows. Tratar evidência de laboratório, teste real e homologação como resultados distintos.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy/SQLite, JavaScript, .NET 10 Windows, PowerShell e DPAPI.

**Spec:** `docs/REVISAO-PARECER-1.8.19.md` e `docs/CERTIFICATE_VAULT.md`.

## Restrições gerais

- Trabalho no fiscal local, no repositório `docpronto-fiscal-atualizacoes`; não alterar o conversor.
- Não apagar arquivos fiscais nem retroceder NSU para repetir consultas.
- Não enfraquecer TLS, revogação, CSRF, isolamento de empresa ou autorização de manifestação.
- Não ativar Ciência automaticamente ao instalar uma atualização.
- Não publicar certificados, senhas, cofre ou dados reais em Git, logs ou testes.
- Respeitar políticas do Windows; a autorização para RemoteSigned nesta instalação não é autorização para mudar a política global.
- Os bloqueios de ferramenta anteriores devem ser tratados de forma explícita, sem executar a mesma ação por um caminho alternativo para contorná-los.

## Foco de revisão

- Restauração em outro perfil deve manter os documentos acessíveis e indicar que o cofre está indisponível; nunca criar chave nova silenciosamente.
- Falta de energia no meio da atualização deve permitir recuperação da última versão saudável.
- Token A3 removido ou PIN cancelado deve gerar estado compreensível, sem sucesso fictício ou repetição agressiva.
- Evento cancelado/rejeitado, chave igual em empresas diferentes e evento posterior ao filtro de data não podem produzir situação fiscal errada.
- Lista de notas local não prova completude do período; comparação exige uma fonte independente e chaves distintas.

## Ordem de execução

1. Recuperação do cofre e do servidor.
2. Atualização do conector e instalação compatível com a política Windows.
3. Diagnóstico e teste A3 em laboratório.
4. Assinatura de executáveis e processo de release.
5. Piloto comparativo de duas semanas com a TI.
6. Ampliação documental e manutenção modular, conforme necessidade comprovada.

Cada etapa terá commit próprio, testes e nota de versão. Não misturar alteração de infraestrutura, refatoração ampla e regra fiscal no mesmo pacote.

## Etapa 1 — Recuperação sem perda de dados

**Arquivos:** `app/certificate_vault.py`, `app/client_capture.py`, `local_backups.py`, `local_runtime.py`, `scripts/local-launch.ps1`, `scripts/install-local.ps1`; novos `tests/test_vault_recovery.py` e testes em `tests/test_runtime_recovery.py`.

**Interfaces propostas:** `vault_health(root) -> {state, protection, recovery_required}` sem devolver segredos; operação administrativa de recadastro deve ser separada da verificação somente leitura.

- [ ] Testar falha DPAPI de outro perfil, cofre corrompido e ausência de chave com A1 existentes.
- [ ] Exibir erro específico de cofre sem impedir consulta dos documentos já armazenados.
- [ ] Projetar recadastro explícito com inventário de A1 afetados e confirmação administrativa; nunca substituir chave antes de preservar o estado anterior e concluir a nova configuração.
- [ ] Testar recadastro parcial e falha de gravação sem invalidar os certificados restantes.
- [ ] Integrar o Agendador do Windows ao instalador de forma idempotente, com o mesmo usuário do perfil, respeitando encerramento voluntário, atualização e restauração.
- [ ] Testar reinício do Windows, saída do processo supervisor e encerramento intencional; impedir servidores duplicados na porta 8080.
- [ ] Registrar perda e recuperação de comunicação; ao voltar, mostrar quando a captura parou. Não prometer notificação pelo site quando o próprio site estiver desligado.

**Aceite:** restauração em perfil estrangeiro não causa perda de documentos, recadastro é explícito e o servidor volta após falha sem contrariar uma parada solicitada.

## Etapa 2 — Um único fluxo de atualização

**Arquivos:** `agent-windows/LocalMaintenance.cs`, `agent-windows/AgentDesktop.cs`, `local_updates.py`, `scripts/install-local.ps1`, `INSTALAR-LOCAL-SEM-DOCKER.cmd`, `INSTALAR-LOCAL-SEM-DOCKER.vbs` e `tests/test_installer_rollback.py`.

**Interface:** o comando Atualizar do conector abre a tela local de manutenção já existente; não aceita uma URL recebida de terceiros nem implementa outro extrator de ZIP.

- [ ] Resolver a disponibilidade da edição antes de repetir a alteração anteriormente bloqueada pela ferramenta; não contornar o bloqueio.
- [ ] Criar teste que demonstre a dependência indevida de `compose.yaml` e o destino local esperado.
- [ ] Remover a dependência Docker do caminho fiscal local e reutilizar a verificação de pacote/backup existente.
- [ ] Eliminar `ExecutionPolicy Bypass` dos iniciadores após testar instalação com RemoteSigned e comportamento sob AllSigned/políticas corporativas; política restritiva deve produzir orientação, não desativação.
- [ ] Testar pacote adulterado, download interrompido, disco insuficiente, falha de saúde, rollback e atualização repetida.
- [ ] Publicar pacote versionado somente após confirmar versão no manifesto, API e executáveis.

**Aceite:** atualização local não procura arquivos Docker, preserva dados e não muda políticas globais do computador.

## Etapa 3 — A3 sem resultados presumidos

**Arquivos:** `agent-windows/SefazClient.cs`, `agent-windows/SefazTransport.cs`, `agent-windows/ConnectionDiagnostic.cs`, `agent-windows/ProtocolSelfTests.cs`, `app/diagnostics.py`.

**Interface:** diagnóstico mantém separadas disponibilidade do certificado, acesso à chave e resposta real da SEFAZ.

- [ ] Resolver o bloqueio anterior da ferramenta antes de reaplicar a mudança de SelfTest.
- [ ] Testar que uma consulta comum não executa assinatura diagnóstica adicional; o teste explícito do certificado continua disponível.
- [ ] Preservar autenticação mTLS, validação do certificado remoto e verificação de revogação.
- [ ] Classificar erros de PIN cancelado, token removido, cadeia ausente e transporte indisponível sem registrar PIN/PFX/senha.
- [ ] Com um A3 e driver disponíveis, executar teste acompanhado pela TI e registrar resultado, horário, ambiente e códigos de retorno, sem segredos.
- [ ] Conferir consultas sucessivas e retomada depois de espera, respeitando o prazo efetivamente informado e os limites existentes.

**Dependência:** token A3 físico, driver e intervenção do titular quando houver PIN. Não é substituível por simulação.

**Aceite:** uma consulta real bem-sucedida e os cenários de interrupção documentados; não prometer funcionamento desassistido universal.

## Etapa 4 — Assinatura e distribuição verificável

**Arquivos:** `agent-windows/Build-Windows.ps1`, novo `scripts/verify-release.ps1`, documentação em `docs/RELEASES.md`.

- [ ] A TI define identidade de assinatura e disponibiliza certificado de assinatura de código ou serviço corporativo equivalente. Não usar A1/A3 fiscal de cliente.
- [ ] Assinar os artefatos Windows e aplicar carimbo de tempo com a infraestrutura escolhida.
- [ ] Verificar Authenticode depois da geração final; gerar hashes do manifesto somente após assinatura.
- [ ] Impedir publicação quando assinatura, versão ou hash esperado divergirem.
- [ ] Testar instalação em computador limpo e registrar alertas reais. Não prometer ausência de alertas de antivírus apenas por estar assinado.

**Dependência:** certificado/processo de assinatura; pode ter custo externo e não é criado gratuitamente pela edição do código.

## Etapa 5 — Piloto de duas semanas

**Arquivos:** novo `docs/PILOTO-FISCAL.md`; aproveitar `/api/history/compare` em `app/note_details.py`. Relatórios com dados reais ficam fora do Git.

- [ ] Escolher com a TI 2–3 CNPJs com A1 e pelo menos um com A3, período e fonte comparativa independente.
- [ ] Registrar chaves únicas, XMLs completos, resumos, cancelamentos e documentos indisponíveis, separados por tipo fiscal.
- [ ] Comparar diariamente as duas fontes e investigar cada diferença, sem tentar recuperar períodos fora da disponibilidade prometida pela fonte.
- [ ] Conferir reinício do computador, espera SEFAZ, falha de rede, backup/restauração e PIN.
- [ ] Ao fim das duas semanas, produzir divergências explicadas, pendências e decisão da TI. Duas semanas são tempo decorrido de operação, não número de testes automatizados.

**Aceite:** nenhuma divergência relevante sem explicação e aprovação expressa da TI para o escopo testado. Este plano não cria monitoramento ou agenda por conta própria.

## Etapa 6 — Cobertura e manutenção

**Arquivos:** `app/fiscal_channels.py`, `app/nfce.py`, `app/fiscal_sources.py`, `app/server.py`, `app/static/app.js` e testes específicos.

- [ ] Confirmar quais tipos fiscais a empresa realmente utiliza; priorizar MDF-e apenas se necessário, após obter contrato oficial do serviço e permissão de consulta.
- [ ] Levantar lacunas de municípios NFS-e e UFs NFC-e a partir das necessidades do piloto, sem anunciar captura automática universal.
- [ ] Refatorar rotas de manutenção/autenticação e telas em mudanças pequenas, mantendo testes de comportamento e isolamento por organização.
- [ ] Acrescentar validação XSD e assinatura de XML somente com cadeia/pacote oficial atualizado e resultados distintos: estrutura, assinatura e situação na SEFAZ.

**Aceite:** cada nova integração tem contrato, testes, limitações visíveis e evidência real antes de ser anunciada como operacional.
