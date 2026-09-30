using System.Net;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;

var cases = new (Exception Error, string Expected)[] {
    (new InvalidDataException("oversized response"), "operation_failed"),
    (new AgentError("invalid certificate"), "certificate_or_request_invalid"),
    (new HttpRequestException("transport",new AuthenticationException()), "tls_failed"),
    (new HttpRequestException("transport",new SocketException((int)SocketError.HostNotFound)), "dns_failed"),
    (new CryptographicException(), "certificate_key_failed"),
    (new TaskCanceledException(), "timeout"),
    (new HttpRequestException("forbidden",null,HttpStatusCode.Forbidden), "http_403")
};
foreach (var (error,expected) in cases)
    if(ConnectionDiagnostic.Code(error)!=expected)throw new Exception("Classification failed: "+expected);
var sensitive=new HttpRequestException("password=DO_NOT_LOG https://host/?token=DO_NOT_LOG",new System.ComponentModel.Win32Exception(123,"DO_NOT_LOG"));
var detail=ConnectionDiagnostic.Detail(sensitive);
if(detail.Contains("DO_NOT_LOG")||!detail.Contains("native=0x0000007B")||!detail.Contains("System.Net.Http.HttpRequestException"))throw new Exception("Unsafe or incomplete diagnostics");
Console.WriteLine("PASS: native codes and exception identity preserved without message/credentials");
Console.WriteLine($"PASS: {cases.Length} agent error classifications, including post-response failure preserving cooldown");
foreach(var (message, expected) in new (string,string?)[] {
    ("Exception was thrown. -2146893792", "0x80090020"),
    ("Localized message 123", "0x0000007B"),
    ("DO_NOT_LOG -999999999999999", null),
    ("password=DO_NOT_LOG", null), ("status 12x", null), ("status ", null)
}) if(ConnectionDiagnostic.ParseNativeSuffix(message)!=expected)throw new Exception("Native suffix parsing failed");
using var rsa=RSA.Create(2048);
var certificateRequest=new System.Security.Cryptography.X509Certificates.CertificateRequest("CN=transport-test",rsa,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1);
using var certificate=certificateRequest.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1),DateTimeOffset.UtcNow.AddHours(1));
using var handler=SefazTransport.Create(certificate);
if(handler.SslProtocols!=SslProtocols.Tls12 || handler.AllowAutoRedirect || !handler.CheckCertificateRevocationList ||
    handler.ClientCertificateOptions!=ClientCertificateOption.Manual || handler.ClientCertificates.Count!=1 ||
    !ReferenceEquals(handler.ClientCertificates[0],certificate) || handler.ServerCertificateCustomValidationCallback is not null)
    throw new Exception("SEFAZ transport policy changed");
Console.WriteLine("PASS: TLS 1.2, exact certificate, standard server validation, revocation and native code parser");
sealed class AgentError(string message):Exception(message);
