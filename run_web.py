"""
run_web.py - manual launcher for the web terminal increment.
Loads config, builds the shared state hub, starts the local pwsh session, then
launches the web terminal (opens a browser). Lets you:
  - watch AI-issued commands stream into the browser, and
  - type commands manually in the browser (raw passthrough).

Run after setup_venv.ps1:
    .\.venv\Scripts\python.exe run_web.py

Type 'ai <command>' at this console to issue an AI command into the shared session
(you should see it appear in the browser). Type 'quit' to exit. Plain ASCII.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from config.config_loader import Config            # noqa: E402
from shared_state import get_shared_state          # noqa: E402
from web.web_terminal import WebTerminalServer      # noqa: E402


def main():
    config = Config("config.yaml")
    # Force the isolated port even if config.yaml still carries the old 8080.
    config.server.port = getattr(config.server, "port", 8090) or 8090
    if config.server.port == 8080:
        config.server.port = 8090
    config.server.host = config.server.host or "127.0.0.1"

    state = get_shared_state()
    state.initialize(config)

    print("Starting pwsh session...")
    if not state.start_session():
        print("FAIL: session did not settle.")
        return
    print("Session ready.")

    server = WebTerminalServer(state, config)
    print("Starting web terminal at http://%s:%s ..." % (config.server.host, config.server.port))
    server.start()  # opens browser

    print("")
    print("Type 'ai <command>' to run an AI command into the session.")
    print("Type 'quit' to exit.")
    try:
        while True:
            line = input("> ").strip()
            if line == "quit":
                break
            if line.startswith("ai "):
                result = state.run_command(line[3:], timeout=30.0)
                print("  status:", result["status"], "exit:", result["exit_code"])
                print("  filtered:", repr(result["filtered"][:200]))
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        print("Shutting down...")
        server.stop()
        state.close_session()
        time.sleep(0.3)


if __name__ == "__main__":
    main()
