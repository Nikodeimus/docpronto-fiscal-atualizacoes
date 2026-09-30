DOCUMENTO HISTORICO — descreve uma revisao anterior. Nao use como instrucao de atualizacao atual. Consulte README.md e docs/REVISAO-PARECER-1.8.19.md.

# DocPronto Fiscal — correções funcionais de 25/09/2026

Fonte: `DocPronto-Local-1.8.19-Sem-Docker (3).zip`, indicado pelo usuário. Este pacote é o sistema fiscal, com empresas, certificados, captura SEFAZ, documentos e histórico. O conversor de documentos é outro projeto.

Foi criada uma cópia separada, auditada e corrigida. Nenhuma instalação ativa, banco, configuração, certificado ou arquivo de cliente foi alterado. As melhorias adicionais de proteção foram deixadas para outra etapa, conforme solicitado. Permanecem as validações existentes de acesso, confirmação e consistência dos dados.

## Correções aplicadas

| Área | Causa e resultado anterior | Comportamento corrigido |
|---|---|---|
| Histórico por mês | O CAST de texto JSON no SQLite retornava valor numérico em vez de objeto; registros existentes desapareciam do filtro. | Leitura JSON compatível com SQLite; XML de teste aparece no mês certo. Meses inválidos recebem erro de entrada. |
| Filtros de documentos | Mesmo CAST afetava filtros por emissão, direção e modelo. | Filtros passam a consultar os valores armazenados. |
| Upload de PDFs | O lote lia a empresa global a cada arquivo; trocar cadastro podia enviar os próximos PDFs a outra empresa. | O lote fixa a empresa original e interrompe arquivos restantes se o contexto mudar. Não muda silenciosamente o destino. |
| Telas e modais | Respostas antigas de consultas substituíam conteúdo de outro cadastro/tela. | Respostas obsoletas são descartadas em histórico, revisão, exportação, pareamento, certificados e cadastros. |
| Troca de cadastro | Mês e página do histórico permaneciam da empresa anterior. | Seleção reinicia o filtro e a página do histórico. |
| Exclusão | Cliques repetidos podiam repetir envios ou abrir modais concorrentes. | Envio único durante operação, modal vinculado ao contexto e senha removida do formulário ao concluir. As regras de senha e confirmação existentes foram mantidas. |
| Edição fiscal | Era possível editar enquanto a revisão salvava; o retorno podia apagar alterações recém-digitadas. | Controles são bloqueados durante o salvamento e seus estados restaurados depois. |
| Troca de certificado/dispositivo | A continuação automática podia usar a seleção antiga ou ficar presa ao dispositivo anterior. | A próxima tarefa usa a seleção atual do cadastro e é encaminhada ao dispositivo correto. |
| Desvinculação de empresa | Uma tarefa pendente continuava sendo entregue depois de remover o vínculo com o conector. | Vínculo é reavaliado no despacho e a tarefa obsoleta é cancelada. Não há envio para o cadastro desvinculado. |
| Espera e NSU | Tarefa já pendente podia ignorar uma espera registrada; resposta sem avanço do NSU podia gerar repetição automática. | Espera existente é respeitada; ausência de avanço interrompe a repetição e preserva continuidade. Não foi criada nova espera artificial nem alterada a regra externa da SEFAZ. |
| Nova tentativa | Tarefa expirada exigia repetir o comando duas vezes. | A recuperação é feita na primeira solicitação. |
| Fila | Worker com posse antiga podia marcar como falha o trabalho já assumido por outro worker. | Só o dono atual pode concluir/falhar a tarefa. |
| Exportação | XML ausente causava erro interno genérico; seleção com objetos inválidos também falhava internamente. | Mensagem informa arquivo ausente e orientação de restauração/reimportação; seleção inválida retorna erro de entrada. |
| XML reconstruído | Texto `"false"` podia ser tratado como confirmação por ser uma string não vazia. | Exportação exige confirmação booleana verdadeira; a conferência fiscal existente continua necessária. |
| Temporários | Exportação do histórico não fechava explicitamente o arquivo temporário em todos os caminhos. | Recurso fechado na falha e no encerramento da resposta; não é entregue ZIP parcial quando falta um arquivo. |

## Validação realizada

- **159 testes Python passaram** com o Python 3.12 incluído no pacote, em Windows, em 100,97 segundos na rodada final.
- **Seis suítes JavaScript passaram:** seleção de certificado, concorrência de modais, seleção de prestadora/cliente/empresa, recuperação da interface, regressões fiscais e respostas de telas obsoletas.
- Sintaxe de `app/static/app.js` e `review.js` validada pelo Node.
- Testes com banco SQLite temporário, documentos de referência do conjunto de testes e conectores simulados; sem consulta real à SEFAZ ou uso de certificados do usuário.
- Supervisor local testado por HTTP com reinício, prevenção de segunda instância e preservação da conta/token no banco temporário.
- No navegador, em servidor isolado na porta 8081: login, seleção de empresa, arquivamento do XML de teste, filtro dezembro/2025 e troca para outra empresa. O filtro mostrou o XML correto; a outra empresa mostrou histórico vazio e filtro reiniciado.
- Testes de senha de exclusão, isolamento entre empresas, CSRF, confirmação por snapshot, preservação dos documentos originais e exportação TXT existentes continuaram passando.

As novas regressões estão em `tests/test_api_export_regressions.py`, `test_fiscal_history_regressions.py`, `test_capture_audit_regressions.py` e `ui-fiscal-regressions.cjs`; também foi estendido `organization-selectors.cjs`. A execução usou dependências de teste externas ao pacote; o runtime de produção não recebeu pytest nem ferramentas da auditoria.

## Arquivos de produção alterados

- `app/server.py`: exportação, validação de cadastro e identificação da revisão.
- `app/document_management.py` e `app/fiscal_history.py`: filtros e exportação do histórico.
- `app/distribution.py` e `app/worker.py`: captura, certificado/dispositivo, continuidade e posse da fila.
- `app/static/app.js`, `review.js` e `index.html`: contexto das operações, edição, modais e versão dos assets.
- `local-manifest.json`: somas SHA-256 recalculadas para o instalador reconhecer o pacote corrigido.

Status da aplicação conserva a versão base `1.8.19` e acrescenta `revision: fiscal-auditado-20260925`. O manifesto identifica `1.8.19-auditado-20260925`.

## Como usar o pacote

1. Extraia o ZIP inteiro para uma pasta curta, por exemplo `C:\DocProntoFiscal`.
2. Para atualizar a instalação fiscal, use `INSTALAR-LOCAL-SEM-DOCKER.vbs` ou o `.cmd`, conforme as instruções originais.
3. Após atualizar, abra o atalho **DocPronto Local** e recarregue o navegador.

Este trabalho entrega o pacote editado; **a atualização da instalação atual não foi executada**. O instalador original preserva a pasta de dados e faz cópia do banco antes de atualizar. Essa cópia não inclui todos os documentos: as instruções originais de backup completo continuam válidas.

## Ainda exige teste no ambiente real

- Comunicação SEFAZ com seus certificados A1/A3, drivers, PIN e permissões fiscais.
- Instalação/atualização completa pelo instalador gráfico na máquina de destino.
- Homologação dos XMLs no sistema que os recebe, inclusive XMLs reconstruídos e variantes não representadas pelos fixtures.
- Carga prolongada e grandes lotes com os documentos de uso diário.

O executável do conector Windows não foi modificado nem recompilado. As correções de despacho e troca de certificado foram feitas no sistema fiscal; problemas específicos de driver/TLS do dispositivo precisam de teste real. Recursos adicionais de proteção ficam para a próxima etapa.

Testes aprovados demonstram os cenários exercitados, não garantem todas as integrações externas. Não foram inventados dados de nota, NSU, autorização ou resultado de consulta.


## Complemento: seleção de mês e intervalo

Em Buscar notas → Período desejado → Escolher mês ou intervalo, informe mês/ano inicial; o final é opcional. 08/2026 sozinho representa agosto; 08/2026 até 09/2026 inclui ambos. A captura do mês corrente considera até hoje. Meses futuros ou intervalos invertidos são rejeitados.

O lote mostra as datas solicitadas e oferece Ver arquivos deste período. O Histórico fiscal filtra os arquivos por emissão (eventos usam sua data registrada), com ZIP e TXT respeitando o mesmo intervalo em todas as páginas. Todos os meses limpa o filtro. Registros sem data continuam disponíveis sem filtro. Trocar de empresa limpa ambos os meses e reinicia a página.

A seleção não altera a distribuição externa por NSU: todos os retornos são preservados, inclusive fora do intervalo. O período solicitado não comprova cobertura completa; documentos indisponíveis na SEFAZ não podem ser prometidos. Nenhuma consulta real com certificado foi executada nesta validação.

Validação desta revisão: 191 testes Python passaram; 1 teste de inicialização local foi ignorado pela porta 8080 ocupada. Sete suítes JavaScript passaram. Interface conferida no navegador em banco temporário: seleção 08/2026 a 09/2026 e links ZIP/TXT com o mesmo intervalo. Testes de integração verificaram conteúdo e limites inclusivos do ZIP/TXT, isolamento entre empresas, mês bissexto, virada de ano, mês corrente, preservação byte a byte de XML fora do período e continuidade do NSU.


## Atualizador integrado — 1.8.20

Canal configurável (HTTPS, arquivo/pasta local e HTTP restrito a loopback), download independente do site, conferência SHA-256 do pacote e de todos os arquivos, bloqueio de operações concorrentes e aplicação pelo instalador local. A UI mostra estado, permite verificar/baixar e aplicar/reiniciar; somente o administrador da instalação pode executar as operações. Configurações preexistentes são preservadas.

O supervisor verifica ao iniciar e a cada seis horas com o programa aberto; baixar não aplica automaticamente. O instalador faz snapshot do banco antes de inicializar a nova versão, verifica HTTP/root/instance e, ao falhar, aguarda o encerramento da tentativa, preserva seu banco/WAL e restaura o snapshot antes de reiniciar a anterior. O supervisor em segundo plano registra falhas sem abrir diálogo que impediria a recuperação.

Canal público: https://github.com/Nikodeimus/docpronto-fiscal-atualizacoes/releases. Uma instalação inicial habilita o mecanismo. Novas versões ainda precisam ser publicadas. Há publicador de pasta e servidor de feed opcionais para redes próprias. O pacote público exclui testes e seus documentos de referência; eles permanecem no ambiente local de validação.

Validação: rodada geral com 253 aprovados, 1 teste ignorado por porta 8080 ocupada e 1 expectativa da versão antiga corrigida para 1.8.20. Rodada focada subsequente 12 aprovados inclui esse teste e um teste adicional do supervisor sem diálogo; total da suíte atual 255 aprovados e 1 ignorado. Oito suítes JavaScript aprovadas. Testes de transação do instalador usam pastas temporárias e simulam processos/atalhos/ACL. Nenhuma atualização foi aplicada à instalação real durante os testes.
