using System.Security.Cryptography.X509Certificates;

static class A1Certificate
{
    // Schannel needs a named user key for client TLS. Do not use PersistKeySet:
    // disposing the certificate removes its temporary key; no store import occurs.
    internal static X509KeyStorageFlags StorageFlags(bool windows) =>
        windows ? X509KeyStorageFlags.UserKeySet : X509KeyStorageFlags.EphemeralKeySet;

    internal static X509Certificate2 Load(byte[] pfx, string? password) =>
        X509CertificateLoader.LoadPkcs12(pfx, password, StorageFlags(OperatingSystem.IsWindows()));
}
