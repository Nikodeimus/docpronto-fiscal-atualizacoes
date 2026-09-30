using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;

if (OperatingSystem.IsWindows() && (args is ["--install"] || Path.GetFileNameWithoutExtension(Environment.ProcessPath!).Equals("DocPronto-Instalar",StringComparison.OrdinalIgnoreCase)))
{ var installer=new Thread(DesktopLink.Install);installer.SetApartmentState(ApartmentState.STA);installer.Start();installer.Join();return 0; }
if (args is ["--wait-desktop"]) { Thread.Sleep(2500);args=[]; }
if (OperatingSystem.IsWindows() && args.Length==1 && args[0].StartsWith("docpronto:",StringComparison.OrdinalIgnoreCase))
{ DesktopLink.Initial=args[0];args=[]; }
if ((args.Length == 0 || args is ["--background"]) && OperatingSystem.IsWindows())
{
    var thread = new Thread(AgentDesktop.Open);
    thread.SetApartmentState(ApartmentState.STA);
    thread.Start(); thread.Join(); return 0;
}
return await Agent.Run(args);

static class Agent
{
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true, PropertyNameCaseInsensitive = true };
    static readonly StoreLocation[] Stores = [StoreLocation.CurrentUser, StoreLocation.LocalMachine];

    public static async Task<int> Run(string[] args)
    {
        try
        {
            if (args is ["protocol-test"]) return ProtocolSelfTests.Run();
            if (args is ["outbox-test"]) return await DistributionOutboxTests.Run();
            if (!OperatingSystem.IsWindows()) throw new AgentError("Este agente requer Windows e o provedor CSP/KSP do certificado.");
            if (args.Length == 0 || args[0] is "help" or "--help")
            {
                Console.WriteLine("pair [--config CONFIG] | connect [--config CONFIG] | run [--config CONFIG] | list | test --thumbprint HEX [--store CurrentUser|LocalMachine] | fetch --thumbprint HEX --url HTTPS --output ARQUIVO --config CONFIG [--store CurrentUser|LocalMachine]");
                return 0;
            }
            var options = Parse(args.Skip(1).ToArray());
            switch (args[0])
            {
                case "pair":
                    RequireOnly(options, "config");
                    await ConnectedAgent.Pair(options.GetValueOrDefault("config"));
                    break;
                case "connect":
                case "run":
                    RequireOnly(options, "config");
                    await ConnectedAgent.Run(options.GetValueOrDefault("config"));
                    break;
                case "list":
                    RequireOnly(options);
                    ListCertificates();
                    break;
                case "test":
                    RequireOnly(options, "thumbprint", "store");
                    using (var cert = Select(options))
                    {
                        CheckUsable(cert);
                        SelfTest(cert);
                        Console.WriteLine("Teste local concluído: assinatura de desafio aleatório verificada. Não comprova autorização em serviço fiscal nem revogação.");
                    }
                    break;
                case "fetch":
                    RequireOnly(options, "thumbprint", "store", "url", "output", "config");
                    await Fetch(options);
                    break;
                default: throw new AgentError("Comando desconhecido. Use --help.");
            }
            return 0;
        }
        catch (AgentError e) { Console.Error.WriteLine(e.Message); return 2; }
        catch (CryptographicException) { Console.Error.WriteLine("Falha criptográfica: confira token, driver CSP/KSP, PIN e permissão da chave. O PIN pertence à interface do provedor."); return 3; }
        catch (HttpRequestException) { Console.Error.WriteLine("Falha HTTPS/mTLS: confira conectividade, cadeia de confiança e autorização do certificado no destino."); return 4; }
        catch (OperationCanceledException) { Console.Error.WriteLine("Operação cancelada ou tempo limite excedido."); return 5; }
        catch (Exception) { Console.Error.WriteLine("Não foi possível concluir. Confira argumentos, JSON de configuração, caminho de saída e permissões locais. Detalhes sensíveis não são registrados."); return 6; }
    }

    static Dictionary<string, string> Parse(string[] args)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        if (args.Length % 2 != 0) throw new AgentError("Cada opção requer um valor.");
        for (int i = 0; i < args.Length; i += 2)
            if (!args[i].StartsWith("--") || !result.TryAdd(args[i][2..], args[i + 1]))
                throw new AgentError("Opção inválida ou duplicada.");
        return result;
    }
    static string Required(Dictionary<string, string> options, string key) =>
        options.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value) ? value : throw new AgentError($"Falta --{key}.");
    static void RequireOnly(Dictionary<string, string> options, params string[] names)
    {
        if (options.Keys.Any(key => !names.Contains(key))) throw new AgentError("Opção não reconhecida para este comando.");
    }

    static void ListCertificates()
    {
        var result = new List<object>();
        foreach (var location in Stores)
        {
            try
            {
                using var store = new X509Store(StoreName.My, location);
                store.Open(OpenFlags.ReadOnly | OpenFlags.OpenExistingOnly);
                foreach (var cert in store.Certificates)
                {
                    using (cert)
                        result.Add(new { store = location.ToString(), cert.Thumbprint, cert.Subject, cert.NotBefore, cert.NotAfter, cert.HasPrivateKey,
                            providerType = "A1/A3 não inferido: confirmar no provedor; execute test para provar acesso à chave" });
                }
            }
            catch (CryptographicException) { Console.Error.WriteLine($"Repositório {location}/My indisponível para este usuário."); }
        }
        Console.WriteLine(JsonSerializer.Serialize(result, Json));
    }

    internal static X509Certificate2 Select(Dictionary<string, string> options)
    {
        string thumbprint = Required(options, "thumbprint").Replace(" ", "").ToUpperInvariant();
        if (thumbprint.Length != 40 || thumbprint.Any(c => !Uri.IsHexDigit(c))) throw new AgentError("Thumbprint deve conter 40 dígitos hexadecimais.");
        var locations = Stores;
        if (options.TryGetValue("store", out var name))
        {
            if (name != "CurrentUser" && name != "LocalMachine") throw new AgentError("Store deve ser CurrentUser ou LocalMachine.");
            locations = [Enum.Parse<StoreLocation>(name)];
        }
        foreach (var location in locations)
        {
            using var store = new X509Store(StoreName.My, location);
            store.Open(OpenFlags.ReadOnly | OpenFlags.OpenExistingOnly);
            var certificates = store.Certificates;
            X509Certificate2? selected = null;
            foreach (var cert in certificates)
            {
                if (selected is null && cert.Thumbprint.Equals(thumbprint, StringComparison.OrdinalIgnoreCase)) selected = cert;
                else cert.Dispose();
            }
            if (selected is not null) return selected;
        }
        throw new AgentError("Certificado não encontrado nos repositórios selecionados.");
    }

    internal static void CheckUsable(X509Certificate2 cert)
    {
        if (!cert.HasPrivateKey) throw new AgentError("Certificado sem associação à chave privada. Instale A1 ou o driver/token A3 no Windows.");
        if (DateTime.Now < cert.NotBefore || DateTime.Now > cert.NotAfter) throw new AgentError("Certificado fora do período de validade.");
        foreach (var extension in cert.Extensions.OfType<X509KeyUsageExtension>())
            if (!extension.KeyUsages.HasFlag(X509KeyUsageFlags.DigitalSignature)) throw new AgentError("O certificado não permite assinatura digital necessária ao cliente TLS.");
    }

    internal static void SelfTest(X509Certificate2 cert)
    {
        byte[] challenge = RandomNumberGenerator.GetBytes(32);
        using RSA? rsa = cert.GetRSAPrivateKey();
        if (rsa is not null)
        {
            byte[] signature = rsa.SignData(challenge, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            using RSA? publicKey = cert.GetRSAPublicKey();
            if (publicKey is null || !publicKey.VerifyData(challenge, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1))
                throw new AgentError("Assinatura do desafio não corresponde ao certificado.");
            return;
        }
        using ECDsa? ec = cert.GetECDsaPrivateKey();
        if (ec is null) throw new AgentError("Algoritmo não suportado: é necessário RSA ou ECDSA.");
        byte[] ecSignature = ec.SignData(challenge, HashAlgorithmName.SHA256);
        using ECDsa? ecPublic = cert.GetECDsaPublicKey();
        if (ecPublic is null || !ecPublic.VerifyData(challenge, ecSignature, HashAlgorithmName.SHA256))
            throw new AgentError("Assinatura do desafio não corresponde ao certificado.");
    }

    static async Task Fetch(Dictionary<string, string> options)
    {
        var config = JsonSerializer.Deserialize<AgentConfig>(await File.ReadAllTextAsync(Required(options, "config")), Json)
            ?? throw new AgentError("Configuração vazia.");
        if (!Uri.TryCreate(Required(options, "url"), UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Port != 443 ||
            uri.HostNameType != UriHostNameType.Dns || uri.IsLoopback || uri.UserInfo.Length != 0 || uri.Fragment.Length != 0)
            throw new AgentError("Destino deve ser HTTPS, host DNS, porta 443, sem credenciais ou fragmento.");
        if (config.AllowedHosts is null || !config.AllowedHosts.Any(host => string.Equals(host, uri.IdnHost, StringComparison.OrdinalIgnoreCase)))
            throw new AgentError("Host não autorizado em allowedHosts. Correspondência exata; curingas não são aceitos.");
        if (config.AllowedPathPrefixes is null || config.AllowedPathPrefixes.Length == 0 ||
            !config.AllowedPathPrefixes.Any(path => path.StartsWith('/') &&
                (uri.AbsolutePath == path || uri.AbsolutePath.StartsWith(path.TrimEnd('/') + "/", StringComparison.Ordinal))))
            throw new AgentError("Caminho não autorizado em allowedPathPrefixes.");
        if (config.TimeoutSeconds < 1 || config.TimeoutSeconds > 300 || config.MaxResponseBytes < 1 || config.MaxResponseBytes > 104857600)
            throw new AgentError("Limites inválidos: timeout 1–300 segundos e resposta até 100 MiB.");
        string output = Path.GetFullPath(Required(options, "output"));
        if (File.Exists(output)) throw new AgentError("Arquivo de saída já existe; escolha outro nome.");
        using var cert = Select(options);
        CheckUsable(cert);
        using var handler = new HttpClientHandler { AllowAutoRedirect = false, ClientCertificateOptions = ClientCertificateOption.Manual,
            CheckCertificateRevocationList = true, UseCookies = false, UseProxy = false };
        handler.ClientCertificates.Add(cert);
        using var client = new HttpClient(handler);
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(config.TimeoutSeconds));
        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellation.Token);
        if ((int)response.StatusCode >= 300 && (int)response.StatusCode < 400) throw new AgentError("Redirecionamento bloqueado. Configure o endpoint final autorizado.");
        if (!response.IsSuccessStatusCode) throw new AgentError($"Destino respondeu HTTP {(int)response.StatusCode}. Nenhum arquivo foi salvo.");
        if (response.Content.Headers.ContentLength is long length && length > config.MaxResponseBytes) throw new AgentError("Resposta excede limite configurado.");
        string temporary = output + "." + Guid.NewGuid().ToString("N") + ".part";
        try
        {
            await using (var input = await response.Content.ReadAsStreamAsync(cancellation.Token))
            await using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
            {
                var buffer = new byte[81920];
                long total = 0;
                int count;
                while ((count = await input.ReadAsync(buffer, cancellation.Token)) > 0)
                {
                    total += count;
                    if (total > config.MaxResponseBytes) throw new AgentError("Resposta excede limite configurado.");
                    await file.WriteAsync(buffer.AsMemory(0, count), cancellation.Token);
                }
            }
            File.Move(temporary, output, false);
            Console.WriteLine("Resposta salva. Valide o formato e importe o arquivo no DocPronto. HTTP 2xx não comprova que o servidor exigiu autenticação mTLS.");
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}

sealed class AgentConfig
{
    public string[] AllowedHosts { get; set; } = [];
    public string[] AllowedPathPrefixes { get; set; } = [];
    public int TimeoutSeconds { get; set; } = 60;
    public long MaxResponseBytes { get; set; } = 33554432;
}
sealed class AgentError(string message) : Exception(message);
