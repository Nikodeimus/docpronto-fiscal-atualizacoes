using System.Net;
using System.Net.Http.Headers;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

static class ConnectedAgent
{
    internal static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, PropertyNameCaseInsensitive = true, WriteIndented = true };
    static string ConfigPath(string? path) => Path.GetFullPath(path ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DocPronto", "agent.json"));

    internal static Uri ValidateServer(string url)
    {
        if (!Uri.TryCreate(url.Trim().TrimEnd('/') + "/", UriKind.Absolute, out var uri) || uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0)
            throw new AgentError("URL do servidor inválida: não inclua credenciais, parâmetros ou fragmentos.");
        bool local = uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase) ||
            (IPAddress.TryParse(uri.Host.Trim('[', ']'), out var address) && IPAddress.IsLoopback(address));
        if (uri.Scheme != "https" && !(uri.Scheme == "http" && local))
            throw new AgentError("Servidor precisa de HTTPS; HTTP só é permitido para localhost/loopback.");
        return uri;
    }

    static HttpClient Client(Uri server, string? token = null)
    {
        var handler = new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false, UseProxy = false, CheckCertificateRevocationList = true };
        var client = new HttpClient(handler) { BaseAddress = server, Timeout = TimeSpan.FromSeconds(60) };
        if (token is not null) client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    public static async Task Pair(string? path)
    {
        if (Console.IsInputRedirected) throw new AgentError("Pareamento requer terminal interativo: código e token não são aceitos em argumentos.");
        Console.Write("URL do seu servidor DocPronto: ");
        Uri server = ValidateServer(Console.ReadLine() ?? "");
        Console.Write("Código de pareamento exibido no DocPronto (oculto): ");
        string code = SecretLine();
        if (code.Length is < 4 or > 256) throw new AgentError("Código de pareamento inválido.");
        Console.Write("Nome para identificar este computador [Agente Windows]: ");
        string name = Console.ReadLine()?.Trim() ?? "";
        if (name.Length == 0) name = "Agente Windows";
        if (name.Length > 100) throw new AgentError("Nome deve ter até 100 caracteres.");
        Console.Write("Pasta para importar automaticamente PDFs/XMLs baixados (Enter desativa): ");
        string folder = Console.ReadLine()?.Trim().Trim('"') ?? "";
        if (folder.Length > 0)
        {
            folder = Path.GetFullPath(folder);
            if (!Directory.Exists(folder)) throw new AgentError("A pasta escolhida não existe.");
        }
        await PairValues(server.AbsoluteUri, code, name, folder, path);
    }

    internal static async Task PairValues(string url, string code, string name, string folder, string? path)
    {
        var server = ValidateServer(url);
        if (string.IsNullOrWhiteSpace(code) || code.Length > 256) throw new AgentError("Código inválido.");
        if (folder.Length > 0 && !Directory.Exists(folder)) throw new AgentError("Pasta não encontrada.");
        using var client = Client(server);
        using var response = await PostJson(client, "api/agent/pair", new { code, name }, CancellationToken.None);
        var pairing = response.RootElement.Deserialize<PairResponse>(Json) ?? throw new AgentError("Resposta de pareamento inválida.");
        if (string.IsNullOrWhiteSpace(pairing.Token) || pairing.Token.Length > 4096 || pairing.Token.Any(char.IsWhiteSpace) || string.IsNullOrWhiteSpace(pairing.CompanyId))
            throw new AgentError("Servidor não retornou token e empresa válidos.");
        var config = new PairedConfig { ServerUrl = server.AbsoluteUri, Token = pairing.Token, CompanyId = pairing.CompanyId,
            CompanyName = pairing.CompanyName, WatchFolder = folder.Length == 0 ? null : folder };
        SaveProtected(ConfigPath(path), config);
        Console.WriteLine("Pareamento concluído. Abra o aplicativo DocPronto Agente para conectar.");
    }

    static string SecretLine()
    {
        var buffer = new StringBuilder();
        while (true)
        {
            var key = Console.ReadKey(true);
            if (key.Key == ConsoleKey.Enter) { Console.WriteLine(); return buffer.ToString().Trim(); }
            if (key.Key == ConsoleKey.Backspace) { if (buffer.Length > 0) buffer.Length--; continue; }
            if (!char.IsControl(key.KeyChar) && buffer.Length < 256) buffer.Append(key.KeyChar);
        }
    }

    internal static void SaveProtected<T>(string path, T value)
    {
        string directory = Path.GetDirectoryName(path) ?? throw new AgentError("Caminho de configuração inválido.");
        Directory.CreateDirectory(directory);
        using var identity = WindowsIdentity.GetCurrent();
        var sid = identity.User ?? throw new AgentError("Não foi possível identificar usuário Windows.");
        var security = new FileSecurity();
        security.SetOwner(sid);
        security.SetAccessRuleProtection(true, false);
        security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, AccessControlType.Allow));
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileInfo(temporary).Create(FileMode.CreateNew, FileSystemRights.FullControl, FileShare.None, 4096, FileOptions.WriteThrough, security))
                JsonSerializer.Serialize(stream, value, Json);
            File.Move(temporary, path, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    internal static T LoadProtected<T>(string path, long maxBytes = 65536)
    {
        var info = new FileInfo(path);
        if (!info.Exists || info.Length > maxBytes || info.Attributes.HasFlag(FileAttributes.ReparsePoint))
            throw new AgentError("Conexão salva indisponível. Use Conectar computador no site.");
        using var identity = WindowsIdentity.GetCurrent();
        var sid = identity.User ?? throw new AgentError("Usuário Windows não identificado.");
        var security = info.GetAccessControl(AccessControlSections.Access | AccessControlSections.Owner);
        if (!security.AreAccessRulesProtected || !sid.Equals(security.GetOwner(typeof(SecurityIdentifier))))
            throw new AgentError("Configuração sem proteção individual. Use Conectar computador no site.");
        foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (rule.AccessControlType == AccessControlType.Allow && !sid.Equals(rule.IdentityReference))
                throw new AgentError("Configuração permite acesso a outro usuário/grupo. Use Conectar computador no site.");
        return JsonSerializer.Deserialize<T>(File.ReadAllText(path), Json) ?? throw new AgentError("Configuração vazia.");
    }

    public static async Task Run(string? path)
    {
        string configPath = ConfigPath(path);
        var config = LoadProtected<PairedConfig>(configPath);
        if (string.IsNullOrWhiteSpace(config.Token) || string.IsNullOrWhiteSpace(config.CompanyId)) throw new AgentError("Pareamento incompleto. Execute pair.");
        using var client = Client(ValidateServer(config.ServerUrl), config.Token);
        using var cancellation = new CancellationTokenSource();
        ConsoleCancelEventHandler cancelHandler = (_, e) => { e.Cancel = true; cancellation.Cancel(); };
        Console.CancelKeyPress += cancelHandler;
        var recentResults = new Dictionary<string, TestResult>(StringComparer.Ordinal);
        using var watcher = new FolderImporter(config, configPath);
        var outbox = new DistributionOutbox(configPath, config);
        DistributionResult? pendingToSave = null;
        async Task<bool> SendResult(DistributionResult result, CancellationToken token)
        {
            using var receipt = await PostJson(client, result.Service is null ? "api/agent/distribution-result" : "api/agent/fiscal-result", result, token);
            return receipt.RootElement.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True;
        }
        int failures = 0; bool connected = false;
        Console.WriteLine("Iniciando conexão. O site pode solicitar testes e consultas SEFAZ com o certificado.");
        try
        {
            while (!cancellation.IsCancellationRequested)
            {
                try
                {
                    if (pendingToSave is not null) { outbox.Save(pendingToSave); pendingToSave = null; }
                    await outbox.Replay(SendResult, cancellation.Token);
                    var inventory = Inventory();
                    using var reply = await PostJson(client, "api/agent/poll", new { certificates = inventory, capabilities = new[] { "distribution", "self_test", "stored_a1", "cte_distribution", "manifest_science", "nfse_distribution" } }, cancellation.Token);
                    var poll = reply.RootElement.Deserialize<PollResponse>(Json) ?? throw new AgentError("Resposta inválida do servidor.");
                    Console.WriteLine("DOCPRONTO_STATUS:" + JsonSerializer.Serialize(new AgentStatus(inventory, poll.Bindings ?? new()), new JsonSerializerOptions(Json) { WriteIndented = false }));
                    if (!connected) { Console.WriteLine("Conectado à central. Aguardando solicitações."); connected = true; }
                    if (poll.Task is AgentTask distributionTask && distributionTask.Kind is "distribution" or "fiscal_query")
                    {
                        Console.WriteLine($"Consulta CNPJ/CPF {distributionTask.Document} · certificado {(string.IsNullOrEmpty(distributionTask.Pfx) ? distributionTask.Thumbprint : "A1 salvo no cadastro")} · repositório {distributionTask.Store}");
                        var distributionResult = await SefazClient.Query(distributionTask, cancellation.Token);
                        pendingToSave = distributionResult;
                        outbox.Save(pendingToSave);
                        pendingToSave = null;
                        await outbox.Replay(SendResult, cancellation.Token);
                        Console.WriteLine(distributionResult.Ok ? "Consulta SEFAZ recebida pela central." : "Consulta SEFAZ falhou. Confira certificado e conexão.");
                    }
                    else if (poll.Task is AgentTask task)
                    {
                        if (string.IsNullOrWhiteSpace(task.Id) || task.Id.Length > 128) throw new AgentError("Identificador de tarefa inválido.");
                        if (!recentResults.TryGetValue(task.Id, out var result))
                        {
                            result = Execute(task);
                            if (recentResults.Count >= 100) recentResults.Remove(recentResults.Keys.First());
                            recentResults[task.Id] = result;
                        }
                        using var acknowledged = await PostJson(client, "api/agent/result", result, cancellation.Token);
                        Console.WriteLine(result.Ok ? "Teste criptográfico enviado ao DocPronto." : "Teste recusado ou indisponível; motivo enviado ao DocPronto.");
                    }
                    await watcher.Process(client, cancellation.Token);
                    failures = 0;
                }
                catch (AgentAuthorizationError) { throw new AgentError("Conexão removida ou expirada. No site, clique em Conectar computador; depois remova esta conexão antiga na janela do conector."); }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { break; }
                catch (Exception ex)
                {
                    connected = false; failures = Math.Min(failures + 1, 4);
                    Console.Error.WriteLine($"{DateTime.Now:HH:mm:ss} Central DocPronto/operação: {ConnectionDiagnostic.Code(ex)}. {ConnectionDiagnostic.Detail(ex)}");
                }
                try { await Task.Delay(TimeSpan.FromSeconds(Math.Min(60, 5 * (1 << failures))), cancellation.Token); }
                catch (OperationCanceledException) { break; }
            }
        }
        finally { Console.CancelKeyPress -= cancelHandler; }
        Console.WriteLine("Agente encerrado.");
    }

    static List<CertificateInfo> Inventory()
    {
        var result = new List<CertificateInfo>();
        foreach (var location in new[] { StoreLocation.CurrentUser, StoreLocation.LocalMachine })
        {
            try
            {
                using var store = new X509Store(StoreName.My, location);
                store.Open(OpenFlags.ReadOnly | OpenFlags.OpenExistingOnly);
                foreach (var cert in store.Certificates)
                    using (cert)
                        if (result.Count < 1000) result.Add(new CertificateInfo(cert.Thumbprint, cert.Subject, cert.NotAfter.ToUniversalTime().ToString("O"), cert.HasPrivateKey, location.ToString()));
            }
            catch (CryptographicException) { Console.Error.WriteLine("Um repositório de certificados está indisponível."); }
            catch (UnauthorizedAccessException) { Console.Error.WriteLine("Um repositório de certificados não permite leitura."); }
        }
        return result;
    }

    internal static TestResult Execute(AgentTask task)
    {
        try
        {
            if (task.Kind != "self_test") return new(task.Id, false, Error: "unsupported_task");
            byte[] challenge;
            try { challenge = Convert.FromBase64String(task.Challenge); }
            catch (FormatException) { return new(task.Id, false, Error: "invalid_challenge"); }
            if (challenge.Length != 32) return new(task.Id, false, Error: "invalid_challenge");
            var selection = new Dictionary<string, string> { ["thumbprint"] = task.Thumbprint };
            if (!string.IsNullOrEmpty(task.Store)) selection["store"] = task.Store;
            using var certificate = Agent.Select(selection);
            Agent.CheckUsable(certificate);
            return SignChallenge(task.Id, certificate, challenge);
        }
        catch (CryptographicException) { return new(task.Id, false, Error: "provider_pin_or_key_unavailable"); }
        catch (AgentError) { return new(task.Id, false, Error: "certificate_missing_expired_or_unusable"); }
        catch (Exception) { return new(task.Id, false, Error: "self_test_failed"); }
    }

    internal static TestResult SignChallenge(string id, X509Certificate2 certificate, byte[] challenge)
    {
        if (challenge.Length != 32) throw new AgentError("Desafio deve conter 32 bytes.");
            using RSA? rsa = certificate.GetRSAPrivateKey();
            byte[] signature;
            string algorithm;
            if (rsa is not null)
            {
                signature = rsa.SignData(challenge, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
                using var publicKey = certificate.GetRSAPublicKey();
                if (publicKey is null || !publicKey.VerifyData(challenge, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1)) return new(id, false, Error: "signature_verification_failed");
                algorithm = "RSA-SHA256";
            }
            else
            {
                using ECDsa? ec = certificate.GetECDsaPrivateKey();
                if (ec is null) return new(id, false, Error: "unsupported_key_algorithm");
                signature = ec.SignData(challenge, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence);
                using var publicKey = certificate.GetECDsaPublicKey();
                if (publicKey is null || !publicKey.VerifyData(challenge, signature, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence)) return new(id, false, Error: "signature_verification_failed");
                algorithm = "ECDSA-SHA256";
            }
            return new(id, true, Convert.ToBase64String(signature), Convert.ToBase64String(certificate.RawData), algorithm);
    }

    internal static async Task<JsonDocument> PostJson(HttpClient client, string endpoint, object value, CancellationToken token)
    {
        using var content = new StringContent(JsonSerializer.Serialize(value, Json), Encoding.UTF8, "application/json");
        return await Send(client, endpoint, content, token);
    }

    internal static async Task<JsonDocument> Send(HttpClient client, string endpoint, HttpContent content, CancellationToken token)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(60));
        token = deadline.Token;
        using var request = new HttpRequestMessage(HttpMethod.Post, endpoint) { Content = content };
        using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token);
        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden) throw new AgentAuthorizationError();
        if (!response.IsSuccessStatusCode)
        {
            Console.Error.WriteLine($"Central DocPronto: HTTP {(int)response.StatusCode} na operação {endpoint}.");
            throw new HttpRequestException("Servidor recusou operação", null, response.StatusCode);
        }
        if (response.Content.Headers.ContentLength is > 65536) throw new AgentError("Resposta do servidor excede limite.");
        await using var input = await response.Content.ReadAsStreamAsync(token);
        using var output = new MemoryStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = await input.ReadAsync(buffer, token)) > 0)
        {
            if (output.Length + count > 65536) throw new AgentError("Resposta do servidor excede limite.");
            output.Write(buffer, 0, count);
        }
        return JsonDocument.Parse(output.ToArray(), new JsonDocumentOptions { MaxDepth = 16 });
    }
}

sealed class PairedConfig
{
    public string ServerUrl { get; set; } = "";
    public string Token { get; set; } = "";
    public string CompanyId { get; set; } = "";
    public string? CompanyName { get; set; }
    [JsonPropertyName("watchFolder")]
    public string? WatchFolder { get; set; }
}
sealed record PairResponse(string Token, string CompanyId, string? CompanyName);
sealed record CertificateInfo(string Thumbprint, string Subject, string ValidUntil, bool HasPrivateKey, string Store);
sealed record CertificateBinding(string CompanyId, string CompanyName, string Document, string Thumbprint, string Store);
sealed record AgentStatus(List<CertificateInfo> Certificates, List<CertificateBinding> Bindings);
sealed record PollResponse(AgentTask? Task, List<CertificateBinding>? Bindings = null);
sealed record AgentTask(string Id, string Kind, string Thumbprint, string Challenge, string? Store = null, string? Document = null, string? Uf = null, string? Nsu = null, string? Key = null, string? Pfx = null, string? PfxPassword = null, string? Service = null, string? EventCode = null, string? EventTime = null, bool Consent = false);
sealed record TestResult(string Id, bool Ok, string? Signature = null, string? CertificateDer = null, string? Algorithm = null, string? Error = null);
sealed class AgentAuthorizationError : Exception;
