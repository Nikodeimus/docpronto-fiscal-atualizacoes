"""Serve a published fiscal update folder; never publishes or uploads packages.

Local test (not a hosted service):
  python scripts/update-server.py --channel C:/DocPronto-Atualizacoes
  GET http://127.0.0.1:8092/latest.json

Publish using scripts/publish-update.py first. For remote use, configure an HTTPS
reverse proxy to this loopback listener and pass --public-url https://host/prefix.
The proxy must forward /prefix/latest.json and /prefix/packages/... to this app.
No tunnel, DNS, certificate, public deployment or background service is created.
Set DOCPRONTO_UPDATE_TOKEN in the server environment to require Authorization:
Bearer on feed/package requests. Configure the consuming client separately; never
place a token in a URL. /health reveals only {"ok": true} and is public.
"""
import argparse
import hmac
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from flask import Flask, abort, jsonify, request, send_file


def create_update_server(channel, public_url=''):
    root = Path(channel).resolve()
    if public_url:
        parsed = urlsplit(public_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or '\\' in public_url or any(char.isspace() for char in public_url)):
            raise ValueError('A URL pública deve ser HTTPS, sem credenciais, consulta ou fragmento.')
        public_url = public_url.rstrip('/')
    token = os.environ.get('DOCPRONTO_UPDATE_TOKEN', '')
    app = Flask(__name__, static_folder=None)

    def authenticate():
        if token and not hmac.compare_digest(
                request.headers.get('Authorization', '').encode('utf-8'),
                ('Bearer ' + token).encode('utf-8')):
            abort(401)

    def current_feed():
        # Only publisher-style filenames are served. A malformed feed never
        # enables generic directory browsing or access to another local path.
        manifest = root / 'latest.json'
        try:
            if manifest.is_symlink() or manifest.stat().st_size > 64 * 1024:
                abort(503)
            feed = json.loads(manifest.read_text(encoding='utf-8'))
            filename = feed.get('package') if isinstance(feed, dict) else None
            if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.zip', filename):
                abort(503)
            path = root / filename
            if path.is_symlink() or path.resolve().parent != root or not path.is_file():
                abort(503)
            if not isinstance(feed.get('sha256'), str) or not re.fullmatch(r'[a-fA-F0-9]{64}', feed['sha256']):
                abort(503)
            return feed, filename, path
        except (OSError, ValueError, TypeError):
            abort(503)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    def health():
        return jsonify(ok=True)

    @app.get('/latest.json')
    def latest():
        authenticate()
        feed, filename, _ = current_feed()
        # Fixed host avoids constructing download links from attacker Host input.
        base = public_url or 'http://127.0.0.1:' + str(int(request.environ.get('SERVER_PORT', 8092)))
        return jsonify({**feed, 'package': base + '/packages/' + quote(filename, safe='')})

    @app.get('/packages/<filename>')
    def package(filename):
        authenticate()
        _, published, path = current_feed()
        if filename != published:
            abort(404)
        # Flask/Werkzeug streams through the WSGI file wrapper, not read_bytes.
        return send_file(path, mimetype='application/zip', as_attachment=True,
                         download_name=published, conditional=True)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8092)
    parser.add_argument('--public-url', default='')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Porta fora do intervalo 1–65535.')
    from waitress import serve
    serve(create_update_server(args.channel, args.public_url), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
