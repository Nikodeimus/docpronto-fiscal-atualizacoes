from pathlib import Path
import os,uuid,hashlib
class LocalStorageProvider:
    def __init__(self,root):self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
    def save(self,company,doc,raw,ext):
        if ext not in ('pdf','xml','json'):raise ValueError('Formato inválido')
        name=f'{company}/{doc}/{hashlib.sha256(raw).hexdigest()}.{ext}'
        p=self.resolve(name);p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        with open(tmp,'xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600);os.replace(tmp,p)
        return name
    def resolve(self,name):
        p=(self.root/name).resolve()
        if not p.is_relative_to(self.root):raise ValueError('Caminho inválido')
        return p
