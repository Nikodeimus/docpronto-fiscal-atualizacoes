from datetime import datetime,timedelta,timezone
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from app.certificates import inspect_a1

def make_pfx(expired=False):
    k=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'TEST ONLY')]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=3)).not_valid_after(now+timedelta(days=-1 if expired else 1)).sign(k,hashes.SHA256())
    return pkcs12.serialize_key_and_certificates(b'test',k,cert,None,serialization.BestAvailableEncryption(b'test-only-password'))
def test_a1_possession():
    r=inspect_a1(make_pfx(),'test-only-password')
    assert r['possession_test']=='OK' and r['chain_validation']=='NAO_VERIFICADA'
def test_a1_bad_password():
    with pytest.raises(ValueError,match='senha'):inspect_a1(make_pfx(),'wrong')
def test_a1_expired():
    with pytest.raises(ValueError,match='validade'):inspect_a1(make_pfx(True),'test-only-password')
