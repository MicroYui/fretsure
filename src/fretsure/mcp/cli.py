"""Console entry point for Fretsure's stdout-clean MCP stdio server."""

from fretsure.mcp.server import mcp


def main() -> None:
    """Run the MCP server over stdio; protocol messages exclusively own stdout."""

    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:  # pragma: no cover - normal interactive shutdown
        pass


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    main()


__all__ = ["main"]
