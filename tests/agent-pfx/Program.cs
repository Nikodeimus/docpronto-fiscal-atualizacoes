using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Net;
using System.Net.Sockets;
using System.Net.Security;
using System.Security.Authentication;

if(A1Certificate.StorageFlags(true)!=X509KeyStorageFlags.UserKeySet)
    throw new Exception("Windows A1 must provide a temporary user key to Schannel");
if(A1Certificate.StorageFlags(false)!=X509KeyStorageFlags.EphemeralKeySet)
    throw new Exception("Non-Windows A1 must remain ephemeral");
using var rsa=RSA.Create(2048);
var request=new CertificateRequest("CN=DocPronto synthetic A1",rsa,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1);
using var generated=request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1),DateTimeOffset.UtcNow.AddDays(1));
const string password="synthetic-test-only";
byte[] pfx=generated.Export(X509ContentType.Pkcs12,password);
string? keyPath=null;
using(var loaded=A1Certificate.Load(pfx,password))
{
    if(!loaded.HasPrivateKey || loaded.Thumbprint!=generated.Thumbprint)throw new Exception("A1 identity/private key lost");
    using var privateKey=loaded.GetRSAPrivateKey()!;
    byte[] challenge=RandomNumberGenerator.GetBytes(32);
    byte[] signature=privateKey.SignData(challenge,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1);
    if(!rsa.VerifyData(challenge,signature,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1))throw new Exception("Loaded A1 cannot sign");
    if(OperatingSystem.IsWindows() && privateKey is RSACng cng)
    {
        if(cng.Key.IsEphemeral)throw new Exception("Schannel incompatible ephemeral key");
        keyPath=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),"Microsoft","Crypto","Keys",cng.Key.UniqueName!);
        if(!File.Exists(keyPath))throw new Exception("Temporary user key missing");
    }
}
if(keyPath is not null && File.Exists(keyPath))throw new Exception("Temporary A1 key persisted after disposal");
bool rejected=false;
try{using var invalid=A1Certificate.Load(pfx,"incorrect-synthetic-password");}
catch(CryptographicException){rejected=true;}
if(!rejected)throw new Exception("Incorrect password was accepted");
if(OperatingSystem.IsWindows())
{
    using var store=new X509Store(StoreName.My,StoreLocation.CurrentUser);
    store.Open(OpenFlags.ReadOnly);
    if(store.Certificates.Find(X509FindType.FindByThumbprint,generated.Thumbprint,false).Count!=0)
        throw new Exception("A1 was permanently installed in certificate store");
}
// Local mutual TLS uses synthetic identities pinned in this test only.
// Production server validation/revocation remain in SefazTransport unchanged.
using(var clientCertificate=A1Certificate.Load(pfx,password))
using(var serverKey=RSA.Create(2048))
{
    var serverRequest=new CertificateRequest("CN=localhost",serverKey,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1);
    using var generatedServer=serverRequest.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1),DateTimeOffset.UtcNow.AddDays(1));
    using var serverCertificate=A1Certificate.Load(generatedServer.Export(X509ContentType.Pkcs12,password),password);
    using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(15));
    using var listener=new TcpListener(IPAddress.Loopback,0);
    listener.Start();
    var server=Task.Run(async()=>
    {
        using var socket=await listener.AcceptTcpClientAsync(deadline.Token);
        using var tls=new SslStream(socket.GetStream(),false,(_,certificate,_,_)=>certificate?.GetCertHashString()==clientCertificate.Thumbprint);
        await tls.AuthenticateAsServerAsync(new SslServerAuthenticationOptions{ServerCertificate=serverCertificate,ClientCertificateRequired=true,EnabledSslProtocols=SslProtocols.Tls12,CertificateRevocationCheckMode=X509RevocationMode.NoCheck},deadline.Token);
        if(!tls.IsMutuallyAuthenticated)throw new Exception("Client certificate missing in TLS handshake");
        await tls.WriteAsync(new byte[]{42},deadline.Token);
    });
    using var connection=new TcpClient();
    await connection.ConnectAsync(IPAddress.Loopback,((IPEndPoint)listener.LocalEndpoint).Port,deadline.Token);
    using var clientTls=new SslStream(connection.GetStream(),false,(_,certificate,_,_)=>certificate?.GetCertHashString()==serverCertificate.Thumbprint);
    await clientTls.AuthenticateAsClientAsync(new SslClientAuthenticationOptions{TargetHost="localhost",ClientCertificates=new X509CertificateCollection{clientCertificate},EnabledSslProtocols=SslProtocols.Tls12,CertificateRevocationCheckMode=X509RevocationMode.NoCheck},deadline.Token);
    byte[] received=new byte[1];
    if(await clientTls.ReadAsync(received,deadline.Token)!=1 || received[0]!=42)throw new Exception("TLS application data failed");
    await server;
}
CryptographicOperations.ZeroMemory(pfx);
Console.WriteLine("PASS: local TLS 1.2 mutual authentication with synthetic A1");
Console.WriteLine("PASS: platform flags, private-key signature, password rejection, temporary key disposal and no certificate store installation");
