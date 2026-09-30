import os
from pathlib import Path

def load_env():
    path=Path(__file__).resolve().parents[1]/'.env'
    if path.is_file():
        for line in path.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            key,value=line.split('=',1)
            if key.replace('_','').isalnum():os.environ.setdefault(key,value)

def root_data():return Path(os.getenv('DOCPRONTO_HOME',str(Path.home()/'.local/share/docpronto'))).resolve()
def default_database():return 'sqlite:///'+str(root_data()/'app.db')
def default_files():return str(root_data()/'files')
