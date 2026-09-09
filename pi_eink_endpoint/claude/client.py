"""Small wrapper around Claude Code authentication and its OAuth usage endpoint."""

import asyncio
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
URL_RE = re.compile(r"https://[^\s\x1b]+")


class AuthenticationError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self, executable: str, state_dir: Path, *, timeout: float = 30):
        self.executable = executable
        self.state_dir = Path(state_dir)
        self.timeout = timeout
        self._login_process = None

    def _env(self):
        return {**os.environ, "CLAUDE_CONFIG_DIR": str(self.state_dir.resolve())}

    async def authenticated(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        process = await asyncio.create_subprocess_exec(
            self.executable, "auth", "status", "--json", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self._env())
        stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        try:
            status = json.loads(stdout)
        except (ValueError, TypeError):
            return False
        return process.returncode == 0 and status.get("loggedIn") is True

    async def login(self, on_url):
        """Run the official CLI login and expose its browser URL to the display."""
        self._login_process = await asyncio.create_subprocess_exec(
            self.executable, "auth", "login", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.PIPE,
            env=self._env())
        url = None
        async with asyncio.timeout(self.timeout):
            while url is None and (line := await self._login_process.stdout.readline()):
                match = URL_RE.search(line.decode(errors="replace"))
                if match:
                    url = match.group(0).rstrip(".,)")
                    on_url(url)
        # Login remains pending while the URL is completed on another device.
        # Do not use communicate() here: it closes stdin before the code can arrive.
        await self._login_process.stdout.read()
        return await self._login_process.wait() == 0 and url is not None

    async def submit_authentication_code(self, code):
        """Send the browser-provided code to the pending CLI login prompt."""
        process = self._login_process
        if (process is None or process.returncode is not None or process.stdin is None
                or process.stdin.is_closing()):
            return False
        try:
            process.stdin.write(f"{code}\n".encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return False
        return True

    async def cancel_login(self):
        """Stop a pending CLI login so a later request can start cleanly."""
        process = self._login_process
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        self._login_process = None

    async def usage(self):
        return await asyncio.to_thread(self._usage)

    def _usage(self):
        credentials = json.loads((self.state_dir / ".credentials.json").read_text())
        oauth = credentials.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not isinstance(token, str) or not token:
            raise AuthenticationError("Claude Code login required")
        request = urllib.request.Request(USAGE_URL, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "pi-eink-endpoint/0.1",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise AuthenticationError("Claude Code login required") from None
            raise

    async def close(self):
        await self.cancel_login()
