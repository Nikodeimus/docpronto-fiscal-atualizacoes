"""Optional private-LAN listener configuration; disabled unless explicitly enabled."""
import ipaddress
import os
from pathlib import Path
import re
from flask import jsonify,request,g
import local_runtime
from local_updates import _json,_atomic
from .local_maintenance import installed_here

ROOT=Path(__file__).resolve().parent.parent
PRIVATE_NETWORKS=tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def private_ipv4(value):
    try:address=ipaddress.ip_address(value)
    except ValueError:return False
    return address.version==4 and any(address in network for network in PRIVATE_NETWORKS)


def validate_hosts(values):
    if not isinstance(values,list) or len(values)>20:raise ValueError('Informe uma lista de até 20 nomes ou endereços privados.')
    result=[]
    for value in values:
        if not isinstance(value,str):raise ValueError('Nome de computador inválido.')
        value=value.strip().lower()
        if not value or len(value)>253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',value):raise ValueError('Use somente nome do computador ou IPv4 privado, sem http://, porta ou caminho.')
        if value=='localhost' or value=='127.0.0.1':continue
        if re.fullmatch(r'[0-9.]+',value):
            if not private_ipv4(value):raise ValueError('Use IPv4 privado da rede da empresa (10.x, 172.16–31.x ou 192.168.x).')
        else:
            labels=value.split('.')
            if any(len(label)>63 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?',label) for label in labels):raise ValueError('Nome de computador inválido.')
            if len(labels)>1 and labels[-1] not in ('local','lan','internal'):raise ValueError('Use nome curto do computador ou domínio interno .local, .lan ou .internal.')
        if value not in result:result.append(value)
    return result


def settings(state):
    saved=_json(Path(state)/'local.json')
    enabled=saved.get('lan_enabled',False)
    hosts=validate_hosts(saved.get('lan_hosts',[]))
    if type(enabled) is not bool:raise ValueError('Configuração LAN inválida.')
    if enabled and not hosts:raise ValueError('Informe o nome ou endereço privado do computador central.')
    return {'enabled':enabled,'hosts':hosts}


def save_settings(state,enabled,hosts):
    if type(enabled) is not bool:raise ValueError('A opção de rede local deve ser verdadeira ou falsa.')
    hosts=validate_hosts(hosts)
    if enabled and not hosts:raise ValueError('Informe o nome ou endereço privado do computador central.')
    state=Path(state)
    lock=local_runtime.InstanceLock(state/'network.lock')
    try:
        config=_json(state/'local.json')
        if not config.get('setup_token'):raise ValueError('Configuração da instalação local não encontrada.')
        config.update(lan_enabled=enabled,lan_hosts=hosts)
        _atomic(state/'local.json',config)
    finally:lock.close()
    return {'enabled':enabled,'hosts':hosts}


def trusted_peer(value):
    try:address=ipaddress.ip_address(value or '')
    except ValueError:return False
    if address.is_loopback:return True
    if address.version==6 and address.ipv4_mapped:return private_ipv4(str(address.ipv4_mapped)) or address.ipv4_mapped.is_loopback
    return private_ipv4(value)


def register_local_network(app):
    @app.before_request
    def private_network_only():
        if os.getenv('DOCPRONTO_LAN_ENABLED')=='1' and not trusted_peer(request.remote_addr):
            return jsonify(error='Acesso restrito à rede privada da empresa.'),403

    def context():
        state=local_runtime.state_root();supported=installed_here(state,ROOT)
        if g.user.role!='superadmin':return state,supported,(jsonify(error='Somente o administrador da instalação configura a rede local.'),403)
        return state,supported,None

    @app.get('/api/network')
    def network_status():
        state,supported,denied=context()
        if denied is not None:return denied
        configured=settings(state)
        active=os.getenv('DOCPRONTO_LAN_ENABLED')=='1'
        active_hosts=validate_hosts([host for host in os.getenv('ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if host not in ('localhost','127.0.0.1')]) if active else []
        return jsonify(**configured,supported=supported,manageable=True,active_enabled=active,
            restart_required=configured['enabled']!=active or (configured['enabled'] and set(configured['hosts'])!=set(active_hosts)),port=8080,
            urls=['http://'+host+':8080' for host in configured['hosts']] if configured['enabled'] else [])

    @app.post('/api/network/settings')
    def network_settings():
        state,supported,denied=context()
        if denied is not None:return denied
        if not supported:return jsonify(error='Disponível somente na instalação Windows local.'),409
        data=request.get_json() or {}
        save_settings(state,data.get('enabled'),data.get('hosts',[]))
        return jsonify(ok=True,restart_required=True,message='Configuração salva. Reinicie o DocPronto Local no computador central quando terminar o trabalho em andamento.')
