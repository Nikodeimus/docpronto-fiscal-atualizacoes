using System.Security.Cryptography;
using System.Text;

// Only query results are retained. Never serialize AgentTask: it may carry an A1 secret.
sealed class DistributionOutbox
{
    readonly string directory;
    internal DistributionOutbox(string profilePath, PairedConfig config)
    {
        string identity=ConnectedAgent.ValidateServer(config.ServerUrl).AbsoluteUri+"\n"+config.CompanyId+"\n"+config.Token+"\n"+Path.GetFullPath(profilePath).ToUpperInvariant();
        directory=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(profilePath))!,"distribution-outbox",Hash(identity));
    }
    static string Hash(string value)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
    internal void Save(DistributionResult result)
    {
        if(string.IsNullOrWhiteSpace(result.Id)||result.Id.Length>128)throw new AgentError("Identificador de resultado inválido.");
        // SaveProtected atomically replaces with a write-through, owner-only Windows ACL file.
        ConnectedAgent.SaveProtected(Path.Combine(directory,Hash(result.Id)+".json"),result);
    }
    internal async Task Replay(Func<DistributionResult,CancellationToken,Task<bool>> send,CancellationToken token)
    {
        if(!Directory.Exists(directory))return;
        foreach(string path in Directory.GetFiles(directory,"*.json").OrderBy(File.GetLastWriteTimeUtc))
        {
            token.ThrowIfCancellationRequested();
            var result=ConnectedAgent.LoadProtected<DistributionResult>(path,32*1024*1024);
            if(Path.GetFileNameWithoutExtension(path)!=Hash(result.Id))throw new AgentError("Resultado pendente inválido; arquivo preservado para diagnóstico.");
            if(!await send(result,token))throw new AgentError("Resultado preservado: a central ainda não confirmou o recebimento.");
            File.Delete(path);
        }
    }
}
