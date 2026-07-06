"""
Entry point for powershell-terminal-mcp command
This wraps the async main() function to be callable from setuptools entry_points
"""
import asyncio
import sys


def main():
    """Synchronous entry point that calls async main"""
    # Import here to avoid import issues
    try:
        from src.mcp_server import main as async_main
    except ImportError:
        from mcp_server import main as async_main
    
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
