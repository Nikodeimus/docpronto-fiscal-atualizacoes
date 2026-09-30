# Ciência automática e NFS-e — 1.8.32

## Ciência automática

A NT 2014.002 v1.40, tabela 3.2 (página 5), distribui `resNFe` somente ao destinatário. O sistema utiliza essa evidência **somente** quando o resumo vem de uma resposta de distribuição aceita, preservada e vinculada ao CPF/CNPJ consultado. O arquivo isolado não demonstra esse vínculo.

Fonte oficial consultada em 29/09/2026: https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=uWO2d%2FgTuWg%3D
Cópia oficial: `docs/contracts/NT2014.002-v1.40.pdf`.

Cada empresa precisa ativar o canal e autorizar separadamente a automação. Instalar a atualização não ativa nenhum envio. O consentimento registra o usuário e o evento 210210. A automação só considera resumos com prova validada nesta versão, emitidos nos últimos sete dias. Ao ativar, também confere recibos antigos preservados para comprovar resumos ainda existentes, sem restaurar arquivos excluídos; esse é um limite operacional conservador do programa, não uma afirmação do prazo legal. Resumos sem origem, cancelados, alterados, já completos ou com eventos conflitantes ficam fora da automação. Não se confirma operação, desconhecimento ou operação não realizada automaticamente.

O envio tem chave e data persistentes. Em falhas ou duplicidades, o resultado exige conferência, sem repetição automática do evento. Após sucesso 135, é criada uma consulta da chave na fila NF-e, com atraso mínimo de cinco minutos e respeito ao intervalo já exigido pela SEFAZ. O XML depende da disponibilização pelo serviço; não é garantido pela Ciência. O XML completo, quando recebido, substitui o resumo na mesma nota do histórico.

## NFS-e

Contrato oficial: https://adn.nfse.gov.br/contribuintes/swagger/v1/swagger.json
Consulta `GET /DFe/{NSU}` com `cnpjConsulta` e `lote=true`. mTLS pelo conector local. Esta implementação aceita CNPJ; não oferece consulta por CPF. Não representa integração direta com todos os sistemas municipais: recupera somente os documentos que o ADN disponibilizar.

NSU separado de NF-e/CT-e, JSON original preservado, XML GZip/Base64 validado, limite de tamanho, arquivos deduplicados e cursor sem regressão. Resposta sem avanço pausa o canal para evitar um ciclo de consultas. Nenhum formato inclusivo/exclusivo do NSU foi presumido além do retorno observado.

Validação real de leitura autorizada: uma consulta, HTTP 200, 42 documentos e NSUs 1–42. O recibo permaneceu isolado; não foi importado automaticamente em cadastros. Testes da Ciência usaram dados fictícios, sem transmissão fiscal real.
