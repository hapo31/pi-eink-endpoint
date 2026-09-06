"""Async JSONL transport for Codex App Server (validated against CLI 0.153.0).

The owner consumes notifications and decides when to reconnect. This client never
replays a request: replaying login/start could create a second login attempt.
Protocol: https://learn.chatgpt.com/docs/app-server
"""

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)


class AppServerError(RuntimeError):
    """Sanitized RPC failure; backend messages may contain account data."""

    def __init__(self, code):
        self.code = code
        super().__init__(f"App Server request failed (code {code})")


class AppServerClient:
    def __init__(self, executable: str, state_dir: Path, *, timeout: float = 30):
        self.executable = executable
        self.state_dir = Path(state_dir)
        self.timeout = timeout
        self.notifications = asyncio.Queue()
        self._process = None
        self._tasks = []
        self._pending = {}
        self._next_id = 0
        self._lifecycle = asyncio.Lock()
        self._writes = asyncio.Lock()
        self._ready = False
        self._stderr_bytes = 0

    async def start(self):
        async with self._lifecycle:
            if self._ready and self._process.returncode is None:
                return
            await self._close()
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._stderr_bytes = 0
            self.state_dir.chmod(0o700)
            env = dict(os.environ, CODEX_HOME=str(self.state_dir.resolve()))
            self._process = await asyncio.create_subprocess_exec(
                self.executable, "app-server", "--listen", "stdio://",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env,
                cwd=self.state_dir, limit=4 * 1024 * 1024,
            )
            self._tasks = [asyncio.create_task(self._read()),
                           asyncio.create_task(self._drain_stderr())]
            try:
                await self._request("initialize", {
                    "clientInfo": {"name": "pi_eink_endpoint", "version": "0.1.0"},
                    "capabilities": None,
                })
                await self._send({"method": "initialized"})
                self._ready = True
            except Exception as error:
                await self._close()
                logger.warning("Codex App Server initialization failed (error=%s, stderr_bytes=%d)", type(error).__name__, self._stderr_bytes)
                raise
            except BaseException:
                await self._close()
                raise

    async def request(self, method: str, params=None):
        if not self._ready:
            raise ConnectionError("App Server is not initialized")
        return await self._request(method, params)

    async def _request(self, method, params):
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(self.timeout):
                await self._send({"id": request_id, "method": method, "params": params})
                return await future
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve errors if the write failed concurrently.

    async def _send(self, message):
        async with self._writes:
            if self._process is None or self._process.returncode is not None:
                raise ConnectionError("App Server is not running")
            self._process.stdin.write((json.dumps(message) + "\n").encode())
            await self._process.stdin.drain()

    async def _read(self):
        try:
            while line := await self._process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid App Server message")
                if "id" in message and "method" not in message:
                    future = self._pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(AppServerError(message["error"].get("code")))
                        else:
                            future.set_result(message.get("result"))
                elif "id" in message:
                    await self._send({"id": message["id"], "error": {
                        "code": -32601, "message": "Client method not supported"}})
                elif "method" in message:
                    self.notifications.put_nowait(message)
        except (ValueError, OSError, ConnectionError) as error:
            logger.warning("Codex App Server stream failed (error=%s)", type(error).__name__)
        finally:
            self._ready = False
            self._fail_pending()
            self.notifications.put_nowait({"method": "client/disconnected", "params": {}})

    async def _drain_stderr(self):
        # Drain without retaining content: diagnostics may contain credentials.
        while data := await self._process.stderr.read(8192):
            self._stderr_bytes += len(data)

    def _fail_pending(self):
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("App Server disconnected"))

    async def close(self):
        async with self._lifecycle:
            await self._close()

    async def _close(self):
        self._ready = False
        self._fail_pending()
        process = self._process
        if process is not None:
            if process.stdin:
                process.stdin.close()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._process = None
