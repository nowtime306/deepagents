"""Fixed explicit A05 v2 factory; uses reviewed canonical base hooks only."""
import inspect

from app.ask_worker import AskWorker
from evaluation_target import build_app as original


def build_app(settings,*,sandbox_config=None,f05_anchor_sha256=None):
    if sandbox_config is not None or f05_anchor_sha256 is not None:
        raise ValueError('A05 v2 cannot attach Work/F05 execution')
    if 'mcp_client_factory' not in inspect.signature(AskWorker).parameters:
        raise ValueError('Reviewed canonical AskWorker instance hook required')
    return original(settings,a05_v2=True)
