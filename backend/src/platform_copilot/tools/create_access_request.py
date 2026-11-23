"""create_access_request: ask for access on the user's behalf. It can never approve."""

from pydantic import BaseModel, Field

from platform_copilot.access import AccessError, create_request
from platform_copilot.tools.base import Tool, ToolContext, ToolError, ToolOutput


class CreateAccessRequestArgs(BaseModel):
    product: str = Field(min_length=3, description="Exact data product name, e.g. sales_es_monthly")
    reason: str = Field(min_length=5, description="The business reason the user gave")


async def create_access_request(args: CreateAccessRequestArgs, ctx: ToolContext) -> ToolOutput:
    try:
        request = await create_request(ctx.pool, args.product, ctx.user.id, args.reason)
    except AccessError as error:
        raise ToolError(str(error)) from None
    return ToolOutput(
        content={
            "request_id": request.id,
            "status": "pending",
            "owner": request.owner,
            "pii": request.pii,
            "note": "The owner must approve it. You cannot approve requests.",
        },
        summary=f"#{request.id}",
    )


TOOL = Tool(
    name="create_access_request",
    description=(
        "Create an access request for a data product on behalf of the current user. Only when"
        " the user explicitly asks for access and gives a reason. The request stays pending"
        " until the product owner approves it; it is never approved automatically."
    ),
    args_model=CreateAccessRequestArgs,
    run=create_access_request,
)
