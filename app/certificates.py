from datetime import datetime,timezone
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from cryptography.hazmat.primitives import hashes
import secrets

def inspect_a1(raw,password):
    """Ephemeral possession test. Does not store PFX, password or private key."""
    if len(raw)>256000:raise ValueError('PFX muito grande.')
    try:key,cert,chain=pkcs12.load_key_and_certificates(raw,password.encode() if password else None)
    except (ValueError,TypeError):raise ValueError('Não foi possível abrir o A1. Confira arquivo e senha.')
    if not key or not cert:raise ValueError('A1 sem certificado ou chave privada.')
    now=datetime.now(timezone.utc)
    start=cert.not_valid_before_utc
    end=cert.not_valid_after_utc
    if not start<=now<=end:raise ValueError('Certificado fora do período de validade.')
    if not isinstance(key,rsa.RSAPrivateKey):raise ValueError('Este teste suporta certificados RSA.')
    challenge=secrets.token_bytes(32)
    signature=key.sign(challenge,padding.PKCS1v15(),hashes.SHA256())
    cert.public_key().verify(signature,challenge,padding.PKCS1v15(),hashes.SHA256())
    return {'subject':cert.subject.rfc4514_string(),'issuer':cert.issuer.rfc4514_string(),'valid_until':end.isoformat(),'fingerprint':cert.fingerprint(hashes.SHA256()).hex(),'possession_test':'OK','chain_validation':'NAO_VERIFICADA','message':'Chave privada testada. Arquivo e senha não foram armazenados. Use o agente Windows para consultas autenticadas A1/A3.'}
