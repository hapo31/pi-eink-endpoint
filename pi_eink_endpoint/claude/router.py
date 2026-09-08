from http import HTTPStatus

from fastapi import Body, HTTPException, Request

from pi_eink_endpoint.quota.router import create_router

router = create_router("claude")


@router.post("/login/code", status_code=HTTPStatus.ACCEPTED)
async def submit_authentication_code(request: Request, code: str = Body(...)):
    """Submit the code shown after browser authorization to the Claude CLI."""
    try:
        accepted = await request.app.state.claude_service.submit_authentication_code(code)
    except ValueError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from None
    if not accepted:
        raise HTTPException(HTTPStatus.CONFLICT, "Claude login is not awaiting a code")
    return request.app.state.claude_service.snapshot()
