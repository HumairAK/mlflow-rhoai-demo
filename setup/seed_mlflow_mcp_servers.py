#!/usr/bin/env python3
"""Restore the demo's MCP catalog registrations. Preview only unless --execute is supplied."""

import argparse
import json
from pathlib import Path
from urllib.parse import quote, urlencode

from seed_mlflow_skills import Registry, WORKSPACE, oc_output

CONFIG = Path(__file__).resolve().with_name('mcp-servers.json')


def load_servers():
    config = json.loads(CONFIG.read_text())
    if config.get('workspace') != WORKSPACE or not config.get('servers'):
        raise RuntimeError('Expected MCP definitions for workspace ' + WORKSPACE + '.')
    for server in config['servers']:
        if not server.get('versions'):
            raise RuntimeError('MCP definitions must include their saved versions.')
        for version in server['versions']:
            if version['server_json']['name'] != server['name']:
                raise RuntimeError('MCP version identity must match its parent.')
    return config['servers']


def paginated(registry, path, key):
    results = []
    while True:
        page = registry.request('GET', path)
        results.extend(page[key])
        token = page.get('next_page_token')
        if not token:
            return results
        path = path.split('?', 1)[0] + '?' + urlencode({'page_token': token})


def restore_tags(registry, path, current, desired):
    for key, value in desired.items():
        if current.get(key) != value:
            registry.request('POST', path + '/tags', data={'key': key, 'value': value})


def register_servers(registry, servers):
    # Check API availability before creating any records. No MCP connections or discovery.
    registry.request('GET')
    restored = []
    for server in servers:
        path = '/' + quote(server['name'], safe='/')
        parent = registry.request('GET', path, missing_ok=True)
        if parent is None:
            parent = registry.request('POST', data={
                k: server[k] for k in ('name', 'description', 'icons') if k in server
            })
        changes = {k: server[k] for k in ('display_name', 'description', 'icons')
                   if k in server and normalize(parent.get(k)) != normalize(server[k])}
        if changes:
            registry.request('PATCH', path, data=changes)
        restore_tags(registry, path, parent.get('tags', {}), server.get('tags', {}))

        for version in server['versions']:
            number = version['server_json']['version']
            version_path = path + '/versions/' + quote(number, safe='')
            existing = registry.request('GET', version_path, missing_ok=True)
            payload = {k: version[k] for k in
                       ('server_json', 'status', 'source', 'tools', 'connect_options') if k in version}
            if existing is None:
                existing = registry.request('POST', path + '/versions', data=payload)
                action = 'Registered'
            else:
                # Semver identifies saved content; never replace a different definition silently.
                if any(normalize(existing.get(k)) != normalize(version.get(k))
                       for k in ('server_json', 'source')):
                    raise RuntimeError('Existing MCP version differs from the saved definition: '
                                       + server['name'] + ' ' + number)
                mutable = {k: version[k] for k in ('status', 'tools', 'connect_options')
                           if k in version and normalize(existing.get(k)) != normalize(version[k])}
                if mutable:
                    registry.request('PATCH', version_path, data=mutable)
                action = 'Reused'
            restore_tags(registry, version_path, existing.get('tags', {}), version.get('tags', {}))
            print(f"{action} {server['name']} {number} ({len(version.get('tools', []))} tools)",
                  flush=True)

        aliases = {a['alias']: a['version'] for a in parent.get('aliases', [])}
        for alias in server.get('aliases', []):
            if aliases.get(alias['alias']) != alias['version']:
                registry.request('POST', path + '/aliases', data=alias)
        if server.get('access_endpoints'):
            endpoints = paginated(registry, path + '/endpoints', 'mcp_access_endpoints')
            for endpoint in server['access_endpoints']:
                if not any(all(e.get(k) == v for k, v in endpoint.items()) for e in endpoints):
                    registry.request('POST', path + '/endpoints', data=endpoint)
                    endpoints.append(endpoint)
        restored.append(server['name'])
    return restored


def normalize(value):
    """Ignore response-only icon provenance and omitted optional null fields."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()
                if v is not None and not (k == 'source' and 'src' in value)}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def seed_mcp_servers():
    servers = load_servers()
    uri = oc_output('get', 'mlflows.mlflow.opendatahub.io', 'mlflow',
                    '-o', 'jsonpath={.status.url}')
    token = oc_output('whoami', '-t')
    if not uri or not token:
        raise RuntimeError('MLflow status.url and an active oc login are required.')
    return register_servers(Registry(uri, token, resource='mcp-servers'), servers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Restore registrations using your oc identity.')
    args = parser.parse_args()
    try:
        if args.execute:
            seed_mcp_servers()
        else:
            for server in load_servers():
                versions = ', '.join(v['server_json']['version'] for v in server['versions'])
                print(server['name'] + ': ' + versions)
            print('Preview only. Use --execute to restore in workspace ' + WORKSPACE + '.')
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        parser.exit(1, 'STOPPED: ' + detail + '\n')


if __name__ == '__main__':
    main()
