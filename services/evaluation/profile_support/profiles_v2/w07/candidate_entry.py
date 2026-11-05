"""Explicit independent Work target overlay; does not register old W07 as ready."""
from contextlib import asynccontextmanager

from app.agent_evaluation_environment import environment
from app.permissions import require_account
from evaluation_target import build_app as original
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from profiles_v2.w07.w07_model import create
from profiles_v2.w07.w07_service import BASE, Service


class Initialize(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    model_id: str = Field(min_length=1,max_length=120)


def build_app(settings, *, sandbox_config, f05_anchor_sha256=None):
    if sandbox_config is None or settings.ask_worker_enabled:
        raise ValueError('Dedicated Work-only target and live bounded sandbox configuration required')
    app = original(settings,sandbox_config=sandbox_config,f05_anchor_sha256=f05_anchor_sha256)
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            # Metadata identity is read without inventing a synthetic Request/authentication.
            marker = application.state.settings.data_dir/'evaluation-instance.json'
            import json
            proof = json.loads(marker.read_text())
            service = Service(application,proof)
            application.state.w07 = service
            profiles = application.state.evaluation_profiles
            profiles.reserve = service.reserve
            mcp = application.state.mcp
            prior_client = getattr(mcp,'http_client_factory',None)
            mcp.http_client_factory = service.client_factory
            engine = application.state.evaluation_work_engine
            prior_model = engine.model_factory
            engine.model_factory = lambda config: create(config,application.state.evaluation_profiles,service) if config.model == 'eval::AET-W07::v2' else prior_model(config)
            application.state.evaluation_execution_limits['profiles']['AET-W07'] = {
                'mode':'work','max_model_calls':1,'max_tokens':15}
            try:
                yield
            finally:
                await service.shutdown()
                profiles.reserve = service.original_reserve
                engine.model_factory = prior_model
                mcp.http_client_factory = prior_client

    app.router.lifespan_context = lifespan

    async def access(request, *, write=False):
        proof = await environment(request)
        required = ['evaluation.read','work.read',*(['evaluation.run','work.execute','tools.use'] if write else [])]
        for permission in required:
            await require_account(request.app.state.db,proof['owner_id'],permission)
        service = request.app.state.w07
        if str(proof['owner_id']) != service.owner or str(proof['environment_id']) != service.environment or str(proof['sandbox_namespace']) != service.namespace:
            raise PermissionError('Foreign test target identity')
        return service

    @app.get(BASE+'/capabilities')
    async def capabilities(request: Request):
        service = await access(request)
        healthy = service.record is None or (service.record['phase'] in {'READY','BOUND'} and service.listener_task is not None and not service.listener_task.done())
        return {'case_id':'AET-W07','definition_version':2,'observer':'terminal-unknown-write-v2',
                'max_roots':1,'max_model_calls':1,'max_tokens':15,'max_sandboxes':2,
                'recovery':'same-root-observation-only','unknown_preserved':True,'healthy':healthy}

    @app.post(BASE+'/receiver')
    async def initialize(body: Initialize, request: Request):
        return await (await access(request,write=True)).initialize(body.model_id)

    @app.get(BASE+'/receiver')
    async def receiver(request: Request):
        return (await access(request)).descriptor()

    @app.post(BASE+'/model-binding')
    async def bind_model(body: Initialize, request: Request):
        service = await access(request,write=True)
        from app.security import administrator
        await administrator(request)
        config = await request.app.state.models.get(body.model_id)
        if config.model != 'eval::AET-W07::v2' or config.kind != 'chat' or config.protocol != 'openai':
            raise ValueError('Only explicit W07 v2 chat model can be sealed')
        request.app.state.evaluation_profiles.register_model(body.model_id,'AET-W07','lead',service.owner)
        return {'sealed':True,'case_id':'AET-W07','definition_version':2,'model_id':body.model_id}

    @app.get(BASE+'/observations')
    async def observations(request: Request):
        return await (await access(request)).observations()

    @app.post(BASE+'/rebuild')
    async def rebuild(request: Request):
        service = await access(request,write=True)
        return await service.recovery_service().rebuild(service.owner,service.scope['root_id'])

    @app.post(BASE+'/close')
    async def close(request: Request):
        return await (await access(request,write=True)).close()

    return app
