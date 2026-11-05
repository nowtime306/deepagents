"""Provision only a new recorded test database; host the registered HTTP profile."""
import argparse
import asyncio
import importlib.util
import importlib
from dataclasses import replace
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
sys.path.insert(0, str(ROOT / 'services/evaluation/src'))
from evaluation_sources import DEPENDENCIES, source_fingerprint
from evaluation_profiles import identity as profile_identity,recorded as recorded_profile,reviewed_contract
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
    recorded_profile(state)
    return state


def admin_params(path):
    # Input is an explicit private file containing only a libpq connection string.
    params = conninfo_to_dict(path.read_text(encoding='utf-8').strip())
    params['dbname'] = 'postgres'
    params['connect_timeout'] = '5'
    return params


def target_params(params, name):
    return {**params, 'dbname': name}


def platform_parent(directory, commit, expected_run=None):
    marker = directory.parent.parent / 'platform-parent.json'
    if not marker.exists():
        if expected_run is not None:
            raise ValueError('Required parent marker disappeared')
        return None
    if expected_run is None:
        raise ValueError('Parent marker needs its explicit requested run identity')
    if marker.is_symlink() or marker.is_junction() or marker.stat().st_size > 4096:
        raise ValueError('Invalid private parent marker')
    value = json.loads(marker.read_text(encoding='utf-8'))
    if value.get('parent_run_id') != str(expected_run):
        raise ValueError('Parent run identity differs')
    required = {'schema_version','contract','parent_run_id','parent_owner_id','candidate_commit','selected_cases'}
    if (set(value) != required or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != 'platform-effect-permit-v1' or value['candidate_commit'] != commit):
        raise ValueError('Private parent marker does not match this checkout')
    for key in ('parent_run_id','parent_owner_id'):
        if str(UUID(value[key])) != value[key]:
            raise ValueError('Canonical parent identity required')
    context = json.loads((directory.parent / 'suite-context.json').read_text(encoding='utf-8'))
    if (context['candidate_commit'] != commit or context['case_id'] not in value['selected_cases']
            or len(value['selected_cases']) != len(set(value['selected_cases']))):
        raise ValueError('Target is not a selected parent case')
    support = ROOT / 'services/evaluation/profile_support'
    sys.path.insert(0, str(support))
    from evaluation_platform_effects import configuration
    if configuration() is None:
        raise ValueError('Required parent delegation was lost before provisioning')
    return {key:value[key] for key in ('schema_version','contract','parent_run_id','parent_owner_id','candidate_commit')} | {'case_id':context['case_id']}


def start(directory, admin_file, manager_key_file, parent_run_id=None,profile_version=1,case_id=None):
    selected=profile_identity(profile_version,case_id)
    context=None
    if profile_version==2:
        context=json.loads((directory.parent/'suite-context.json').read_text(encoding='utf-8'))
        if context.get('profile_version')!=2 or context.get('case_id')!=case_id or context.get('rules_id')!='controlled-a20-v2':
            raise ValueError('Explicit v2 target must belong to this recorded suite context')
        if bool(manager_key_file)!=selected['work']:
            raise ValueError('Fixed v2 target sandbox setting differs from selected case')
        factory_path=ROOT/'services/evaluation/profile_support'/Path(*selected['factory'].split('.'))
        if not factory_path.with_suffix('.py').is_file():
            raise ValueError('Registered canonical v2 factory is not packaged; no database provisioned')
    reviewed,contract_sha=reviewed_contract(ROOT,selected,context)
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
    parent = platform_parent(directory, commit, parent_run_id)
    state = {'environment_id': str(identifier), 'database_name': name, 'platform_commit': commit,
             'data_dir': str(data), 'status': 'PROVISIONING', 'pid': None,
             'profile_version':profile_version,'profile_case_id':case_id if profile_version==2 else None}
    if profile_version==2:
        state['profile_contract_sha256']=contract_sha
        state['profile_context']=context
    state['source_sha256'] = source_fingerprint(ROOT,profile_version=profile_version)
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
    sys.path.insert(0, str(ROOT / "services/evaluation/profile_support"))
    import f05_deadline_contract as f05
    guard_state = f05.initialize_target(directory.parent, str(identifier), owner, namespace, commit, reviewed.get("f05_deadline", {})) if profile_version==1 else {}
    state.update(guard_state)
    marker = {'environment_id': str(identifier), 'owner_id': owner, 'database_name': name,
              'platform_commit': commit, 'sandbox_namespace': namespace,
              'fixture_profile': selected['fixture_profile']}
    save(data / 'evaluation-instance.json', marker)
    if parent is not None:
        save(data / 'evaluation-parent.json', {**parent, 'environment_id':str(identifier), 'owner_id':owner})
    save(directory / 'credentials.json', {'username': owner_name, 'password': password})
    # The child binds and retains its own socket before publishing its actual port.
    save(directory / 'server-private.json', {'database_url': connection, 'data_dir': str(data),
         'environment_id': str(identifier), 'platform_commit': commit,
         'manager_key_file': str(manager_key_file.resolve()) if manager_key_file else None, **guard_state})
    state.update(owner_id=owner, sandbox_namespace=namespace, required_migrations=migrations, status='STARTING')
    save(directory / 'target-state.json', state)
    environment = {k: v for k, v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'}}
    environment['PYTHON_DOTENV_DISABLED'] = '1'
    if parent is not None:
        for key in ('EVALUATION_PLATFORM_PERMIT_ORIGIN','EVALUATION_PLATFORM_PERMIT_SECRET'):
            if not os.environ.get(key):
                raise ValueError('Required parent delegation was lost before startup')
            environment[key] = os.environ[key]
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
                if not guard_state and profile_version==1:
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
                 'required_migrations': migrations, 'fixture_profile': selected['fixture_profile'],
                 'resource_bindings': {} if guard_state or profile_version==2 else {'AET-A02': model_id}})
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
    selected=recorded_profile(state)
    if selected['profile_version']==2:
        if Path(sys.executable).resolve()!=(ROOT/'services/api/.venv/Scripts/python.exe').resolve():
            raise RuntimeError('Versioned target must execute in the reviewed API interpreter')
        if any(Path(importlib.util.find_spec(name).origin).resolve()!=(ROOT/relative).resolve()
               for name,(relative,_) in DEPENDENCIES.items()):
            raise RuntimeError('Versioned target loaded a different API runtime dependency')
        _,contract_sha=reviewed_contract(ROOT,selected,state['profile_context'])
        if contract_sha!=state['profile_contract_sha256']:
            raise RuntimeError('Registered versioned profile contract changed after provisioning')
    if source_fingerprint(ROOT,profile_version=selected['profile_version'])!=state['source_sha256']:
        raise RuntimeError('Target source changed after provisioning; no mixed target startup')
    profile_path = ROOT / 'services/evaluation/profile_support/evaluation_target.py'
    sys.path.insert(0, str(profile_path.parent))
    if selected['profile_version']==1:
        spec = importlib.util.spec_from_file_location('registered_evaluation_target', profile_path)
        profile = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = profile
        spec.loader.exec_module(profile)
    else:
        profile=importlib.import_module(selected['factory'])
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
    if selected['profile_version']==2:
        settings=replace(settings,work_enabled=selected['work'],ask_worker_enabled=state['profile_case_id']=='AET-A05',
            run_timeout=120 if not selected['work'] else 300,automation_enabled=False,evaluation_enabled=False,media_worker_enabled=False)
    import f05_deadline_contract as f05
    required_anchor = f05.startup_anchor(directory.parent, state, private) if selected['profile_version']==1 else None
    app = (profile.build_app(settings, sandbox_config=sandbox_config, f05_anchor_sha256=required_anchor)
        if selected['profile_version']==1 or selected['work'] else profile.build_app(settings))
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


def business_history(database):
    return any(database.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(table))).fetchone()
               for table in ('task_runs','work_queue_runs','workflow_runs','resource_operations'))


def confirm_cleanup_evidence(directory):
    allowed={'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'}
    environment={key:value for key,value in os.environ.items() if key.upper() in allowed}
    environment.update(PYTHON_DOTENV_DISABLED='1',DEEPEVAL_DISABLE_DOTENV='1',DEEPEVAL_TELEMETRY_OPT_OUT='1')
    for key in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA',
                'DEEPEVAL_HOME','ENV_DIR_PATH','DEEPEVAL_RESULTS_FOLDER','DEEPEVAL_CACHE_FOLDER'):
        environment[key]=str(directory)
    python=ROOT/'services/evaluation/.venv/Scripts/python.exe'
    result=subprocess.run([str(python),'-I',str(ROOT/'scripts/evaluation-ready.py'),'--state',str(directory)],
                          cwd=directory,env=environment,capture_output=True,timeout=20,check=False,
                          **({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}))
    if result.returncode!=0:
        raise RuntimeError('Business execution evidence is incomplete; database retained')


def cleanup(directory, admin_file):
    state,stopped=cleanup_identity(directory)
    params = admin_params(admin_file)
    target = target_params(params, state['database_name'])
    with psycopg.connect(**target) as database:
        namespace = str(database.execute('SELECT id FROM work_sandbox_namespace').fetchone()[0])
        if namespace != state['sandbox_namespace']:
            raise RuntimeError('Test namespace changed; manual review required')
        pending_resources(database)
        has_history=business_history(database)
    if has_history:
        confirm_cleanup_evidence(directory)
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
    parser.add_argument('--parent-run-id', type=UUID)
    parser.add_argument('--profile-version',type=int,choices=[1,2])
    parser.add_argument('--case-id',choices=['AET-A05','AET-W07','AET-W08','AET-F07'])
    args = parser.parse_args()
    directory = args.state.resolve()
    if args.action!='start':
        recorded_profile(read_state(directory),args.profile_version,args.case_id)
    allowed = ROOT / '.local/agent-evaluations'
    if not directory.is_relative_to(allowed) or directory == allowed:
        raise ValueError('Private recorded output required')
    if args.action in ('start', 'cleanup'):
        lock = directory / 'target-operation.lock'
        with lock.open('x') as claimed:
            claimed.write(str(os.getpid()))
        try:
            if args.action == 'start':
                start(directory, args.admin_connection_file, args.manager_key_file, args.parent_run_id,
                    args.profile_version if args.profile_version is not None else 1,args.case_id)
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
