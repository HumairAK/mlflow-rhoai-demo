#!/usr/bin/env python3
"""Reset this demo's PostgreSQL metadata. Read-only unless --execute or --resume."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

APPS = 'redhat-ods-applications'
DB_NS = 'mlflow-demo-db'
DB_DEPLOYMENT = 'postgresql'
MLFLOW = 'mlflow'
OPERATOR = 'mlflow-operator-controller-manager'
FORCE = 'mlflow.opendatahub.io/force-migrate'
COMMAND_ERROR = 'Command failed; credentials and command output are suppressed.'


def oc(*args, payload=None, binary=False):
    result = subprocess.run(
        ['oc', *args], input=payload, capture_output=True, text=not binary,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(COMMAND_ERROR)
    return result.stdout


def get(kind, name=None, namespace=None, selector=None):
    args = ['get', kind]
    if name:
        args.append(name)
    if namespace:
        args += ['-n', namespace]
    if selector:
        args += ['-l', selector]
    return json.loads(oc(*args, '-o', 'json'))


def patch(kind, name, value):
    oc('patch', kind, name, '-n', APPS, '--type=merge',
       '--patch-file=/dev/stdin', payload=json.dumps(value))


def wait_for(description, check, timeout):
    print(description, flush=True)
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out: ' + description)
        time.sleep(3)


def selector(deployment):
    spec = deployment['spec']['selector']
    if spec.get('matchExpressions'):
        raise RuntimeError('Unsupported deployment selector; review script for this installation.')
    return ','.join(k + '=' + v for k, v in spec['matchLabels'].items())


def owned(obj, uid):
    return any(o['uid'] == uid for o in obj['metadata'].get('ownerReferences', []))


def db_command(program, *args, stdin=None, binary=False):
    # Password is expanded only inside the existing PostgreSQL pod, never locally.
    shell = ('export PGPASSWORD="${POSTGRESQL_PASSWORD:?}" PGSSLMODE=require; '
             'exec "$@" -h 127.0.0.1 -U "${POSTGRESQL_USER:?}" '
             '-d "${POSTGRESQL_DATABASE:?}"')
    command = ['exec']
    if stdin is not None:
        command.append('-i')
    command += ['-n', DB_NS, 'deployment/' + DB_DEPLOYMENT, '-c', 'postgresql',
                '--', '/bin/bash', '-c', shell, 'reset-mlflow-db', program, *args]
    return oc(*command, payload=stdin, binary=binary)


def sql(statement):
    return db_command('psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', stdin=statement)


def database_info():
    return json.loads(sql("""
SELECT json_build_object(
 'database', current_database(), 'user', current_user,
 'schema_owner', (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'),
 'extra_schemas', (SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('information_schema','public')),
 'public_extensions', (SELECT count(*) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE n.nspname='public'),
 'tables', (SELECT count(*) FROM pg_tables WHERE schemaname='public'),
 'other_connections', (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid())
);
"""))


def validate_database(info):
    if info['database'] != 'mlflow' or info['user'] != 'mlflow':
        raise RuntimeError('Refusing to reset a database/user other than mlflow.')
    if info['schema_owner'] != 'pg_database_owner':
        raise RuntimeError('Unexpected public schema owner; review privileges before resetting.')
    if info['extra_schemas'] or info['public_extensions']:
        raise RuntimeError('Extra schemas/extensions found; this script is only for the dedicated demo DB.')


def save(state, path):
    # Small atomic state updates make recovery possible after an interruption.
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def migration_jobs():
    return get('jobs', namespace=APPS,
               selector='mlflow.opendatahub.io/migration-instance=' + MLFLOW)['items']


def snapshot():
    cr = get('mlflows.mlflow.opendatahub.io', MLFLOW)
    spec = cr['spec']
    if FORCE in cr['metadata'].get('annotations', {}):
        raise RuntimeError('A forced migration is already requested; finish it first.')
    ref = spec.get('backendStoreUriFrom', {})
    if not ref or spec.get('registryStoreUriFrom') or spec.get('registryStoreUri'):
        raise RuntimeError('Expected a Secret-backed backend with registry using the same DB.')
    if spec.get('readReplicaBackendStoreUriFrom') or spec.get('readReplicaBackendStoreUri'):
        raise RuntimeError('Read replica configured; this script does not reset replicas.')
    if any(e.get('name') in {'MLFLOW_BACKEND_STORE_URI', 'MLFLOW_REGISTRY_STORE_URI'}
           for e in spec.get('env', [])):
        raise RuntimeError('Metadata URI override found; inspect configuration before resetting.')
    secret = get('secret', ref['name'], APPS)
    uri = base64.b64decode(secret['data'][ref['key']]).decode()
    target = urlsplit(uri)
    if (target.scheme != 'postgresql' or
            target.hostname != 'postgresql.mlflow-demo-db.svc.cluster.local' or
            unquote(target.path.strip('/')) != 'mlflow'):
        raise RuntimeError('MLflow backend does not point at the expected dedicated demo database.')
    operator = get('deployment', OPERATOR, APPS)
    deployments = [p for p in get('deployments', namespace=APPS)['items']
                   if owned(p, cr['metadata']['uid'])]
    if not any(p['metadata']['name'] == 'mlflow' for p in deployments):
        raise RuntimeError('Expected MLflow-owned tracking Deployment was not found.')
    cronjobs = [p for p in get('cronjobs', namespace=APPS)['items']
                if owned(p, cr['metadata']['uid'])]
    info = database_info()
    validate_database(info)
    return {
        'context': oc('config', 'current-context').strip(),
        'server': oc('config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}').strip(),
        'mlflow_uid': cr['metadata']['uid'],
        'database': info,
        'operator': {'name': OPERATOR, 'replicas': operator['spec'].get('replicas', 1),
                     'selector': selector(operator)},
        'deployments': [{'name': p['metadata']['name'], 'replicas': p['spec'].get('replicas', 1),
                         'selector': selector(p)} for p in deployments],
        'cronjobs': [{'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                      'suspend': p['spec'].get('suspend', False)} for p in cronjobs],
        'phase': 'prepared',
    }


def assert_operator_paused(state):
    d = get('deployment', OPERATOR, APPS)
    if d['spec'].get('replicas', 1) != 0:
        raise RuntimeError('Operator was scaled back up externally; refusing the reset.')
    if get('pods', namespace=APPS, selector=state['operator']['selector'])['items']:
        raise RuntimeError('Operator pods still exist; refusing the reset.')


def quiesce(state, timeout):
    patch('deployment', OPERATOR, {'spec': {'replicas': 0}})
    wait_for('Waiting for MLflow operator pods to stop...',
             lambda: not get('pods', namespace=APPS, selector=state['operator']['selector'])['items'], timeout)
    for cj in state['cronjobs']:
        patch('cronjob', cj['name'], {'spec': {'suspend': True}})
    cron_uids = {c['uid'] for c in state['cronjobs']}

    def no_active_jobs():
        for job in get('jobs', namespace=APPS)['items']:
            relevant = (any(o['uid'] in cron_uids for o in job['metadata'].get('ownerReferences', []))
                        or job['metadata'].get('labels', {}).get('mlflow.opendatahub.io/migration-instance') == MLFLOW)
            finished = any(c['type'] in {'Complete', 'Failed'} and c['status'] == 'True'
                           for c in job.get('status', {}).get('conditions', []))
            if relevant and not finished:
                return False
        return True

    wait_for('Waiting for existing MLflow maintenance/migration jobs to finish...', no_active_jobs, timeout)
    for d in state['deployments']:
        patch('deployment', d['name'], {'spec': {'replicas': 0}})
    for d in state['deployments']:
        wait_for('Waiting for ' + d['name'] + ' pods to stop...',
                 lambda d=d: not get('pods', namespace=APPS, selector=d['selector'])['items'], timeout)
    assert_operator_paused(state)
    wait_for('Waiting for remaining database sessions to close (no sessions are killed)...',
             lambda: database_info()['other_connections'] == 0, timeout)


def recover(state, path, timeout):
    cr = get('mlflows.mlflow.opendatahub.io', MLFLOW)
    if cr['metadata']['uid'] != state['mlflow_uid']:
        raise RuntimeError('MLflow resource has been replaced; do not resume this state file.')
    if state['operator']['replicas'] < 1:
        raise RuntimeError('Original operator replicas were zero; resume requires a running operator.')
    if state.get('phase') != 'migrating':
        state['old_migration_uids'] = [p['metadata']['uid'] for p in migration_jobs()]
        state['phase'] = 'requesting-migration'
        save(state, path)
        oc('annotate', 'mlflows.mlflow.opendatahub.io', MLFLOW,
           FORCE + '=' + datetime.now(timezone.utc).isoformat(), '--overwrite')
        state['phase'] = 'migrating'
        save(state, path)
    patch('deployment', OPERATOR, {'spec': {'replicas': state['operator']['replicas']}})

    def migrated():
        cr = get('mlflows.mlflow.opendatahub.io', MLFLOW)
        conditions = {c['type']: c['status'] for c in cr.get('status', {}).get('conditions', [])}
        new_success = any(
            j['metadata']['uid'] not in state.get('old_migration_uids', []) and
            any(c['type'] == 'Complete' and c['status'] == 'True'
                for c in j.get('status', {}).get('conditions', []))
            for j in migration_jobs())
        return (new_success and FORCE not in cr['metadata'].get('annotations', {})
                and conditions.get('Migration') == 'True' and conditions.get('Available') == 'True')

    wait_for('Waiting for a NEW successful migration and MLflow availability...', migrated, timeout)
    for d in state['deployments']:
        patch('deployment', d['name'], {'spec': {'replicas': d['replicas']}})
    for cj in state['cronjobs']:
        patch('cronjob', cj['name'], {'spec': {'suspend': cj['suspend']}})
    state['phase'] = 'complete'
    save(state, path)
    print('Reset complete. MinIO and agent/chat state were not cleared. Seed the baseline prompt once.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Actually erase all MLflow public-schema data.')
    parser.add_argument('--confirm-database', help='Must be mlflow when executing; all workspaces are affected.')
    parser.add_argument('--resume', type=Path, help='Resume migration using an existing state.json; NEVER resets schema again.')
    parser.add_argument('--backup-root', type=Path, default=Path.home() / '.local/state/mlflow-demo/database-resets')
    parser.add_argument('--timeout', type=int, default=900, help='Seconds per wait (default: 900).')
    args = parser.parse_args()
    if args.resume and args.execute:
        parser.error('Use either --execute or --resume, not both.')
    if args.timeout < 1:
        parser.error('--timeout must be positive.')
    os.umask(0o077)
    path = None
    try:
        if args.resume:
            path = args.resume.expanduser().resolve()
            state = json.loads(path.read_text())
            if oc('config', 'current-context').strip() != state['context'] or oc(
                    'config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}').strip() != state['server']:
                raise RuntimeError('Current cluster/context differs from the saved reset state.')
            if state['phase'] == 'complete':
                print('This reset already completed; nothing to resume.')
                return
            recover(state, path, args.timeout)
            return
        state = snapshot()
        print(json.dumps(state, indent=2), flush=True)
        print('DESTRUCTIVE: all MLflow workspaces, traces, prompts, datasets, runs and models in this DB will be erased.')
        if not args.execute:
            print('DRY RUN ONLY. No resources or data were changed.')
            return
        if args.confirm_database != 'mlflow':
            parser.error('--execute requires --confirm-database mlflow')
        if state['operator']['replicas'] < 1:
            raise RuntimeError('MLflow operator must initially be running.')
        directory = args.backup_root.expanduser().resolve() / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
        path = directory / 'state.json'
        save(state, path)
        print('Private backup/recovery directory: ' + str(directory), flush=True)
        quiesce(state, args.timeout)
        state['phase'] = 'quiesced'
        save(state, path)
        dump = db_command('pg_dump', '--format=custom', binary=True)
        if len(dump) < 100:
            raise RuntimeError('Database dump unexpectedly small; refusing reset.')
        dump_path = directory / 'mlflow-before.dump'
        dump_path.write_bytes(dump)
        os.chmod(dump_path, 0o600)
        oc('exec', '-i', '-n', DB_NS, 'deployment/' + DB_DEPLOYMENT, '-c', 'postgresql',
           '--', 'pg_restore', '--list', payload=dump, binary=True)
        state['phase'] = 'backed-up'
        save(state, path)
        assert_operator_paused(state)
        for d in state['deployments']:
            if get('deployment', d['name'], APPS)['spec'].get('replicas', 1) != 0 or get(
                    'pods', namespace=APPS, selector=d['selector'])['items']:
                raise RuntimeError('An MLflow writer resumed externally; refusing reset.')
        info = database_info()
        validate_database(info)
        if info['other_connections']:
            raise RuntimeError('Database sessions remain; refusing reset.')
        state['phase'] = 'reset-requested'
        save(state, path)
        sql("""
BEGIN;
SET LOCAL lock_timeout = '5s';
DO $$ BEGIN
 IF current_database() <> 'mlflow' OR current_user <> 'mlflow' THEN
  RAISE EXCEPTION 'Unexpected database identity';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()) THEN
  RAISE EXCEPTION 'Other database connections remain';
 END IF;
END $$;
DROP SCHEMA public CASCADE;
CREATE SCHEMA public AUTHORIZATION pg_database_owner;
GRANT USAGE ON SCHEMA public TO PUBLIC;
COMMIT;
""")
        state['phase'] = 'reset'
        save(state, path)
        recover(state, path, args.timeout)
    except (Exception, KeyboardInterrupt) as exc:
        # Never print subprocess stderr or exception contents that may include credentials.
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print('STOPPED: ' + detail, file=sys.stderr)
        if path:
            print('Saved state: ' + str(path), file=sys.stderr)
            print('Some workloads may remain paused. To restore service without repeating the reset:', file=sys.stderr)
            print('python3 setup/reset-mlflow-db.py --resume ' + str(path), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
