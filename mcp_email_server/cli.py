import os

import typer

import uvicorn

from mcp_email_server.app import mcp
from mcp_email_server.config import delete_settings
from mcp_email_server.server import create_starlette

app = typer.Typer()


@app.command()
def stdio():
    mcp.run(transport="stdio")


@app.command()
def sse(
    host: str = "localhost",
    port: int = 9557,
):
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.sse_path = "/"
    # mcp.run(transport="sse")

    starlette_app = create_starlette(mcp, "/sse", mcp.sse_app())
    uvicorn.run(starlette_app, host=host, port=port)


@app.command()
def streamable_http(
    host: str = os.environ.get("MCP_HOST", "localhost"),
    port: int = os.environ.get("MCP_PORT", 9557),
):
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = "/"
    # mcp.run(transport="streamable-http")

    starlette_app = create_starlette(mcp, "/mcp", mcp.streamable_http_app())
    uvicorn.run(starlette_app, host=host, port=port)


@app.command()
def ui():
    from mcp_email_server.ui import main as ui_main

    ui_main()


@app.command()
def reset():
    delete_settings()
    typer.echo("✅ Config reset")


if __name__ == "__main__":
    app(["stdio"])
