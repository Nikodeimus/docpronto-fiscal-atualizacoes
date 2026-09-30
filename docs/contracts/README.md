# Contrato ADN contribuintes

`adn-contribuintes-openapi.json` é uma cópia da especificação pública oficial obtida por GET autenticado com certificado cliente em 29/09/2026, sem consulta a documentos fiscais.

- Origem: https://adn.nfse.gov.br/contribuintes/swagger/v1/swagger.json
- Página de documentação: https://adn.nfse.gov.br/contribuintes/docs/index.html
- Catálogo oficial de ambientes: https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao
- SHA256 da cópia: `8D9C5041979E9449BBC0C308B9497A38B7E19DCB2817630E2E3B7B0AB916CC39`

O conector usa `GET /DFe/{NSU}`, com `cnpjConsulta` e `lote=true`, em produção, aceitando JSON. O NSU é inteiro de 64 bits. A resposta usa `StatusProcessamento`, `LoteDFe`, `Erros`, `Alertas` e `TipoAmbiente`; não possui `ultNSU` nem `maxNSU`. As respostas HTTP 400 e 404 possuem o mesmo esquema e são preservadas para interpretação pelo servidor local.

Esta versão do conector restringe a consulta a CNPJ. O catálogo não comprova cobertura de notas municipais não compartilhadas com o ADN, nem garante todo o histórico de um contribuinte. O OpenAPI não especifica, por si só, semântica inclusiva/exclusiva do cursor nem tamanho máximo do lote. Não extrapolar regras da distribuição NF-e para este canal.

A especificação não contém certificado, chave privada, senha ou dados de cliente.
