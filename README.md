# DocPronto Fiscal — atualizações para Windows

Distribuição do sistema fiscal local para Windows x64, sem Docker.

## Instalação inicial

1. Abra [a versão mais recente](https://github.com/Nikodeimus/docpronto-fiscal-atualizacoes/releases/latest).
2. Baixe o arquivo `DocPronto-Fiscal-1.8.21-Contas-Equipes.zip`, ou o ZIP do programa indicado na versão mais recente. Não use os arquivos automáticos “Source code”.
3. Extraia o ZIP e execute `INSTALAR-LOCAL-SEM-DOCKER.cmd`.
4. Abra o atalho **DocPronto Local**.

A instalação inicial habilita o atualizador. A partir daí, abra **Conexões e manutenção → Atualizações do fiscal**. O sistema verifica novas versões ao iniciar e a cada seis horas enquanto estiver aberto, baixa e confere o pacote em segundo plano. Quando estiver pronto, use **Aplicar e reiniciar site**.

A aplicação da atualização cria uma cópia do banco, mantém os arquivos fiscais na pasta persistente e verifica a nova instância. Se falhar, tenta restaurar o banco anterior e reiniciar a versão anterior, preservando os dados da tentativa para diagnóstico. O download não reinicia o programa automaticamente.

## Fonte permanente

```
https://github.com/Nikodeimus/docpronto-fiscal-atualizacoes/releases/latest/download/latest.json
```

Esta fonte já vem configurada na primeira instalação. Uma configuração escolhida anteriormente pelo administrador é preservada.

As próximas versões precisam ser publicadas neste repositório para serem oferecidas pelo programa. O mecanismo não cria novas versões por conta própria.

## Publicação de uma versão

Cada release estável deve incluir o ZIP completo do programa e um `latest.json` com `version`, `package` (URL HTTPS absoluta do ZIP daquela versão) e `sha256` (SHA-256 do ZIP). A versão do feed precisa coincidir com `local-manifest.json` dentro do pacote. Publique os dois arquivos na mesma release e marque-a como a mais recente. Não substitua um pacote já publicado por conteúdo diferente.

O pacote de distribuição contém somente o programa, componentes de execução e instruções. Os dados da instalação, certificados e arquivos fiscais não são publicados.

## Contas e equipes

Na tela de entrada, escolha **Criar organização** para ter sua conta e seus próprios dados. Em **Organização e equipe**, administradores podem gerar convites para compartilhar os dados da organização com perfis de administrador, operador ou somente leitura. Convites duram sete dias e podem ser revogados antes da aceitação.

Uma instalação nova começa sem contas ou documentos de outras pessoas. O primeiro administrador usa o token fornecido pelo instalador. Instalações independentes não sincronizam dados. Para uma equipe acessar o sistema de computadores diferentes, todos precisam usar a mesma instalação em um endereço compartilhado. `localhost` funciona apenas no computador instalado. Este repositório distribui atualizações; não hospeda o serviço fiscal nem o banco de dados.

## Funcionalidades desta revisão

- Seleção de um mês ou intervalo no histórico fiscal e nas exportações ZIP/TXT.
- Período solicitado na captura, preservando a continuidade por NSU e todos os documentos recebidos.
- Atualização integrada com verificação, download e aplicação pelo próprio fiscal.

A recuperação de notas depende da disponibilidade na fonte fiscal; selecionar um período não garante cobertura completa. O conector de certificados permanece um componente separado.


## Código-fonte local

Este repositório também contém o código do DocPronto Fiscal local, baseado no pacote 1.8.32.

- `app/`: aplicação Python, interface e motores fiscais.
- `agent-windows/`: código C# do conector de certificados (.NET 10).
- `tests/`: testes automatizados e amostras fictícias.
- `scripts/`: instalação, manutenção e publicação de pacotes.
- `docs/`: documentação de integrações e contratos oficiais.

Para desenvolvimento, use Python 3.12 em um ambiente virtual e instale `requirements-dev.txt`. Execute `python -m pytest tests` para os testes Python. Os testes de interface `tests/ui-*.cjs` são executados com Node.js. A compilação do conector usa `agent-windows/Build-Windows.ps1` e requer o SDK .NET 10.

O clone contém código-fonte, não um instalador completo: `runtime/`, `tools/`, executáveis e `local-manifest.json` do pacote não são versionados. OCR/PDF e testes que dependem desses componentes exigem suas ferramentas locais. Para instalar o programa pronto, use os arquivos das Releases. Ao montar uma distribuição, gere novamente o manifesto de hashes do pacote completo.

Certificados, senhas, banco de dados e documentos de clientes ficam fora do Git. Os dados da instalação existente não são migrados para este repositório. Esta inclusão de código não publica uma nova versão no canal de atualização.

## Revisão 1.8.33 — parecer técnico

A comparação dos 15 apontamentos do parecer sobre a 1.8.19 com o código atual está em [docs/REVISAO-PARECER-1.8.19.md](docs/REVISAO-PARECER-1.8.19.md). Inclui proteção DPAPI do cofre Windows, situação fiscal de cancelamento, ICMS monofásico, ISSQN e participantes terceiros. Leia as limitações do cofre antes de migrar contas Windows. Essa revisão não constitui homologação de produção nem inicia consultas/manifestações fiscais por conta própria.
