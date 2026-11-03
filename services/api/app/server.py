"""Start with a Windows-compatible event loop for psycopg async connections."""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    """Serve the single-process development API on loopback only."""
    parser = argparse.ArgumentParser(description="启动本机平台 API，可指定隔离恢复副本的环境文件")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口应为 1024–65535")
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("环境文件不存在")
        load_dotenv(args.env_file, override=True)
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=15))

    async def serve():
        async def watch():
            while True:
                fence = getattr(app.state, "service_fence", None)
                if fence and fence.lost.is_set():
                    server.should_exit = True
                    return
                await asyncio.sleep(0.2)

        watcher = asyncio.create_task(watch())
        try:
            await server.serve()
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(serve())


if __name__ == "__main__":
    main()
