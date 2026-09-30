"""Private appearance files; immutable names keep concurrent reads safe."""
import hashlib,os,re,tempfile

def save_media(storage,raw):
    directory=storage.root/'appearance-media'
    directory.mkdir(parents=True,exist_ok=True)
    name=hashlib.sha256(raw).hexdigest()+'.bin'
    target=directory/name
    if not target.exists():
        fd,temp=tempfile.mkstemp(dir=directory,prefix='upload-')
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(temp,target)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    return name

def media_path(storage,name):
    if not isinstance(name,str) or not re.fullmatch(r'[0-9a-f]{64}\.bin',name):
        raise ValueError('Referência de fundo inválida.')
    return storage.resolve('appearance-media/'+name)
