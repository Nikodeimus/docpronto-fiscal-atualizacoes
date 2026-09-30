"""Protect the local Fernet key with current-user Windows DPAPI.

DPAPI backups require the original Windows profile. Import the A1 again after
moving to another account/computer; never silently replace an unreadable key.
"""
import ctypes
import os
import tempfile
from ctypes import wintypes

from cryptography.fernet import Fernet

PREFIX = b'DOCPRONTO-DPAPI-USER-V1\n'


def _dpapi(raw, decrypt=False):
    if os.name != 'nt':
        raise ValueError('O cofre Windows exige a conta Windows original.')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN; deliberately omit LOCAL_MACHINE, which broadens access.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Não foi possível abrir/proteger o cofre de certificados nesta conta Windows.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def _protected(key):
    encrypted = _dpapi(key)
    if _dpapi(encrypted, decrypt=True) != key:
        raise ValueError('Falha na verificação da proteção do cofre.')
    return PREFIX + encrypted


def vault_cipher(root):
    path = root / 'certificate-vault.key'
    if not path.exists():
        if os.name != 'nt':
            external = os.environ.get('DOCPRONTO_CERTIFICATE_VAULT_KEY')
            if not external:
                raise ValueError('Configure DOCPRONTO_CERTIFICATE_VAULT_KEY para armazenar A1 fora do Windows.')
            return Fernet(external.encode('ascii'))
        contents = _protected(Fernet.generate_key())
        _write(path, contents, replace=False)
    contents = path.read_bytes()
    if contents.startswith(PREFIX):
        return Fernet(_dpapi(contents[len(PREFIX):], decrypt=True))
    # Validate the legacy key before migration; corrupt keys remain untouched.
    result = Fernet(contents)
    if os.name == 'nt':
        protected = _protected(contents)
        _write(path, protected, replace=True)
    return result


def _write(path, contents, replace):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='vault-key-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
