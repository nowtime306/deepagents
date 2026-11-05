import asyncio
from uuid import uuid4

import pytest

from app.errors import APIError
from app.workflow_compiler import WorkflowNodeFailed, compile_workflow
from app.workflow_definition import Definition, Edge, Node, parameters
from app.workflow_pure_timeout import PureNodeTimeout, confirmed_pure_timeout, run_pure_transform
from app.workflow_runtime import WorkflowRuntime


def definition():
    return Definition(nodes=[Node(id='start', kind='start'),
        Node(id='pure', kind='transform', parameters={'operation':'upper', 'timeout_ms':5}),
        Node(id='normal',kind='end',input='pure'),
        Node(id='fallback',kind='transform',parameters={'operation':'template','template':'已处理超时'}),
        Node(id='failed',kind='end',input='fallback')],
        edges=[Edge(source='start',target='pure'),Edge(source='pure',target='normal'),
            Edge(source='pure',target='fallback',case='error'),Edge(source='fallback',target='failed')])


async def test_real_timer_runtime_and_langgraph_fallback_without_write():
    calls, cancelled, events = [], [], []

    async def slow(node, value, values):
        calls.append(node.id)
        try:
            await asyncio.sleep(0.2)
            return value.upper()
        finally:
            cancelled.append(node.id)

    runtime = WorkflowRuntime(None,None,None,None,pure_transform_factory=slow)

    async def execute(node,text,values):
        return await runtime.execute(node,text,values,uuid4(),'','',uuid4())

    async def observe(event):
        events.append(event)

    output = await compile_workflow(definition(),execute,observe).ainvoke(
        {'input':'seed','values':{},'output':'','route':False})
    assert output['output']=='已处理超时'
    assert calls == cancelled == ['pure']
    assert any(e['event']=='node.failed' and e['error_code']=='WORKFLOW_PURE_TIMEOUT' for e in events)
    assert any(e['event']=='node.handled' and e['node_id']=='pure' for e in events)
    assert not any(e['node_id']=='normal' for e in events)


@pytest.mark.parametrize('error',[
    TimeoutError(),APIError(408,'TIMEOUT','unknown'),APIError(408,'WORKFLOW_PURE_TIMEOUT','forged')])
async def test_arbitrary_timeouts_cannot_fallback(error):
    events=[]
    async def execute(node,text,values):
        raise error
    async def observe(event):
        events.append(event)
    with pytest.raises(WorkflowNodeFailed):
        await compile_workflow(definition(),execute,observe).ainvoke(
            {'input':'seed','values':{},'output':'','route':False})
    assert not any(e['event']=='node.handled' for e in events)


@pytest.mark.parametrize('value',[0,5001,True,1.1,'5',None])
def test_invalid_deadlines_rejected(value):
    with pytest.raises(ValueError):
        parameters(Node(id='pure',kind='transform',parameters={'operation':'upper','timeout_ms':value}))


async def test_default_runtime_remains_compatible():
    runtime=WorkflowRuntime(None,None,None,None)
    value=await runtime.execute(
        Node(id='old',kind='transform',parameters={'operation':'upper'}),'old',{},uuid4(),'','',uuid4())
    assert value=='OLD'


async def test_default_real_executor_returns_before_deadline():
    assert await run_pure_transform(lambda:'safe',1000)=='safe'


async def test_inner_timeout_does_not_forge_deadline_expiry():
    async def inner():
        raise TimeoutError('not the pure executor deadline')
    with pytest.raises(TimeoutError,match='not the pure executor deadline'):
        await run_pure_transform(lambda:'unused',1000,controlled=inner())


@pytest.mark.parametrize('with_parameter',[True,False])
async def test_bare_timeout_class_has_no_expired_node_proof(with_parameter):
    graph=definition()
    if not with_parameter:
        graph.nodes[1].parameters.pop('timeout_ms')
    async def execute(node,text,values):
        raise PureNodeTimeout()
    async def observe(event):
        pass
    with pytest.raises(WorkflowNodeFailed):
        await compile_workflow(graph,execute,observe).ainvoke({'input':'seed','values':{},'output':'','route':False})


async def test_expired_proof_does_not_apply_to_another_equal_node():
    selected=definition().nodes[1]
    async def slow():
        await asyncio.sleep(.1)
    with pytest.raises(PureNodeTimeout) as caught:
        await run_pure_transform(lambda:'unused',5,node=selected,controlled=slow())
    assert confirmed_pure_timeout(caught.value,selected)
    assert not confirmed_pure_timeout(caught.value,selected.model_copy(deep=True))
    selected.parameters['timeout_ms']=10
    assert not confirmed_pure_timeout(caught.value,selected)


async def test_expired_proof_cannot_cross_task_or_resource_context():
    from app.resource_runtime import ResourceRun, active_resources
    selected=definition().nodes[1]
    async def slow():
        await asyncio.sleep(.1)
    with pytest.raises(PureNodeTimeout) as caught:
        await run_pure_transform(lambda:'unused',5,node=selected,controlled=slow())
    assert confirmed_pure_timeout(caught.value,selected)
    async def other_task():
        return confirmed_pure_timeout(caught.value,selected)
    assert not await asyncio.create_task(other_task())
    token=active_resources.set(ResourceRun(None,uuid4(),'workflow',uuid4()))
    try:
        assert not confirmed_pure_timeout(caught.value,selected)
    finally:
        active_resources.reset(token)


async def test_formal_workflow_api_runtime_factory_reaches_real_timer():
    from types import SimpleNamespace

    from app.workflow_api import runtime as runtime_for_request
    async def slow(node,value,values):
        await asyncio.sleep(.1)
        return value
    application=SimpleNamespace(state=SimpleNamespace(db=None,models=None,capabilities=None,
        runtime=SimpleNamespace(model_factory=None,pure_transform_factory=slow)))
    runtime=runtime_for_request(SimpleNamespace(app=application))
    selected=definition().nodes[1]
    with pytest.raises(PureNodeTimeout) as caught:
        await runtime.execute(selected,'seed',{},uuid4(),'','',uuid4())
    assert confirmed_pure_timeout(caught.value,selected)


async def test_parent_cancellation_is_not_converted_to_handled_timeout():
    cancelled=[]
    async def block():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    task=asyncio.create_task(run_pure_transform(lambda:'unused',5000,controlled=block()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled==[True]
