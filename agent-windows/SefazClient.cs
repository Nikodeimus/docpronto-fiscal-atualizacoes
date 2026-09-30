using System.Net;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Xml;
using System.Xml.Linq;

sealed record DistributionResult(string Id, bool Ok, string? Response = null, string? Error = null, string? Stage = null, List<ErrorFact>? Diagnostics = null, string? Service = null, int? HttpStatus = null);
static class SefazClient
{
    internal static XDocument Envelope(AgentTask task)
    {
        if (task.Document is null || !System.Text.RegularExpressions.Regex.IsMatch(task.Document,@"^(\d{11}|\d{14})$")) throw new AgentError("Documento inválido.");
        if (task.Uf is null || !"11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53".Split(' ').Contains(task.Uf)) throw new AgentError("UF inválida.");
        XNamespace n="http://www.portalfiscal.inf.br/nfe",soap="http://www.w3.org/2003/05/soap-envelope",ws="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe";
        XElement query;
        if (!string.IsNullOrEmpty(task.Key))
        {
            if (!System.Text.RegularExpressions.Regex.IsMatch(task.Key,@"^\d{44}$")) throw new AgentError("Chave inválida.");
            query=new XElement(n+"consChNFe",new XElement(n+"chNFe",task.Key));
        }
        else
        {
            if (task.Nsu is null || !System.Text.RegularExpressions.Regex.IsMatch(task.Nsu,@"^\d{15}$")) throw new AgentError("NSU inválido.");
            query=new XElement(n+"distNSU",new XElement(n+"ultNSU",task.Nsu));
        }
        return new XDocument(new XElement(soap+"Envelope",new XElement(soap+"Body",new XElement(ws+"nfeDistDFeInteresse",new XElement(ws+"nfeDadosMsg",new XElement(n+"distDFeInt",new XAttribute("versao","1.01"),new XElement(n+"tpAmb","1"),new XElement(n+"cUFAutor",task.Uf),new XElement(n+(task.Document.Length==14?"CNPJ":"CPF"),task.Document),query))))));
    }
    internal static async Task<DistributionResult> Query(AgentTask task,CancellationToken token)
    {
        string stage="certificate_selection";
        try
        {
            using var cert=task.Pfx is not null ? A1Certificate.Load(Convert.FromBase64String(task.Pfx),task.PfxPassword) : Agent.Select(new Dictionary<string,string>{{"thumbprint",task.Thumbprint},{"store",task.Store??"CurrentUser"}});
            Agent.CheckUsable(cert);
            stage="private_key";
            Console.WriteLine("Acessando a chave do certificado selecionado. O Windows pode solicitar o PIN.");
            Agent.SelfTest(cert);
            Console.WriteLine("Chave privada acessível nesta tentativa. Iniciando comunicação com a SEFAZ.");
            stage="request_preparation";
            using var handler=SefazTransport.Create(cert);
            Console.WriteLine("Conexão SEFAZ: TLS 1.2; certificado selecionado; validação de servidor e revogação ativas.");
            using var client=new HttpClient(handler){Timeout=TimeSpan.FromSeconds(90)};
            // Fixed official endpoint: neither browser nor task can choose a remote URL.
            using var request=FiscalRequest.Create(task,cert);
            using var deadline=CancellationTokenSource.CreateLinkedTokenSource(token);deadline.CancelAfter(TimeSpan.FromSeconds(90));
            stage="https_send";
            var receipt=await FiscalRequest.Receive(client,request,deadline.Token,task.Service=="nfse");
            return new(task.Id,true,Convert.ToBase64String(receipt.Body),Service:task.Service,HttpStatus:receipt.Status);
        }
        catch(Exception ex) when(ex is not OperationCanceledException || !token.IsCancellationRequested)
        {var code=ConnectionDiagnostic.Code(ex); Console.Error.WriteLine($"SEFAZ etapa={stage}: {code}. {ConnectionDiagnostic.Detail(ex)}");return new(task.Id,false,Error:code,Stage:stage,Diagnostics:ConnectionDiagnostic.Facts(ex),Service:task.Service);}
    }
}
