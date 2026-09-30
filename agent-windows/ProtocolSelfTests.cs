using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;

static class ProtocolSelfTests
{
    public static int Run()
    {
        int checks = 0;
        void Check(bool passed, string name)
        {
            if (!passed) throw new AgentError("Teste falhou: " + name);
            checks++;
        }
        foreach (string allowed in new[] { "https://docpronto.example", "http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000" })
            Check(ConnectedAgent.ValidateServer(allowed).IsAbsoluteUri, "URL permitida");
        foreach (string blocked in new[] { "http://example.com", "http://localhost.evil.example", "file:///tmp/token", "https://user:password@example.com", "https://example.com?token=x", "https://example.com#token" })
        {
            bool refused = false;
            try { ConnectedAgent.ValidateServer(blocked); }
            catch (AgentError) { refused = true; }
            Check(refused, "URL recusada");
        }
        byte[] challenge = RandomNumberGenerator.GetBytes(32);
        using var rsa = RSA.Create(2048);
        var rsaRequest = new CertificateRequest("CN=DocPronto ephemeral protocol RSA test", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var rsaCertificate = rsaRequest.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1), DateTimeOffset.UtcNow.AddDays(1));
        var rsaResult = ConnectedAgent.SignChallenge("rsa-test", rsaCertificate, challenge);
        Check(rsaResult.Ok && rsaResult.Algorithm == "RSA-SHA256", "RSA resultado");
        byte[] rsaSignature = Convert.FromBase64String(rsaResult.Signature!);
        Check(rsa.VerifyData(challenge, rsaSignature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1), "RSA assinatura");
        byte[] tampered = (byte[])challenge.Clone(); tampered[0] ^= 1;
        Check(!rsa.VerifyData(tampered, rsaSignature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1), "RSA desafio adulterado");
        using var ec = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var ecRequest = new CertificateRequest("CN=DocPronto ephemeral protocol ECDSA test", ec, HashAlgorithmName.SHA256);
        using var ecCertificate = ecRequest.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1), DateTimeOffset.UtcNow.AddDays(1));
        var ecResult = ConnectedAgent.SignChallenge("ec-test", ecCertificate, challenge);
        Check(ecResult.Ok && ecResult.Algorithm == "ECDSA-SHA256", "ECDSA resultado");
        byte[] ecSignature = Convert.FromBase64String(ecResult.Signature!);
        Check(ec.VerifyData(challenge, ecSignature, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence), "ECDSA DER assinatura");
        Check(!ec.VerifyData(tampered, ecSignature, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence), "ECDSA desafio adulterado");
        Check(Convert.FromBase64String(ecResult.CertificateDer!).SequenceEqual(ecCertificate.RawData), "Certificado DER p√∫blico");
        Check(ConnectedAgent.Execute(new AgentTask("unknown", "fetch_anywhere", "", "")).Error == "unsupported_task", "Tarefa arbitr√°ria bloqueada");
        Check(ConnectedAgent.Execute(new AgentTask("short", "self_test", "", "AA==")).Error == "invalid_challenge", "Desafio curto bloqueado");
        Check(ConnectedAgent.Execute(new AgentTask("bad", "self_test", "", "not-base64")).Error == "invalid_challenge", "Base64 inv√°lido bloqueado");
        using var serialized = JsonDocument.Parse(JsonSerializer.Serialize(rsaResult, ConnectedAgent.Json));
        Check(serialized.RootElement.TryGetProperty("certificate_der", out _) && serialized.RootElement.TryGetProperty("algorithm", out _), "Contrato result snake_case");
        var pair = JsonSerializer.Deserialize<PairResponse>("{\"token\":\"test-token\",\"company_id\":\"company-test\",\"company_name\":\"Test\"}", ConnectedAgent.Json);
        Check(pair?.CompanyId == "company-test", "Contrato pair");
        var envelope=SefazClient.Envelope(new AgentTask("dist","distribution","","",Document:"12345678000199",Uf:"35",Nsu:"000000000000000"));
        System.Xml.Linq.XNamespace n="http://www.portalfiscal.inf.br/nfe";
        Check(envelope.Descendants(n+"ultNSU").Single().Value=="000000000000000","NSU envelope");
        Check(envelope.Descendants(n+"CNPJ").Single().Value=="12345678000199","CNPJ envelope");
        Check(envelope.Descendants(n+"tpAmb").Single().Value=="1","Produ√ß√£o expl√≠cita");
        bool invalid=false;try{SefazClient.Envelope(new AgentTask("bad","distribution","","",Document:"x",Uf:"35"));}catch(AgentError){invalid=true;}
        Check(invalid,"Documento inv√°lido bloqueado");
        var fiscalTask=new AgentTask("fiscal-test","fiscal_query","","",Document:"12345678000199",Uf:"35",Nsu:"000000000000001",Service:"cte");
        using(var cteRequest=FiscalRequest.Create(fiscalTask,rsaCertificate))
        {
            Check(cteRequest.RequestUri!.Host=="www1.cte.fazenda.gov.br","CT-e endpoint fixo");
            var cte=System.Xml.Linq.XDocument.Parse(cteRequest.Content!.ReadAsStringAsync().GetAwaiter().GetResult());
            System.Xml.Linq.XNamespace c="http://www.portalfiscal.inf.br/cte";
            Check(cte.Descendants(c+"distDFeInt").Single().Attribute("versao")!.Value=="1.00","CT-e schema");
            Check(cte.Descendants(c+"ultNSU").Single().Value=="000000000000001","CT-e NSU independente");
        }
        var manifest=fiscalTask with {Service="manifest",Key="35260912345678000199550010000000011000000010",EventCode="210210",EventTime="2026-09-29T12:00:00-03:00",Consent=true};
        var signedText=FiscalRequest.Science(manifest,rsaCertificate);
        var signedDoc=new System.Xml.XmlDocument{PreserveWhitespace=true};signedDoc.LoadXml(signedText);
        var verify=new System.Security.Cryptography.Xml.SignedXml(signedDoc);
        verify.LoadXml((System.Xml.XmlElement)signedDoc.GetElementsByTagName("Signature",System.Security.Cryptography.Xml.SignedXml.XmlDsigNamespaceUrl)[0]!);
        Check(verify.CheckSignature(rsaCertificate,true),"CiÍncia XML assinado verific·vel");
        Check(signedText==FiscalRequest.Science(manifest,rsaCertificate),"CiÍncia repetiÁ„o mantÈm ID/data/assinatura");
        signedDoc.GetElementsByTagName("dhEvento")[0]!.InnerText="2026-09-29T13:00:00-03:00";
        Check(!verify.CheckSignature(rsaCertificate,true),"CiÍncia adulterada recusada");
        foreach(var blockedTask in new[]{manifest with{Consent=false},manifest with{EventCode="210200"},manifest with{EventTime=null}})
        {
            bool blockedEvent=false;try{FiscalRequest.Science(blockedTask,rsaCertificate);}catch(AgentError){blockedEvent=true;}
            Check(blockedEvent,"CiÍncia sem autorizaÁ„o ou evento n„o permitido bloqueado");
        }
        using(var stubClient=new HttpClient(new FiscalStub()))
        using(var stubRequest=FiscalRequest.Create(fiscalTask,rsaCertificate))
        {
            var bytes=FiscalRequest.Send(stubClient,stubRequest,CancellationToken.None).GetAwaiter().GetResult();
            var returned=System.Xml.Linq.XDocument.Parse(System.Text.Encoding.UTF8.GetString(bytes));
            Check(returned.Root!.Element("ultNSU")!.Value=="2" && returned.Root.Element("maxNSU")!.Value=="5","transporte preserva cursores da resposta CT-e");
        }
        using(var stubClient=new HttpClient(new FiscalStub(System.Net.HttpStatusCode.ServiceUnavailable)))
        using(var stubRequest=FiscalRequest.Create(fiscalTask,rsaCertificate))
        {
            bool httpFailed=false;try{FiscalRequest.Send(stubClient,stubRequest,CancellationToken.None).GetAwaiter().GetResult();}catch(HttpRequestException){httpFailed=true;}
            Check(httpFailed,"HTTP falho n„o È tratado como recibo v·lido");
        }
        var nfseTask=fiscalTask with{Service="nfse",Nsu="000000000000003"};
        using(var nfseRequest=FiscalRequest.Create(nfseTask,rsaCertificate))
        {
            Check(nfseRequest.Method==HttpMethod.Get && nfseRequest.RequestUri!.AbsoluteUri=="https://adn.nfse.gov.br/contribuintes/DFe/3?cnpjConsulta=12345678000199&lote=true","ADN endpoint oficial e par‚metros exatos");
            Check(nfseRequest.Headers.Accept.Single().MediaType=="application/json" && nfseRequest.Content is null,"ADN GET JSON sem segredo no corpo");
        }
        foreach(var invalidAdn in new[]{nfseTask with{Nsu="9223372036854775808"},nfseTask with{Nsu="../docs"},nfseTask with{Document="12345678901"},nfseTask with{Key=manifest.Key}})
        {
            bool rejected=false;try{using var unused=FiscalRequest.Create(invalidAdn,rsaCertificate);}catch(AgentError){rejected=true;}
            Check(rejected,"ADN entrada inv·lida recusada");
        }
        foreach(var status in new[]{System.Net.HttpStatusCode.OK,System.Net.HttpStatusCode.BadRequest,System.Net.HttpStatusCode.NotFound})
        {
            using var adnClient=new HttpClient(new AdnStub(status));using var adnRequest=FiscalRequest.Create(nfseTask,rsaCertificate);
            var receipt=FiscalRequest.Receive(adnClient,adnRequest,CancellationToken.None,true).GetAwaiter().GetResult();
            Check(receipt.Status==(int)status && System.Text.Encoding.UTF8.GetString(receipt.Body).Contains("StatusProcessamento"),"ADN corpo e status preservados inclusive400/404");
        }
        Console.WriteLine($"PASS: {checks} verifica√ß√µes de URL, contratos e assinaturas RSA/ECDSA. Teste port√°til; n√£o testa reposit√≥rio/ACL Windows nem token A3.");
        return 0;
    }
}

sealed class FiscalStub(System.Net.HttpStatusCode status=System.Net.HttpStatusCode.OK):HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
    {
        if(request.RequestUri!.Host!="www1.cte.fazenda.gov.br")throw new AgentError("Host do teste inesperado");
        return Task.FromResult(new HttpResponseMessage(status){Content=new StringContent("<retDistDFeInt><cStat>138</cStat><ultNSU>2</ultNSU><maxNSU>5</maxNSU></retDistDFeInt>")});
    }
}

sealed class AdnStub(System.Net.HttpStatusCode status):HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
    {
        if(request.RequestUri!.Host!="adn.nfse.gov.br")throw new AgentError("Host ADN inesperado");
        return Task.FromResult(new HttpResponseMessage(status){Content=new StringContent("{\"TipoAmbiente\":\"PRODUCAO\",\"StatusProcessamento\":\"NENHUM_DOCUMENTO_LOCALIZADO\",\"LoteDFe\":[]}")});
    }
}
