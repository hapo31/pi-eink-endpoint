"""HTTP boundary for the Codex display service."""

from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/codex", tags=["codex"])


def _service(request: Request):
    return request.app.state.codex_service


@router.post("/display/start", status_code=HTTPStatus.ACCEPTED)
async def start_display(request: Request):
    return _service(request).start_display()


@router.post("/login/start", status_code=HTTPStatus.ACCEPTED)
async def start_login(request: Request):
    return _service(request).start_login()


@router.get("/status")
async def status(request: Request):
    return _service(request).snapshot()


@router.post("/refresh", status_code=HTTPStatus.ACCEPTED)
async def refresh(request: Request):
    if not _service(request).refresh():
        raise HTTPException(HTTPStatus.CONFLICT, "Display must be enabled and login complete")
    return _service(request).snapshot()
