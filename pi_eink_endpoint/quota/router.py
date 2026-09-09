"""Shared HTTP boundary for quota display providers."""

from http import HTTPStatus
import inspect

from fastapi import APIRouter, HTTPException, Request


def create_router(provider: str) -> APIRouter:
    router = APIRouter(prefix=f"/{provider}", tags=[provider])

    def service(request: Request):
        return getattr(request.app.state, f"{provider}_service")

    def controller(request: Request):
        return getattr(request.app.state, "quota_display_controller", None)

    @router.post("/display/start", status_code=HTTPStatus.ACCEPTED)
    async def start_display(request: Request):
        if display_controller := controller(request):
            return display_controller.start_display(provider)
        return service(request).start_display()

    @router.post("/login/start", status_code=HTTPStatus.ACCEPTED)
    async def start_login(request: Request):
        if display_controller := controller(request):
            result = display_controller.start_login(provider)
        else:
            result = service(request).start_login()
        return await result if inspect.isawaitable(result) else result

    @router.get("/status")
    async def status(request: Request):
        if display_controller := controller(request):
            display_controller.activate(provider)
        return service(request).snapshot()

    @router.post("/refresh", status_code=HTTPStatus.ACCEPTED)
    async def refresh(request: Request):
        display_controller = controller(request)
        refreshed = (display_controller.refresh(provider) if display_controller
                     else service(request).refresh())
        if not refreshed:
            raise HTTPException(HTTPStatus.CONFLICT, "Display must be enabled and login complete")
        return service(request).snapshot()

    return router
