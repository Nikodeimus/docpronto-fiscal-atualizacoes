using System.Net.Http;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;

static class ConnectionDiagnostic
{
    internal static string Code(Exception ex)
    {
        if(ex is HttpRequestException h && h.StatusCode is not null) return $"http_{(int)h.StatusCode}";
        for(Exception? e=ex; e is not null; e=e.InnerException)
        {
            if(e is AuthenticationException || IsSchannelInternal(e)) return "tls_failed";
            if(e is SocketException s) return s.SocketErrorCode is SocketError.HostNotFound or SocketError.NoData ? "dns_failed" : "connection_failed";
            if(e is CryptographicException) return "certificate_key_failed";
        }
        if(ex is OperationCanceledException) return "timeout";
        if(ex is HttpRequestException r) return r.HttpRequestError switch
        {
            HttpRequestError.NameResolutionError => "dns_failed",
            HttpRequestError.SecureConnectionError => "tls_failed",
            HttpRequestError.ConnectionError => "connection_failed",
            _ => "http_transport_failed"
        };
        if(ex is AgentError) return "certificate_or_request_invalid";
        return "operation_failed";
    }
    private static bool IsSchannelInternal(Exception ex) =>
        ex.GetType().FullName == "System.Net.InternalException" &&
        (ex.StackTrace?.Contains("System.Net.SecurityStatusAdapterPal.GetSecurityStatusPalFromInterop", StringComparison.Ordinal) ?? false);

    internal static string? ParseNativeSuffix(string message)
    {
        // .NET InternalException appends the unmapped SSPI numeric status to
        // its localized message. Export only that number, never the message.
        var suffix = message.AsSpan(message.LastIndexOf(' ') + 1);
        return int.TryParse(suffix, System.Globalization.NumberStyles.AllowLeadingSign,
            System.Globalization.CultureInfo.InvariantCulture, out var code) ? $"0x{code:X8}" : null;
    }
    private static string? NativeCode(Exception ex) => ex is System.ComponentModel.Win32Exception w
        ? $"0x{w.NativeErrorCode:X8}"
        : IsSchannelInternal(ex) ? ParseNativeSuffix(ex.Message) : null;

    internal static List<ErrorFact> Facts(Exception ex)
    {
        var result=new List<ErrorFact>();
        for(Exception? e=ex;e is not null&&result.Count<5;e=e.InnerException)
        {
            var frames=new System.Diagnostics.StackTrace(e,false).GetFrames() ?? Array.Empty<System.Diagnostics.StackFrame>();
            var sites=frames.Select(f=>f.GetMethod()).Where(m=>m?.DeclaringType?.FullName?.StartsWith("System.",StringComparison.Ordinal)==true)
                .Take(3).Select(m=>m!.DeclaringType!.FullName+"."+m.Name).ToArray();
            result.Add(new ErrorFact(e.GetType().FullName ?? e.GetType().Name,$"0x{e.HResult:X8}",
                NativeCode(e),
                e is HttpRequestException h?h.HttpRequestError.ToString():null,string.Join(" > ",sites)));
        }
        return result;
    }
    internal static string Detail(Exception ex)=>string.Join(" → ",Facts(ex).Select(e=>
        $"{e.Type} ({e.Hresult})"+(e.Native is null?"":$" native={e.Native}")+
        (e.HttpError is null?"":$" http={e.HttpError}")+(e.Site.Length==0?"":$" at {e.Site}")));
}
sealed record ErrorFact(string Type,string Hresult,string? Native,string? HttpError,string Site);
