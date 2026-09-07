import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from queue import Queue
from threading import Lock, Thread

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from pi_eink_endpoint.claude.client import ClaudeClient
from pi_eink_endpoint.claude.router import router as claude_router
from pi_eink_endpoint.claude.service import ClaudeService
from pi_eink_endpoint.codex.client import AppServerClient
from pi_eink_endpoint.codex.router import router as codex_router
from pi_eink_endpoint.codex.service import CodexService
from pi_eink_endpoint.display import WaveshareDisplay

WAVESHARE_LIB = (
    Path(__file__).parent / "waveshare_e_paper/RaspberryPi_JetsonNano/python/lib"
)
sys.path.insert(0, str(WAVESHARE_LIB))

from waveshare_epd import epd2in9_V3

logger = logging.getLogger(__name__)


class RenderWorker:
    def __init__(self):
        self.render_queue = Queue()
        self._automatic_lock = Lock()
        self._automatic_job = None
        self._automatic_queued = False
        self.render_worker = Thread(
            target=self._render_jobs, name="eink-render", daemon=True
        )
        self.render_worker.start()

    def enqueue_automatic(self, image, *, partial=False):
        """Keep only the newest waiting Codex image; FIFO API jobs are untouched."""
        with self._automatic_lock:
            self._automatic_job = (image.copy(), partial)
            if not self._automatic_queued:
                self._automatic_queued = True
                self.render_queue.put((self._render_automatic, None))

    def _render_automatic(self, _):
        with self._automatic_lock:
            job = self._automatic_job
            self._automatic_job = None
            self._automatic_queued = False
        if job is not None:
            image, partial = job
            update_eink_from_monochrome(image, partial=partial)

    def _render_jobs(self):
        while True:
            job = self.render_queue.get()
            try:
                if job is None:
                    return
                render, payload = job
                render(payload)
            except Exception:
                logger.exception("E-ink rendering failed")
            finally:
                self.render_queue.task_done()

    def close(self):
        # Drain accepted jobs before the worker stops on normal shutdown.
        self.render_queue.put(None)
        self.render_worker.join()


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = RenderWorker()
    app.state.render_worker = worker
    state_dir = Path(
        os.environ.get("CODEX_STATE_DIR", "/var/lib/pi-eink-endpoint/codex")
    )
    state_path = Path(
        os.environ.get("CODEX_DISPLAY_STATE_PATH", state_dir / "display-state.json")
    )
    client = AppServerClient(os.environ.get("CODEX_EXECUTABLE", "codex"), state_dir)
    codex = CodexService(
        client,
        worker.enqueue_automatic,
        state_path=state_path,
        timezone_name=os.environ.get("CODEX_TIMEZONE", "Asia/Tokyo"),
    )
    app.state.codex_service = codex
    await codex.start()
    claude_state_dir = Path(
        os.environ.get("CLAUDE_STATE_DIR", "/var/lib/pi-eink-endpoint/claude")
    )
    claude = ClaudeService(
        ClaudeClient(os.environ.get("CLAUDE_EXECUTABLE", "claude"), claude_state_dir),
        worker.enqueue_automatic,
        state_path=Path(
            os.environ.get(
                "CLAUDE_DISPLAY_STATE_PATH", claude_state_dir / "display-state.json"
            )
        ),
        timezone_name=os.environ.get("CLAUDE_TIMEZONE", "Asia/Tokyo"),
    )
    app.state.claude_service = claude
    await claude.start()
    try:
        yield
    finally:
        await codex.close()
        await claude.close()
        await run_in_threadpool(worker.close)


def create_app() -> FastAPI:
    app = FastAPI(title="Pi E-ink Endpoint")
    # Bookworm's FastAPI 0.92 does not forward the lifespan constructor argument.
    # Register on its Starlette router, which supports the same lifespan protocol.
    app.router.lifespan_context = lifespan
    app.include_router(codex_router)
    app.include_router(claude_router)

    @app.post(
        "/text",
        status_code=HTTPStatus.ACCEPTED,
        responses={400: {"description": "Invalid JSON"}},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "default": "Hello E-ink"}
                            },
                        }
                    }
                },
            }
        },
    )
    async def post_text(request: Request):
        """Queue text without waiting for the panel refresh."""
        # Preserve malformed JSON's 400 response and callers without Content-Type.
        try:
            data = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return PlainTextResponse("Invalid JSON", status_code=HTTPStatus.BAD_REQUEST)
        request.app.state.render_worker.render_queue.put((update_eink_from_text, data))
        return {"message": "E-ink update queued"}

    @app.post(
        "/image",
        status_code=HTTPStatus.ACCEPTED,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def post_image(request: Request):
        """Queue raw image bytes (not multipart) for display."""
        data = await request.body()
        request.app.state.render_worker.render_queue.put((update_eink_from_image, data))
        return {"message": "E-ink update queued"}

    return app


# Keep the factory lazy so importing the HTTP app does not open GPIO resources.
eink_display = WaveshareDisplay(lambda: epd2in9_V3.EPD())


def update_eink_from_text(data: dict):
    return eink_display.show_text(data)


def update_eink_from_image(binary_image: bytes):
    return eink_display.show_image(binary_image)


def update_eink_from_monochrome(image: Image.Image, *, partial: bool = False):
    """Display a quota screen using a partial waveform after its base frame."""
    return eink_display.show_monochrome(image, partial=partial)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)
