from typing import Annotated

from fastapi import Depends, Request

from bank_project.application import ApplicationServices


async def get_services(request: Request) -> ApplicationServices:
    return request.app.state.services


Services = Annotated[ApplicationServices, Depends(get_services)]
