#!/usr/bin/env python3
"""Register mock mortgage skills in MLflow. Preview only unless --execute is supplied."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import tarfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

SKILLS = Path(__file__).resolve().parent / 'skills'
WORKSPACE = 'mlflow-demo'


def oc_output(*args):
    result = subprocess.run(['oc', *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('oc lookup failed; check your login and cluster context.')
    return result.stdout.strip()


def skill_payloads():
    """Only package the reviewed SKILL.md files; never upload a whole checkout."""
    payloads = []
    for manifest in sorted(SKILLS.glob('*/SKILL.md')):
        if manifest.is_symlink():
            raise RuntimeError('Skill manifests must not be symbolic links.')
        content = manifest.read_bytes()
        text = content.decode('utf-8')
        # These demo manifests deliberately use simple, single-line YAML strings.
        header = re.fullmatch(r'---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n.*',
                              text, flags=re.DOTALL)
        if not header or header[1] != manifest.parent.name:
            raise RuntimeError('Unexpected demo skill manifest: ' + manifest.parent.name)
        path = b'SKILL.md'
        digest = hashlib.sha256(struct.pack('>Q', len(path)) + path
                                + struct.pack('>Q', len(content)) + content).hexdigest()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as tar:
            info = tarfile.TarInfo('SKILL.md')
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
        payloads.append({'name': header[1], 'description': header[2],
                         'digest': digest, 'archive': archive.getvalue()})
    if not payloads:
        raise RuntimeError('No demo skills found next to the seeding helper.')
    return payloads


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Metadata/upload endpoints should not redirect; keep the token on this host.
        return None


class Registry:
    def __init__(self, uri, token, workspace=WORKSPACE, *, resource='skills'):
        if not uri.startswith('https://'):
            raise RuntimeError('Expected an HTTPS MLflow endpoint.')
        if resource not in {'skills', 'mcp-servers'}:
            raise RuntimeError('Unsupported registry resource.')
        self.resource = resource
        self.base = uri.rstrip('/') + '/api/3.0/mlflow/' + resource
        self.headers = {'Authorization': 'Bearer ' + token, 'X-MLFLOW-WORKSPACE': workspace}
        self.opener = build_opener(NoRedirect())

    def request(self, method, path='', *, data=None, content_type=None, missing_ok=False):
        headers = dict(self.headers)
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            content_type = 'application/json'
        if content_type:
            headers['Content-Type'] = content_type
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise RuntimeError(f'{self.resource} registry {method} failed (HTTP {exc.code}); '
                               'check API support, RBAC, and artifact storage.') from None
        except URLError:
            raise RuntimeError('Cannot reach MLflow securely; check connectivity and CA trust.') from None


def multipart(skill):
    boundary = 'mlflow-demo-' + uuid4().hex
    metadata = json.dumps({'digest': skill['digest'], 'status': 'active'}).encode()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
            'Content-Type: application/json\r\n\r\n').encode() + metadata
    body += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="content"; '
             'filename="content.tar.gz"\r\nContent-Type: application/gzip\r\n\r\n').encode()
    body += skill['archive'] + f'\r\n--{boundary}--\r\n'.encode()
    return body, 'multipart/form-data; boundary=' + boundary


def register_skills(registry, payloads):
    results = []
    # Fail before writing anything if the Skills API is unavailable.
    registry.request('GET')
    for skill in payloads:
        path = '/' + quote(skill['name'], safe='')
        parent = registry.request('GET', path, missing_ok=True)
        if parent is None:
            parent = registry.request('POST', data={'name': skill['name'],
                                                    'description': skill['description']})
        elif parent.get('description') != skill['description']:
            registry.request('PATCH', path, data={'description': skill['description']})
        latest = parent.get('latest_version')
        version = registry.request('GET', path + f'/versions/{latest}') if latest else None
        if version is None or version.get('digest') != skill['digest']:
            body, content_type = multipart(skill)
            version = registry.request('POST', path + '/versions', data=body,
                                       content_type=content_type)
            action = 'Registered'
        else:
            action = 'Reused'
        print(f"{action} {skill['name']} v{version['version']}", flush=True)
        results.append(version)
    return results


def seed_skills():
    payloads = skill_payloads()
    uri = oc_output('get', 'mlflows.mlflow.opendatahub.io', 'mlflow',
                    '-o', 'jsonpath={.status.url}')
    token = oc_output('whoami', '-t')
    if not uri or not token:
        raise RuntimeError('MLflow status.url and an active oc login are required.')
    return register_skills(Registry(uri, token), payloads)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Register skills using your oc identity.')
    args = parser.parse_args()
    try:
        if args.execute:
            seed_skills()
        else:
            for skill in skill_payloads():
                print(skill['name'] + ': ' + skill['description'])
            print('Preview only. Use --execute to register in workspace ' + WORKSPACE + '.')
    except Exception as exc:
        # Never include HTTP bodies, headers, or subprocess output in error messages.
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        parser.exit(1, 'STOPPED: ' + detail + '\n')


if __name__ == '__main__':
    main()
