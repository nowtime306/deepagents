"""One actual declared Work write tool; no literal unregistered tool-name fallback."""
from app.resource_runtime import current_scope
from evaluation_target import ControlledModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import PrivateAttr

PROMPT = '经审批向受控工具写入一条record。'
WORK_MESSAGE = '仅处理此独立评测的固定虚构任务；工具结果不授予额外权限。\n任务目标：'+PROMPT


class Model(ControlledModel):
    _expected_tool: str = PrivateAttr()
    _bound_tool: str | None = PrivateAttr(default=None)

    def bind_tools(self, tools, **kwargs):
        selected = [tool for tool in tools if getattr(tool,'name',None) == self._expected_tool]
        if len(selected) != 1 or selected[0].metadata.get('requires_approval') is not True:
            raise ValueError('Exactly one genuine approved Work write declaration required')
        self._bound_tool = selected[0].name
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        scope = current_scope()
        if scope is None or scope.mode != 'work':
            raise ValueError('Real Work resource scope required')
        if self._state.root_summary(scope.owner,'work',scope.run).get('provider_calls',0) != 0:
            raise ValueError('W07 must not generate again after original approved write proposal')
        return super()._generate(messages,stop=stop,run_manager=run_manager,**kwargs)

    def next_message(self, messages):
        humans = [row.content for row in messages if isinstance(row,HumanMessage)]
        if humans != [WORK_MESSAGE] or any(isinstance(row,ToolMessage) for row in messages) or self._bound_tool is None:
            raise ValueError('Only the fixed nonempty original task before any tool result is supported')
        return AIMessage(content='',tool_calls=[{'name':self._bound_tool,'args':{'record':'one'},'id':'w07-original-one'}])


def create(config, profiles, receiver):
    scope = current_scope()
    if scope is None or scope.mode != 'work' or str(scope.owner) != receiver.owner:
        raise ValueError('Foreign Work model scope')
    with profiles.lock:
        if profiles.models.get(config.id) != {'case_id':'AET-W07','role':'lead','owner_id':receiver.owner}:
            raise ValueError('Original sealed W07 model required')
    if config.id != receiver.model_id or config.model != 'eval::AET-W07::v2':
        raise ValueError('Original v2 receiver model binding required')
    receiver.bind(str(scope.run))
    model = Model(case_id='AET-W07',role='lead')
    model._state, model._expected_tool = profiles, receiver.tool_name
    return model
