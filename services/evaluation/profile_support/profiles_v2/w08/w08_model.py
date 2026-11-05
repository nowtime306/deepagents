"""Deterministic real Work tool proposals; checkpoint outputs are checked independently."""
from app.resource_runtime import current_scope
from evaluation_target import ControlledModel, call
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import PrivateAttr

from evaluation_runner.profiles_v2.w08.contract import INSTRUCTIONS, PROMPT, actions


class Model(ControlledModel):
    _root: str = PrivateAttr()
    _tools_bound: bool = PrivateAttr(default=False)

    def bind_tools(self, tools, **kwargs):
        names = [getattr(tool,'name',None) for tool in tools]
        if names.count('read_file') != 1 or names.count('write_file') != 1:
            raise ValueError('Actual standard filesystem tool declarations required')
        self._tools_bound = True
        return self

    def next_message(self, messages):
        humans = [row.content for row in messages if isinstance(row,HumanMessage)]
        results = [row for row in messages if isinstance(row,ToolMessage)]
        if humans != [INSTRUCTIONS+'\n任务目标：'+PROMPT] or not self._tools_bound or len(results)>5:
            raise ValueError('Only fixed nonempty W08 Work task is allowed')
        sequence = actions(self._root)
        if len(results) < len(sequence):
            return call(*sequence[len(results)])
        return AIMessage(content='固定文件操作结束，请以实际检查点和最新导出核验边界。')


def create(config, service):
    scope = current_scope()
    if scope is None or scope.mode != 'work' or str(scope.owner) != service.owner:
        raise ValueError('Original owned real Work scope required')
    service.bind(str(scope.run),config.id)
    model = Model(case_id='AET-W08')
    model._root,model._state = str(scope.run),service.profiles
    return model
