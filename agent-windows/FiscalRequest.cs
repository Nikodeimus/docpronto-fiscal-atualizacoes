using System.Globalization;
using System.Security.Cryptography.X509Certificates;
using System.Security.Cryptography.Xml;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;
using System.Xml.Linq;

// Official contracts: CT-e NT 2015.002 / PL_CTeDistDFe_100; NF-e MOC event 210210.
// Endpoints: cte.fazenda.gov.br/portal/webServices.aspx and nfe.fazenda.gov.br/portal/webServices.aspx.
// SHA-1 below is the NF-e XMLDSig interoperability algorithm, not a password/key derivation.
static class FiscalRequest
{
    internal sealed record HttpReceipt(byte[] Body,int Status);
    internal static async Task<byte[]> Send(HttpClient client,HttpRequestMessage request,CancellationToken token)
        => (await Receive(client,request,token)).Body;
    internal static async Task<HttpReceipt> Receive(HttpClient client,HttpRequestMessage request,CancellationToken token,bool preserveAdnError=false)
    {
        using var response=await client.SendAsync(request,HttpCompletionOption.ResponseHeadersRead,token);
        if(!preserveAdnError || response.StatusCode is not (System.Net.HttpStatusCode.BadRequest or System.Net.HttpStatusCode.NotFound)) response.EnsureSuccessStatusCode();
        using var input=await response.Content.ReadAsStreamAsync(token);using var output=new MemoryStream();
        var buffer=new byte[8192];int count;
        while((count=await input.ReadAsync(buffer,token))>0)
        {
            if(output.Length+count>16*1024*1024)throw new InvalidDataException("Resposta fiscal excessiva.");
            output.Write(buffer,0,count);
        }
        return new(output.ToArray(),(int)response.StatusCode);
    }
    internal static HttpRequestMessage Create(AgentTask task, X509Certificate2 certificate)
    {
        if(task.Kind=="fiscal_query" && task.Service=="nfse")
        {
            if(task.Document is null || !Regex.IsMatch(task.Document,@"^\d{14}$")) throw new AgentError("Consulta ADN requer CNPJ de 14 d�gitos.");
            if(task.Nsu is null || !Regex.IsMatch(task.Nsu,@"^\d{1,19}$") || !long.TryParse(task.Nsu,NumberStyles.None,CultureInfo.InvariantCulture,out var nsu) || nsu<0) throw new AgentError("NSU ADN inv�lido.");
            if(!string.IsNullOrEmpty(task.Key)) throw new AgentError("Distribui��o ADN por NSU n�o aceita chave.");
            var adn=new HttpRequestMessage(HttpMethod.Get,"https://adn.nfse.gov.br/contribuintes/DFe/"+nsu.ToString(CultureInfo.InvariantCulture)+"?cnpjConsulta="+task.Document+"&lote=true");
            adn.Headers.Accept.ParseAdd("application/json");return adn;
        }
        string endpoint, action, xml;
        switch (task.Service)
        {
            case null when task.Kind == "distribution":
                endpoint="https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx";
                action="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse";
                xml=SefazClient.Envelope(task).ToString(SaveOptions.DisableFormatting);break;
            case "cte" when task.Kind == "fiscal_query":
                endpoint="https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx";
                action="http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe/cteDistDFeInteresse";
                xml=Cte(task).ToString(SaveOptions.DisableFormatting);break;
            case "manifest" when task.Kind == "fiscal_query":
                endpoint="https://www.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx";
                action="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4/nfeRecepcaoEvento";
                xml=Science(task,certificate);break;
            default: throw new AgentError("Serviço fiscal não suportado por este conector.");
        }
        var content=new StringContent(xml,Encoding.UTF8);
        content.Headers.ContentType=System.Net.Http.Headers.MediaTypeHeaderValue.Parse("application/soap+xml; charset=utf-8; action=\""+action+"\"");
        return new(HttpMethod.Post,endpoint){Content=content};
    }
    internal static XDocument Cte(AgentTask task)
    {
        if (!string.IsNullOrEmpty(task.Key)) throw new AgentError("Distribuição CT-e exige NSU, não chave.");
        // Reuse validated document/UF/15-digit NSU, then change only the protocol vocabulary.
        var doc=SefazClient.Envelope(task);
        foreach(var element in doc.Descendants())
        {
            if(element.Name.NamespaceName.StartsWith("http://www.portalfiscal.inf.br/nfe",StringComparison.Ordinal))
                element.Name=XName.Get(element.Name.LocalName.Replace("nfeDistDFeInteresse","cteDistDFeInteresse").Replace("nfeDadosMsg","cteDadosMsg"),element.Name.NamespaceName.Replace("/nfe","/cte").Replace("NFeDistribuicaoDFe","CTeDistribuicaoDFe"));
        }
        doc.Descendants().Single(x=>x.Name.LocalName=="distDFeInt").SetAttributeValue("versao","1.00");
        return doc;
    }
    internal static string Science(AgentTask task,X509Certificate2 certificate)
    {
        if(!task.Consent || task.EventCode!="210210") throw new AgentError("Ciência exige autorização específica; outros eventos não são permitidos.");
        if(task.Document is null || !Regex.IsMatch(task.Document,@"^(\d{11}|\d{14})$") || task.Key is null || !Regex.IsMatch(task.Key,@"^\d{44}$") || task.Key.Substring(20,2)!="55") throw new AgentError("Documento/chave NF-e inválidos.");
        if(task.EventTime is null || !Regex.IsMatch(task.EventTime,@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$") || !DateTimeOffset.TryParse(task.EventTime,CultureInfo.InvariantCulture,DateTimeStyles.None,out _)) throw new AgentError("Data persistente do evento inválida.");
        XNamespace n="http://www.portalfiscal.inf.br/nfe", soap="http://www.w3.org/2003/05/soap-envelope", ws="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4";
        string id="ID210210"+task.Key+"01";
        var info=new XElement(n+"infEvento",new XAttribute("Id",id),new XElement(n+"cOrgao","91"),new XElement(n+"tpAmb","1"),new XElement(n+(task.Document.Length==14?"CNPJ":"CPF"),task.Document),new XElement(n+"chNFe",task.Key),new XElement(n+"dhEvento",task.EventTime),new XElement(n+"tpEvento","210210"),new XElement(n+"nSeqEvento","1"),new XElement(n+"verEvento","1.00"),new XElement(n+"detEvento",new XAttribute("versao","1.00"),new XElement(n+"descEvento","Ciencia da Operacao")));
        var envelope=new XDocument(new XElement(soap+"Envelope",new XElement(soap+"Body",new XElement(ws+"nfeDadosMsg",new XElement(n+"envEvento",new XAttribute("versao","1.00"),new XElement(n+"idLote","1"),new XElement(n+"evento",new XAttribute("versao","1.00"),info))))));
        var document=new XmlDocument{PreserveWhitespace=true,XmlResolver=null};document.LoadXml(envelope.ToString(SaveOptions.DisableFormatting));
        using var rsa=certificate.GetRSAPrivateKey() ?? throw new AgentError("Ciência requer chave RSA.");
        var signed=new SignedXml(document){SigningKey=rsa};
        signed.SignedInfo!.CanonicalizationMethod=SignedXml.XmlDsigCanonicalizationUrl;
        signed.SignedInfo.SignatureMethod=SignedXml.XmlDsigRSASHA1Url;
        var reference=new Reference("#"+id){DigestMethod=SignedXml.XmlDsigSHA1Url};reference.AddTransform(new XmlDsigEnvelopedSignatureTransform());reference.AddTransform(new XmlDsigC14NTransform());signed.AddReference(reference);
        var keyInfo=new KeyInfo();keyInfo.AddClause(new KeyInfoX509Data(certificate));signed.KeyInfo=keyInfo;signed.ComputeSignature();
        document.GetElementsByTagName("evento",n.NamespaceName)[0]!.AppendChild(document.ImportNode(signed.GetXml(),true));
        return document.OuterXml;
    }
}
