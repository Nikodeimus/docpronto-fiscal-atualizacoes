"""Installation-wide backup controls. Never accepts filesystem paths from requests."""
from pathlib import Path
from flask import g,jsonify,request
from werkzeug.security import check_password_hash
import local_backups
import local_runtime
from .local_maintenance import installed_here
from .db import log

ROOT=Path(__file__).resolve().parent.parent


def register_local_backups(app):
    @app.errorhandler(local_backups.BackupBusy)
    def busy_error(error):return jsonify(error=str(error)),409

    def context():
        state=local_runtime.state_root()
        supported=installed_here(state,ROOT)
        if g.user.role!='superadmin':return state,(jsonify(error='Somente o administrador da instalação gerencia backups completos.'),403)
        if not supported:return state,(jsonify(error='Backups completos estão disponíveis na instalação Windows local.'),409)
        return state,None

    @app.get('/api/backups')
    def backups_status():
        state=local_runtime.state_root();manageable=g.user.role=='superadmin'
        if not manageable:return jsonify(error='Somente o administrador da instalação consulta backups completos.'),403
        return jsonify(**local_backups.status(state),supported=installed_here(state,ROOT),manageable=manageable)

    @app.post('/api/backups/settings')
    def backups_settings():
        state,denied=context()
        if denied is not None:return denied
        data=request.get_json() or {}
        local_backups.save_settings(state,data.get('automatic'),data.get('interval_hours',24))
        return jsonify(ok=True)

    def busy(state):return local_backups.status(state)['state'] in ('creating','restoring')

    @app.post('/api/backups/verify')
    def backups_verify():
        state,denied=context()
        if denied is not None:return denied
        data=request.get_json() or {}
        result=local_backups.verify(state,data.get('backup_id'))
        log(g.s,None,g.user.id,'backup_verificado',
            'backup='+result['backup_id']+'; resultado='+('íntegro' if result['ok'] else 'falha'))
        g.s.commit()
        return jsonify(result)

    @app.post('/api/backups/create')
    def backups_create():
        state,denied=context()
        if denied is not None:return denied
        if busy(state):return jsonify(error='Um backup ou restauração já está em andamento.'),409
        local_runtime.launch_backup('create',root=ROOT,state=state)
        return jsonify(accepted=True,message='Backup iniciado. O fiscal reiniciará brevemente.'),202

    @app.post('/api/backups/restore')
    def backups_restore():
        state,denied=context()
        if denied is not None:return denied
        data=request.get_json() or {};ident=local_backups._id(data.get('backup_id'))
        if data.get('confirm')!=ident:raise ValueError('Confirme o backup selecionado antes de restaurar.')
        password=data.get('login_password')
        if not isinstance(password,str) or len(password)>1024 or not check_password_hash(g.user.password,password):
            return jsonify(error='Senha de acesso incorreta.'),403
        if busy(state):return jsonify(error='Um backup ou restauração já está em andamento.'),409
        # Lightweight existence check; full integrity validation runs in the child.
        if not (local_backups._folder(state)/ident/'manifest.json').is_file():raise ValueError('Backup não encontrado.')
        local_runtime.launch_backup('restore',backup_id=ident,root=ROOT,state=state)
        return jsonify(accepted=True,message='Restauração iniciada. Um backup preventivo será criado; entre novamente após reiniciar.'),202
