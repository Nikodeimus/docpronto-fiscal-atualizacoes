using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;

sealed class FolderImporter : IDisposable
{
    const long MaxBytes = 20 * 1024 * 1024;
    readonly PairedConfig config;
    readonly string receiptPath;
    readonly HashSet<string> receipts = new(StringComparer.Ordinal);
    readonly Queue<string> receiptOrder = new();
    readonly Dictionary<string, SeenFile> seen = new(StringComparer.OrdinalIgnoreCase);
    IEnumerator<string>? files;
    bool disabled;
    bool missingNotified;

    public FolderImporter(PairedConfig config, string configPath)
    {
        this.config = config;
        receiptPath = configPath + ".receipts.json";
        if (File.Exists(receiptPath))
        {
            try
            {
                foreach (var value in ConnectedAgent.LoadProtected<string[]>(receiptPath, 4 * 1024 * 1024).TakeLast(20000))
                    if (receipts.Add(value)) receiptOrder.Enqueue(value);
            }
            catch (Exception)
            {
                disabled = true;
                Console.Error.WriteLine("Importação automática desativada: recibos locais não puderam ser verificados. Certificados continuam disponíveis.");
            }
        }
    }

    public async Task Process(HttpClient client, CancellationToken token)
    {
        if (disabled || string.IsNullOrWhiteSpace(config.WatchFolder)) return;
        string folder = Path.GetFullPath(config.WatchFolder);
        if (!Directory.Exists(folder))
        {
            if (!missingNotified) Console.Error.WriteLine("A pasta configurada está indisponível; importação retomará quando ela voltar.");
            missingNotified = true;
            files?.Dispose(); files = null;
            return;
        }
        missingNotified = false;
        files ??= Directory.EnumerateFiles(folder, "*", SearchOption.TopDirectoryOnly).GetEnumerator();
        int scanned = 0;
        int attempted = 0;
        // A enumeração continua no próximo poll: pastas grandes não bloqueiam os testes A3.
        while (scanned++ < 100 && attempted < 10 && !token.IsCancellationRequested)
        {
            if (!files.MoveNext()) { files.Dispose(); files = null; break; }
            string path = files.Current;
            string extension = Path.GetExtension(path).ToLowerInvariant();
            if (extension is not ".pdf" and not ".xml") continue;
            try
            {
                var info = new FileInfo(path);
                if (info.Attributes.HasFlag(FileAttributes.ReparsePoint) || info.Length is <= 0 or > MaxBytes) continue;
                if (seen.TryGetValue(path, out var previous) && previous.Length == info.Length && previous.ModifiedUtc == info.LastWriteTimeUtc && receipts.Contains(previous.Receipt)) continue;
                // FileShare.Read impede alterações/exclusão durante hash + upload no Windows.
                await using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 81920, FileOptions.Asynchronous | FileOptions.SequentialScan);
                if (input.Length is <= 0 or > MaxBytes) continue;
                string hash = Convert.ToHexString(await SHA256.HashDataAsync(input, token));
                string receiptKey = config.CompanyId + ":" + hash;
                if (seen.Count >= 20000) seen.Clear();
                seen[path] = new SeenFile(info.Length, info.LastWriteTimeUtc, receiptKey);
                if (receipts.Contains(receiptKey)) continue;
                input.Position = 0;
                attempted++;
                using var form = new MultipartFormDataContent();
                using var upload = new StreamContent(input);
                upload.Headers.ContentType = new MediaTypeHeaderValue(extension == ".pdf" ? "application/pdf" : "application/xml");
                form.Add(upload, "file", Path.GetFileName(path));
                form.Add(new StringContent(config.CompanyId), "company_id");
                using var response = await ConnectedAgent.Send(client, "api/agent/import", form, token);
                var imported = response.RootElement.Deserialize<ImportResponse>(ConnectedAgent.Json);
                if (imported is null || !imported.Ok || string.IsNullOrWhiteSpace(imported.Id)) throw new AgentError("Servidor não confirmou importação.");
                receipts.Add(receiptKey);
                receiptOrder.Enqueue(receiptKey);
                if (receiptOrder.Count > 20000) receipts.Remove(receiptOrder.Dequeue());
                try { ConnectedAgent.SaveProtected(receiptPath, receiptOrder.ToArray()); }
                catch (Exception)
                {
                    receipts.Remove(receiptKey);
                    disabled = true;
                    Console.Error.WriteLine("Importação pausada: não foi possível persistir recibo local. O servidor deve deduplicar o reenvio; originais preservados.");
                    return;
                }
                Console.WriteLine("Arquivo da pasta importado e recibo gravado; original preservado.");
            }
            catch (AgentAuthorizationError) { throw; }
            catch (HttpRequestException) { throw; }
            catch (OperationCanceledException) { throw; }
            catch (IOException) { /* Arquivo ainda em gravação: tentar em outra passagem. */ }
            catch (UnauthorizedAccessException) { /* Não altera permissões de arquivos do usuário. */ }
            catch (AgentError) { Console.Error.WriteLine("Um arquivo não foi aceito pelo DocPronto. Sem recibo e sem exclusão; poderá ser reenviado após correção."); }
        }
    }

    public void Dispose() => files?.Dispose();
    sealed record SeenFile(long Length, DateTime ModifiedUtc, string Receipt);
    sealed record ImportResponse(bool Ok, string? Id, string? Key);
}
