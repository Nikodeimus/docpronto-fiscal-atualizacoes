# Revisão do parecer sobre a versão 1.8.19

Data: 30/09/2026. Referência: “Parecer técnico — DocPronto Local 1.8.19”, recebido em PDF. Comparação com o código versionado no Git (base 1.8.32) e correções desta revisão 1.8.33. O parecer não descreve integralmente a versão atual; seus critérios de aceite não equivalem a uma homologação já realizada.

## Conferência dos 15 apontamentos

| Item | Situação verificada no código atual | Ação / limite |
| --- | --- | --- |
| 1. Cofre A1 recuperável por cópia da pasta | A chave Fernet em arquivo ainda existia. | Chave protegida por DPAPI da conta Windows, com migração atômica verificada no primeiro uso. Ver `CERTIFICATE_VAULT.md`. Backups antigos não ficam protegidos retroativamente. |
| 2. A3/TLS sem validação real | Há testes de protocolo e diagnóstico; isso não prova funcionamento com cada token, driver e PIN. | Continua pendente piloto real com A3. Não desativar revogação ou validação TLS para fazer o teste passar. |
| 3. Ausência de Ciência | Superado como ausência de implementação: há envio 210210 e automação com autorização específica e comprovação do resumo destinatário. | Continua desativada por padrão. Não confundir Ciência com Confirmação da Operação, nem prometer liberação imediata de todo XML. |
| 4. Cancelamento invisível | A ficha já lia retorno de evento aceito, mas faltavam resumos e indicação na lista agrupada. | Acrescentada evidência `cSitNFe=3` e resumo de evento registrado; situação fiscal separada da disponibilidade de XML e do processamento. Arquivos originais preservados, sem consulta online implícita. |
| 5. Só NF-e | Parcialmente superado: CT-e e NFS-e/ADN têm conectores próprios; NFC-e tem importação e navegação oficial por UF. | NFC-e não possui captura automática universal. MDF-e não está implementado. NFS-e depende da adesão/disponibilidade no ADN. |
| 6. Parser limitado | ICMS02/15/53 ainda eram recusados. | Modalidades adicionadas com preservação de campos e conferência dos totais monofásicos; ISSQN com totalização separada; transportadora/autXML com identidade explícita no XML e fluxo terceiro, sem atribuir entrada/saída. São perfis validados em testes, não homologação de todas as exceções tributárias. |
| 7. Inicialização Windows | Já há supervisor, reinício de filhos, atalho de inicialização e tarefa local configurada no computador principal. | Não existe garantia de captura com computador desligado, usuário desconectado ou A3 aguardando PIN. Alertas de captura/conector são exibidos com o site em funcionamento. |
| 8. Período não enviado à SEFAZ | A distribuição por NSU continua sem filtro remoto por data. | A interface já informa retenção/limites e cobertura não comprovada; filtros atuam nos arquivos locais. Não inventar parâmetro de período para o serviço. |
| 9. Um usuário / localhost | Superado parcialmente: existem organizações, perfis e configuração de rede local. | Porta padrão 8080 mantida. HTTPS e acesso externo exigem infraestrutura/configuração; não são ativados por esta revisão. |
| 10. PIN / assinatura extra A3 | A assinatura de teste antes de cada consulta ainda está no conector. | Alteração proposta foi bloqueada pela revisão automática da ferramenta, sem motivo específico. Nenhuma remoção foi aplicada. A3 não é prometido como desassistido. |
| 11. XML reconstruído do PDF | Já é marcado RECONSTRUCTED, exige conferência e confirmação explícita no lote, e sai separado dos originais. | Continua sem validade de XML autorizado. A conferência humana não cria assinatura nem protocolo. |
| 12. Authenticode | Assinatura de código não implementada. | Exige certificado/processo de assinatura apropriado da empresa. Não usar certificado fiscal de cliente para assinar executáveis. |
| 13. Bypass / atualizador Docker | Continua em scripts legados; `LocalMaintenance.Update` ainda espera pacote Docker. | Bloqueio automático impediu substituir esse fluxo pelo atualizador local. Usar a tela de atualizações do fiscal. Não foi alterada a política global do Windows. |
| 14. NF-e real em testes | Removida da cópia Git na importação anterior do projeto. | Amostra fictícia e assinatura sem validade; referências de teste harmonizadas. Pacotes históricos já distribuídos não são apagados pela revisão. |
| 15. ZIPs sobrepostos / manutenção | O projeto agora está em Git com testes e histórico. | Documentos de aplicação sobreposta marcados como históricos. API de status corrigida: deixava a versão fixa 1.8.23, impedindo identificar o código atual. Refatoração ampla de módulos continua gradual. |

## Critérios que ainda precisam de evidência operacional

O piloto comparativo de duas semanas proposto pela TI não é substituído por testes unitários. Deve registrar CNPJs autorizados, fonte e período, chaves distintas, XMLs completos, resumos, cancelamentos, falhas e consultas em espera. A lista da fonte de comparação é necessária para medir diferenças; contagem local isolada não comprova completude. Não foi iniciada manifestação real nem nova consulta fiscal nesta revisão.

Não há evidência neste parecer de que uma cópia do cofre foi obtida por terceiros. Revogação de certificados, exclusão de dados, avisos a clientes e desinstalação não são executados automaticamente a partir das recomendações do PDF.

## Restauração do cofre

DPAPI protege uma cópia simples da pasta fora do perfil original; não protege contra código malicioso executado como o mesmo usuário. Uma restauração em outro Windows deve preservar os arquivos antigos e planejar a criação de um novo cofre e o recadastro de todos os A1 com a TI. Ainda não existe botão de reset/migração entre perfis: não apagar a chave manualmente. Documentos fiscais e banco não dependem da abertura do A1 para consulta local. O envio PFX/senha ao conector autorizado continua existindo e requer transporte protegido quando atravessa rede.

## Referências técnicas do parser

- NT 2023.001 v1.51 (ICMS monofásico): https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=nyszXJzXYlk%3D
- MOC 7, Anexo I (grupos ISSQN/ISSQNtot, totais e participantes): https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=J%2BI%2Bv4eN00E%3D

## Verificação desta revisão

- Parser e importação de fontes: 46 testes aprovados, incluindo ISSQN, terceiros e ICMS monofásico.
- Cofre, identificação da versão, captura, backup e operações de documentos: rodada com 67 testes aprovados e 1 ignorado (ferramentas externas de PDF não disponíveis naquele processo).
- Cancelamento na API de Documentos: teste de integração aprovado.
- Histórico, agrupamento e evidências fiscais: rodada com 59 testes aprovados antes da ampliação ISSQN/terceiros.
- Interface: 23 arquivos de testes JavaScript aprovados.

As rodadas têm testes em comum; os números não devem ser somados como testes únicos. Nenhum teste utilizou certificado real ou enviou manifestação fiscal. A instalação local foi tentada, mas bloqueada pela política de scripts do Windows antes de alterar a versão ativa. O pacote preparado não comprova instalação concluída.
