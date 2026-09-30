using System.Text;
using System.Text.Json;

static class DistributionOutboxTests
{
    public static async Task<int> Run()
    {
        string root=Path.Combine(Path.GetTempPath(),"docpronto-outbox-test-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        int checks=0;
        void Check(bool value,string name){if(!value)throw new AgentError("Teste falhou: "+name);checks++;}
        try
        {
            var config=new PairedConfig{ServerUrl="http://127.0.0.1:8080/",CompanyId="synthetic",Token="synthetic-token"};
            string path=Path.Combine(root,"agent.json");
            var saved=new DistributionResult("synthetic-task",true,Convert.ToBase64String(Encoding.UTF8.GetBytes("<synthetic/>")));
            var queue=new DistributionOutbox(path,config);
            queue.Save(saved);
            // Recreate the queue as after process termination; transport fails before acknowledgement.
            queue=new DistributionOutbox(path,config);
            int attempts=0;
            try{await queue.Replay((r,t)=>{attempts++;throw new HttpRequestException("synthetic disconnect");},CancellationToken.None);}catch(HttpRequestException){}
            Check(attempts==1,"persisted result replay after restart");
            try{await queue.Replay((r,t)=>Task.FromResult(false),CancellationToken.None);}catch(AgentError){}
            int received=0;
            await queue.Replay((r,t)=>{Check(r==saved,"response preserved after failed delivery and missing ACK");received++;return Task.FromResult(true);},CancellationToken.None);
            await queue.Replay((r,t)=>{received++;return Task.FromResult(true);},CancellationToken.None);
            Check(received==1,"ACK removes result and prevents duplicate send");
            queue.Save(saved);
            var other=new DistributionOutbox(path,new PairedConfig{ServerUrl=config.ServerUrl,CompanyId=config.CompanyId,Token="other-token"});
            await other.Replay((r,t)=>{throw new AgentError("Wrong profile replay");},CancellationToken.None);
            Check(true,"new pairing cannot replay previous pairing");
            var otherServer=new DistributionOutbox(path,new PairedConfig{ServerUrl="https://example.test",CompanyId=config.CompanyId,Token=config.Token});
            await otherServer.Replay((r,t)=>{throw new AgentError("Wrong server replay");},CancellationToken.None);
            Check(true,"new server cannot replay previous server");
            // The same ACK may be lost after server acceptance: keep and resend identical result.
            try{await queue.Replay((r,t)=>{Check(r==saved,"same result after accepted response lost");throw new IOException("ACK lost");},CancellationToken.None);}catch(IOException){}
            await new DistributionOutbox(path,config).Replay((r,t)=>{Check(r==saved,"replay remains identical");return Task.FromResult(true);},CancellationToken.None);
            queue.Save(saved);
            var payload=Directory.GetFiles(root,"*.json",SearchOption.AllDirectories).Single();
            using var json=JsonDocument.Parse(File.ReadAllText(payload));
            Check(json.RootElement.EnumerateObject().All(p=>p.Name is "id" or "ok" or "response" or "error" or "stage" or "diagnostics" or "service" or "http_status"),"only result stored; no PFX, password, token or task");
            Check(ConnectedAgent.LoadProtected<DistributionResult>(payload)==saved,"individual Windows ACL verified");
            await queue.Replay((r,t)=>Task.FromResult(true),CancellationToken.None);
            queue.Save(saved with{Service="cte"});
            await new DistributionOutbox(path,config).Replay((r,t)=>{Check(r.Service=="cte","fiscal channel preserved across restart");return Task.FromResult(true);},CancellationToken.None);
            Console.WriteLine($"PASS: {checks} verificações de spool, reinício, ACK, isolamento e ACL Windows; dados sintéticos, sem SEFAZ.");
            return 0;
        }
        finally { Directory.Delete(root,true); }
    }
}
