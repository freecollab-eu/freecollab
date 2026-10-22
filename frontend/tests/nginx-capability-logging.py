#!/usr/bin/env python3
"""Exercise raw capability URLs against the actual app Nginx configurations.

Uses an installed Nginx, or a copied Alpine binary with --loader/--library-path.
All listeners, upstream data and capabilities belong to this disposable fixture.
No Docker service, customer credentials or external network is required.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def port():
    with socket.socket() as binding:
        binding.bind(('127.0.0.1', 0))
        return binding.getsockname()[1]


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/calendar')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def raw_request(listen_port, target):
    with socket.create_connection(('127.0.0.1', listen_port), timeout=5) as connection:
        connection.sendall(('GET ' + target + ' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode('ascii'))
        data = bytearray()
        while chunk := connection.recv(65536):
            data.extend(chunk)
        return int(data.split(b' ', 2)[1])


def probe(command, config_source, directory, upstream_port, protected):
    directory.mkdir()
    listen_port = port()
    root = directory / 'html'
    root.mkdir()
    (root / 'index.html').write_text('<!doctype html><title>Owned fixture</title>')
    (root / 'proof.css').write_text('body{color:black}')
    config = config_source.replace('listen 80;', f'listen 127.0.0.1:{listen_port};')
    config = config.replace('/usr/share/nginx/html', str(root))
    config = config.replace('http://backend:8000', f'http://127.0.0.1:{upstream_port}')
    assert 'listen 80;' not in config and 'http://backend:' not in config
    # Positive control removes only the server-level safeguards. The original
    # API location directives remain, reproducing the previous boundary gap.
    if not protected:
        config, count = re.subn(r'(?m)^    access_log off;\n    error_log /dev/null emerg;\n', '', config, count=1)
        assert count == 1, 'Expected one server-level logging boundary'
    (directory / 'server.conf').write_text(config)
    access, error = directory / 'access.log', directory / 'error.log'
    main = directory / 'nginx.conf'
    main.write_text(f'daemon off; master_process off; pid {directory}/nginx.pid;\n'
        f'error_log {error} info; events {{ worker_connections 32; }}\n'
        'http { '
        + ''.join(f'{kind}_temp_path {directory}/{kind}; ' for kind in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'))
        + f'access_log {access} combined; include {directory}/server.conf; }}\n')
    output = (directory / 'process.log').open('w')
    process = subprocess.Popen([*command, '-p', str(directory) + '/', '-c', str(main)], stdout=output, stderr=output)
    statuses = {}
    marker = 'owned-calendar-capability-' + secrets.token_hex(12)
    base = '/api/calendar/subscriptions/fixture/' + marker + '.ics'
    cases = {
        'canonical': base,
        'encoded_calendar_prefix': '/api/%63alendar/subscriptions/fixture/' + marker + '.ics',
        'encoded_api_and_slash': '/%61%70%69/calendar%2fsubscriptions/fixture/' + marker + '.ics',
        'static_css_suffix': base + '.css',
        'static_mjs_suffix': base + '.mjs',
        'encoded_static_suffix': base + '%2ecss',
        'dot_segments': base + '/../../../../../proof.css',
        'encoded_dot_segments': base + '/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/proof.css',
        'malformed_escape': base + '%GG',
        'truncated_escape': base + '%',
    }
    try:
        deadline = time.monotonic() + 10
        while True:
            assert process.poll() is None, 'Nginx startup failed: ' + (directory / 'process.log').read_text()
            try:
                with socket.create_connection(('127.0.0.1', listen_port), timeout=.2):
                    break
            except OSError:
                assert time.monotonic() < deadline, 'Nginx fixture did not start'
                time.sleep(.05)
        for name, target in cases.items():
            statuses[name] = raw_request(listen_port, target)
        assert statuses['malformed_escape'] == statuses['truncated_escape'] == 400
        if 'proxy_pass' in config:
            assert statuses['canonical'] == statuses['encoded_calendar_prefix'] == statuses['encoded_api_and_slash'] == 200
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        output.close()
    logs = '\n'.join(path.read_text() for path in (access, error, directory / 'process.log') if path.exists())
    leaked = marker in logs
    assert leaked != protected, 'Logging boundary failed its actual Nginx positive/negative control'
    return {'http_statuses': statuses, 'raw_capability_logged': leaked,
            'server_scope_logging_suppressed': protected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx', default=shutil.which('nginx'))
    parser.add_argument('--loader', type=Path)
    parser.add_argument('--library-path', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not args.nginx:
        parser.error('Install Nginx or pass --nginx with an actual copied binary.')
    command = [str(args.nginx)]
    if args.loader:
        if not args.library_path:
            parser.error('--loader requires --library-path')
        command = [str(args.loader), '--library-path', str(args.library_path), *command]
    version = subprocess.run([*command, '-v'], check=True, capture_output=True, text=True).stderr.strip()
    result = {'scope': 'actual local Nginx; synthetic capabilities and owned loopback upstream only',
              'nginx_version': version, 'nginx_binary_sha256': sha256(args.nginx), 'configurations': {}}
    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='fc-nginx-capability-013-') as temporary:
            for name in ('nginx.conf', 'nginx.static.conf'):
                source = ROOT / 'frontend' / name
                config = source.read_text()
                result['configurations'][name] = {
                    'source_sha256': sha256(source),
                    'previous_scope_control': probe(command, config, Path(temporary)/(name+'-control'), server.server_port, False),
                    'protected': probe(command, config, Path(temporary)/(name+'-protected'), server.server_port, True),
                }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result['test_source_sha256'] = sha256(__file__)
    result['cleanup'] = 'Owned Nginx processes, loopback upstream and temporary fixture files removed; no Docker service created.'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
