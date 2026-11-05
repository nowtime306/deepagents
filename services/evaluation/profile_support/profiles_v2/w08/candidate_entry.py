"""Explicit private W08 target overlay. No source profile is advertised as implemented."""
import json
from contextlib import asynccontextmanager

from app.agent_evaluation_environment import environment
from app.permissions import require_account
from evaluation_target import build_app as original
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from evaluation_runner.profiles_v2.w08.contract import BASE, check_anchor
from profiles_v2.w08.w08_model import create
from profiles_v2.w08.w08_service import Service


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    model_id: str = Field(min_length=1,max_length=120)
    started_at: str = Field(min_length=20,max_length=50)
    deadline: str = Field(min_length=20,max_length=50)


def build_app(settings,*,sandbox_config,f05_anchor_sha256=None):
    if sandbox_config is None or settings.ask_worker_enabled:
        raise ValueError('Dedicated explicit Work test target required')
    app = original(settings,sandbox_config=sandbox_config,f05_anchor_sha256=f05_anchor_sha256)
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            proof = json.loads((application.state.settings.data_dir/'evaluation-instance.json').read_text())
            service = Service(application,proof)
            application.state.w08 = service
            engine = application.state.evaluation_work_engine
            prior_model,prior_graph = engine.model_factory,engine.agent_graph
            service.profiles.reserve = service.reserve
            service.sandboxes.reserve = service.reserve_sandbox
            service.db.evaluation_effect_guard = service.effect
            engine.model_factory = lambda config: create(config,service) if config.model=='eval::AET-W08::v2' else prior_model(config)

            async def graph(run,model,backend,browser,guard):
                if service.record is not None and run['model_id']==service.record['model_id']:
                    await service.prepare(run,backend,guard)
                    prior_read,prior_write = backend.aread,backend.awrite
                    async def read(*args,**kwargs):
                        service.alive()
                        return await prior_read(*args,**kwargs)
                    async def write(*args,**kwargs):
                        service.alive()
                        return await prior_write(*args,**kwargs)
                    backend.aread,backend.awrite = read,write
                    # Native download/scan/release retain their original cleanup guards.
                    return await prior_graph(run,model,backend,browser,guard)
                return await prior_graph(run,model,backend,browser,guard)

            engine.agent_graph = graph
            try:
                yield
            finally:
                engine.model_factory,engine.agent_graph = prior_model,prior_graph
                service.profiles.reserve = service.original_reserve
                service.sandboxes.reserve = service.original_sandbox_reserve
                if service.original_effect_guard is None:
                    del service.db.evaluation_effect_guard
                else:
                    service.db.evaluation_effect_guard = service.original_effect_guard

    app.router.lifespan_context = lifespan

    async def access(request,*,write=False):
        proof = await environment(request)
        for permission in ['evaluation.read','work.read',*(['evaluation.run','work.execute'] if write else [])]:
            await require_account(request.app.state.db,proof['owner_id'],permission)
        service = request.app.state.w08
        if (str(proof['owner_id']),str(proof['environment_id']),str(proof['sandbox_namespace']))!=(service.owner,service.environment,service.namespace):
            raise PermissionError('Foreign W08 target identity')
        return service

    @app.get(BASE+'/capabilities')
    async def capabilities(request: Request):
        service = await access(request)
        return {'case_id':'AET-W08','definition_version':2,'max_roots':1,'max_model_calls':6,'max_tokens':90,
                'max_sandboxes':1,'physical_symlink':True,'healthy':not service.reconstructed and (service.record is None or service.record['phase'] in {'READY','BOUND'})}

    @app.post(BASE+'/model-binding')
    async def bind(body: Binding,request: Request):
        service = await access(request,write=True)
        from app.security import administrator
        await administrator(request)
        config = await request.app.state.models.get(body.model_id)
        if config.model!='eval::AET-W08::v2' or config.kind!='chat' or config.protocol!='openai':
            raise ValueError('Explicit W08 v2 chat model required')
        if service.record is not None:
            raise ValueError('Original W08 initialization already exists')
        check_anchor(body.started_at,body.deadline)
        service.profiles.register_model(body.model_id,'AET-W08','lead',service.owner)
        return service.initialize(body.model_id,body.started_at,body.deadline)

    @app.get(BASE+'/binding')
    async def binding(request: Request):
        return (await access(request)).anchor()

    @app.get(BASE+'/observations')
    async def observations(request: Request):
        return await (await access(request)).observe()

    return app
