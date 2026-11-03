"""Provision only a new recorded test database; host the registered HTTP profile."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/api'))
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluation_sources import source_fingerprint
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def read_state(directory):
    state = json.loads((directory / 'target-state.json').read_text(encoding='utf-8'))
    identifier = UUID(state['environment_id']).hex
    if state['database_name'] != 'owner_agent_evaluation_' + identifier + '_test':
        raise ValueError('Recorded database is not this environment test database')
    return state


def admin_params(path):
    # Input is an explicit private file containing only a libpq connection string.
    params = conninfo_to_dict(path.read_text(encoding='utf-8').strip())
    params['dbname'] = 'postgres'
    params['connect_timeout'] = '5'
    return params


def target_params(params, name):
    return {**params, 'dbname': name}


def start(directory, admin_file, manager_key_file):
    from app.config import Settings
    from app.manage import add_user, migrate
    from app.models import CredentialVault
    if (directory / 'target-state.json').exists():
        raise ValueError('Existing environment must be reviewed, never reprovisioned')
    profile = ROOT / 'services/evaluation/profile_support/evaluation_target.py'
    if not profile.is_file():
        raise ValueError('Registered controlled-v1 profile is not ready')
    params = admin_params(admin_file)
    identifier, owner_name, password = uuid4(), 'evaluation-' + uuid4().hex, secrets.token_urlsafe(32)
    name = 'owner_agent_evaluation_' + identifier.hex + '_test'
    data = directory / ('data_' + identifier.hex + '_test')
    commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    state = {'environment_id': str(identifier), 'database_name': name, 'platform_commit': commit,
             'data_dir': str(data), 'status': 'PROVISIONING', 'pid': None}
    state['source_sha256'] = source_fingerprint(ROOT)
    state['tracked_source_diff'] = subprocess.check_output(['git','diff','--name-only','--','services','scripts'], cwd=ROOT, text=True).splitlines()
    save(directory / 'target-state.json', state)
    with psycopg.connect(**params, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    connection = make_conninfo(**target_params(params, name))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else asyncio.new_event_loop) as runner:
        runner.run(migrate(connection))
        runner.run(add_user(connection, owner_name, password, is_admin=True))
    CredentialVault(data / 'model.key')
    with psycopg.connect(connection) as database:
        owner = str(database.execute('SELECT id FROM users WHERE username=%s', (owner_name,)).fetchone()[0])
        namespace = str(database.execute('SELECT id FROM work_sandbox_namespace').fetchone()[0])
        migrations = [row[0] for row in database.execute('SELECT version FROM schema_migrations ORDER BY version')]
    marker = {'environment_id': str(identifier), 'owner_id': owner, 'database_name': name,
              'platform_commit': commit, 'sandbox_namespace': namespace,
              'fixture_profile': {'id': 'controlled-v1', 'version': 1}}
    save(data / 'evaluation-instance.json', marker)
    save(directory / 'credentials.json', {'username': owner_name, 'password': password})
    # The child binds and retains its own socket before publishing its actual port.
    save(directory / 'server-private.json', {'database_url': connection, 'data_dir': str(data),
         'environment_id': str(identifier), 'platform_commit': commit,
         'manager_key_file': str(manager_key_file.resolve()) if manager_key_file else None})
    state.update(owner_id=owner, sandbox_namespace=namespace, required_migrations=migrations, status='STARTING')
    save(directory / 'target-state.json', state)
    environment = {k: v for k, v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'}}
    environment['PYTHON_DOTENV_DISABLED'] = '1'
    with (directory / 'server.log').open('ab') as output:
        child = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), 'serve', '--state', str(directory)],
                                 cwd=directory, env=environment, stdout=output, stderr=output,
                                 **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
    state.update(pid=child.pid)
    save(directory / 'target-state.json', state)
    for _ in range(120):
        if (directory / 'server-ready.json').exists():
            import httpx
            ready = json.loads((directory / 'server-ready.json').read_text(encoding='utf-8'))
            origin = ready['api_base_url']
            with httpx.Client(base_url=origin, headers={'Origin': origin}, trust_env=False, timeout=15) as client:
                client.post('/api/v1/auth/login', json={'username': owner_name, 'password': password}).raise_for_status()
                proof = client.get('/api/v1/agent-evaluations/environment')
                proof.raise_for_status()
                if proof.json()['environment_id'] != str(identifier) or proof.json()['owner_id'] != owner:
                    raise RuntimeError('Target identity did not match newly provisioned environment')
                response = client.post('/api/v1/admin/models', json={
                    'name': 'AE AET-A02 lead ' + identifier.hex[:8], 'model': 'eval::AET-A02::v1',
                    'base_url': origin + '/controlled-model', 'kind': 'chat', 'protocol': 'openai',
                    'provider': 'custom', 'supports_tools': True, 'enabled': True, 'max_output_tokens': 4096})
                response.raise_for_status()
                model_id = response.json()['id']
                state['created_resources'] = [{'kind': 'model', 'id': model_id}]
                save(directory / 'target-state.json', state)
                client.post('/api/v1/agent-evaluations/profiles/model-bindings', json={
                    'case_id': 'AET-A02', 'role': 'lead', 'model_id': model_id}).raise_for_status()
            save(directory / 'target-manifest.json', {'schema_version': 1, 'environment_id': str(identifier),
                 'api_base_url': origin, 'platform_commit': commit, 'owner_id': owner,
                 'required_migrations': migrations, 'fixture_profile': {'id':'controlled-v1','version':1},
                 'resource_bindings': {'AET-A02': model_id}})
            state['status'] = 'READY_WORK_ENABLED' if manager_key_file else 'READY_NON_WORK'
            save(directory / 'target-state.json', state)
            return
        if child.poll() is not None:
            save(directory / 'stopped.json', {'environment_id': str(identifier), 'graceful': False,
                 'startup_failed': True, 'child_exit_code': child.returncode})
            raise RuntimeError('Target child exited; preserve private logs and test database')
        time.sleep(0.5)
    raise RuntimeError('Target readiness not confirmed; child and test database retained')


def serve(directory):
    import uvicorn
    from app.config import Settings
    private = json.loads((directory / 'server-private.json').read_text(encoding='utf-8'))
    state = read_state(directory)
    if source_fingerprint(ROOT)!=state['source_sha256']:
        raise RuntimeError('Target source changed after provisioning; no mixed target startup')
    profile_path = ROOT / 'services/evaluation/profile_support/evaluation_target.py'
    sys.path.insert(0, str(profile_path.parent))
    spec = importlib.util.spec_from_file_location('registered_evaluation_target', profile_path)
    profile = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = profile
    spec.loader.exec_module(profile)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    if port in (8100, 3131):
        raise RuntimeError('Reserved platform port was selected')
    origin = 'http://127.0.0.1:' + str(port)
    sandbox_config = None
    if private.get('manager_key_file'):
        from opensandbox.config import ConnectionConfig
        sandbox_config = ConnectionConfig(domain='127.0.0.1:18180',
            api_key=Path(private['manager_key_file']).read_text(encoding='utf-8').strip(), use_server_proxy=True)
    settings = Settings(database_url=private['database_url'], data_dir=Path(private['data_dir']),
                        web_origin=origin, internal_api_url=origin, work_enabled=sandbox_config is not None, ask_worker_enabled=True,
                        evaluation_instance_id=private['environment_id'], evaluation_platform_commit=private['platform_commit'])
    app = profile.build_app(settings, sandbox_config=sandbox_config)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False))
    async def host():
        async def watch():
            while not server.started and not server.should_exit:
                await asyncio.sleep(0.1)
            if server.started:
                save(directory / 'server-ready.json', {'api_base_url': origin})
            while not server.should_exit:
                if (directory / 'stop.request').exists():
                    server.should_exit = True
                await asyncio.sleep(0.2)
        watcher = asyncio.create_task(watch())
        try:
            await server.serve(sockets=[listener])
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            listener.close()
            save(directory / 'stopped.json', {'environment_id':state['environment_id'], 'graceful':True})
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else asyncio.new_event_loop) as runner:
        runner.run(host())


async def confirm_namespace_empty(directory, namespace):
    private = json.loads((directory / 'server-private.json').read_text(encoding='utf-8'))
    if not private.get('manager_key_file'):
        return
    from opensandbox.config import ConnectionConfig
    from opensandbox.manager import SandboxManager
    from opensandbox.models.sandboxes import SandboxFilter
    config = ConnectionConfig(domain='127.0.0.1:18180',
        api_key=Path(private['manager_key_file']).read_text(encoding='utf-8').strip(), use_server_proxy=True)
    manager = await SandboxManager.create(config)
    try:
        page = 1
        while True:
            inventory = await manager.list_sandbox_infos(SandboxFilter(
                metadata={'purpose': 'owner-agent-work', 'namespace': namespace}, page=page, page_size=100))
            if inventory.sandbox_infos:
                raise RuntimeError('Test namespace remote containers remain; database retained')
            if not inventory.pagination.has_next_page:
                break
            page += 1
    finally:
        await manager.close()


def cleanup_identity(directory):
    state = read_state(directory)
    stopped_path=directory/'stopped.json'
    stopped=json.loads(stopped_path.read_text(encoding='utf-8')) if stopped_path.exists() else None
    if stopped is not None and stopped.get('environment_id')!=state['environment_id']:
        raise ValueError('Stop receipt belongs to another environment')
    if state.get('pid') and stopped is None:
        raise RuntimeError('Graceful target stop is not confirmed; database retained')
    return state,stopped


def pending_resources(database):
    checks = {'task_runs': "status IN ('PENDING','RUNNING')", 'work_queue_runs': "status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED','NEEDS_REVIEW')",
              'work_sandbox_sessions': "state<>'RELEASED'", 'workflow_runs': "status IN ('PENDING','RUNNING','NEEDS_REVIEW')",
              'operation_executions': "status IN ('STARTED','APPROVED','UNKNOWN')",
              'resource_operations': "status IN ('RUNNING','UNKNOWN')", 'model_usage': "status IN ('PENDING','UNKNOWN')",
              'notification_deliveries': "status IN ('SENDING','UNKNOWN')", 'repository_actions': "status IN ('STARTED','UNKNOWN')"}
    for table,predicate in checks.items():
        if database.execute(sql.SQL('SELECT 1 FROM {} WHERE '+predicate+' LIMIT 1').format(sql.Identifier(table))).fetchone():
            raise RuntimeError('Unresolved test resources; database retained')


def cleanup(directory, admin_file):
    state,stopped=cleanup_identity(directory)
    params = admin_params(admin_file)
    target = target_params(params, state['database_name'])
    with psycopg.connect(**target) as database:
        namespace = str(database.execute('SELECT id FROM work_sandbox_namespace').fetchone()[0])
        if namespace != state['sandbox_namespace']:
            raise RuntimeError('Test namespace changed; manual review required')
        pending_resources(database)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else asyncio.new_event_loop) as runner:
        runner.run(confirm_namespace_empty(directory, state['sandbox_namespace']))
    with psycopg.connect(**params, autocommit=True) as admin:
        sessions = admin.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=%s', (state['database_name'],)).fetchone()[0]
        if sessions:
            raise RuntimeError('Test database still has connections; no forced drop')
        if cleanup_identity(directory)!=(state,stopped):
            raise RuntimeError('Stop/state identity changed during cleanup; no drop')
        admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(state['database_name'])))
    state['status'] = 'DATABASE_REMOVED_EVIDENCE_RETAINED'
    save(directory / 'target-state.json', state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start','serve','status','stop','cleanup'])
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--admin-connection-file', type=Path)
    parser.add_argument('--manager-key-file', type=Path)
    args = parser.parse_args()
    directory = args.state.resolve()
    allowed = ROOT / '.local/agent-evaluations'
    if not directory.is_relative_to(allowed) or directory == allowed:
        raise ValueError('Private recorded output required')
    if args.action in ('start', 'cleanup'):
        lock = directory / 'target-operation.lock'
        with lock.open('x') as claimed:
            claimed.write(str(os.getpid()))
        try:
            if args.action == 'start':
                start(directory, args.admin_connection_file, args.manager_key_file)
            else:
                cleanup(directory, args.admin_connection_file)
        finally:
            lock.unlink()
    elif args.action == 'serve':
        serve(directory)
    elif args.action == 'stop':
        read_state(directory)
        (directory / 'stop.request').touch()
    else:
        state = read_state(directory)
        print(json.dumps({k:state.get(k) for k in ('environment_id','status','pid')}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Connection strings and generated credentials never reach stdout/stderr.
        print(json.dumps({'status':'BLOCKED','error_type':type(error).__name__}), file=sys.stderr)
        raise SystemExit(3)
