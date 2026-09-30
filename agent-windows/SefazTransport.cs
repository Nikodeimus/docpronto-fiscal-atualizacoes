using System.Net.Http;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;

static class SefazTransport
{
    internal static HttpClientHandler Create(X509Certificate2 certificate)
    {
        // NF-e MOC 7.0 §4.2.2 permits TLS 1.2. Keep this compatibility
        // profile local to SEFAZ; do not change Windows/global TLS policy.
        var handler = new HttpClientHandler {
            SslProtocols = SslProtocols.Tls12,
            AllowAutoRedirect = false,
            ClientCertificateOptions = ClientCertificateOption.Manual,
            CheckCertificateRevocationList = true
        };
        handler.ClientCertificates.Add(certificate);
        return handler;
    }
}
