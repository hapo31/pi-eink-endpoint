"""Shared HTTP boundary for quota display providers."""

from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Request


def create_router(provider: str) -> APIRouter:
    router = APIRouter(prefix=f"/{provider}", tags=[provider])

    def service(request: Request):
        return getattr(request.app.state, f"{provider}_service")

    @router.post("/display/start", status_code=HTTPStatus.ACCEPTED)
    async def start_display(request: Request):
        return service(request).start_display()

    @router.post("/login/start", status_code=HTTPStatus.ACCEPTED)
    async def start_login(request: Request):
        return service(request).start_login()

    @router.get("/status")
    async def status(request: Request):
        return service(request).snapshot()

    @router.post("/refresh", status_code=HTTPStatus.ACCEPTED)
    async def refresh(request: Request):
        if not service(request).refresh():
            raise HTTPException(HTTPStatus.CONFLICT, "Display must be enabled and login complete")
        return service(request).snapshot()

    return router
