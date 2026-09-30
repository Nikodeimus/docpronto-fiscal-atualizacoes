"""Authenticated controls for the installed Windows fiscal updater."""
import os
import re
from pathlib import Path

from flask import g, jsonify, request
import local_runtime
import local_updates


ROOT = Path(__file__).resolve().parent.parent


def installed_here(state, root):
    if os.name != 'nt' or not os.getenv('DOCPRONTO_LOCAL_INSTANCE'):
        return False
    try:
        name = (state / 'active.txt').read_text(encoding='utf-8-sig').strip()
        return bool(re.fullmatch(r'release-[a-f0-9]{32}', name)) and (state / 'releases' / name).resolve() == root.resolve()
    except OSError:
        return False


def register_local_maintenance(app):
    def context():
        state = local_runtime.state_root()
        return state, installed_here(state, ROOT)

    def require_admin(supported):
        if g.user.role != 'superadmin':
            return jsonify(error='Somente o administrador da instalação pode atualizar o fiscal.'), 403
        if not supported:
            return jsonify(error='Disponível na instalação Windows local do fiscal. Instale esta versão uma vez para habilitar as próximas atualizações.'), 409

    @app.get('/api/updates')
    def updates_status():
        state, supported = context()
        manageable = g.user.role == 'superadmin'
        result = local_updates.status(state, ROOT)
        # Non-administrators can see availability but not local source locations.
        if not manageable:
            result['source'] = ''
        return jsonify(**result, supported=supported, manageable=manageable)

    @app.post('/api/updates/settings')
    def updates_settings():
        state, supported = context()
        denied = require_admin(supported)
        if denied is not None:
            return denied
        data = request.get_json(silent=True) or {}
        if not isinstance(data.get('source'), str) or type(data.get('automatic')) is not bool:
            raise ValueError('Informe a fonte e a preferência de atualização automática.')
        local_updates.save_settings(state, data['source'], data['automatic'])
        return jsonify(ok=True)

    @app.post('/api/updates/check')
    def updates_check():
        state, supported = context()
        denied = require_admin(supported)
        if denied is not None:
            return denied
        if not local_updates.settings(state).get('source'):
            raise ValueError('Configure primeiro a fonte das atualizações.')
        local_runtime.launch_update('check', root=ROOT, state=state)
        return jsonify(accepted=True), 202

    @app.post('/api/updates/apply')
    def updates_apply():
        state, supported = context()
        denied = require_admin(supported)
        if denied is not None:
            return denied
        if local_updates.status(state, ROOT).get('state') != 'ready':
            return jsonify(error='Nenhuma atualização está pronta. Verifique e aguarde o download.'), 409
        local_runtime.launch_update('apply', root=ROOT, state=state)
        return jsonify(accepted=True, message='Atualização iniciada. O fiscal reiniciará ao concluir.'), 202
