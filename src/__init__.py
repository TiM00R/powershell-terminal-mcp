"""
PowerShell Terminal MCP - Shared AI + user PowerShell session on Windows
"""

__version__ = "0.3.0"

import sys
import os

# Print post-install instructions only if being installed (not when imported by MCP)
if 'pip' in sys.argv[0].lower() or 'setup.py' in sys.argv[0].lower():
    print("\n" + "=" * 70)
    print("PowerShell Terminal MCP v0.3.0 - Installation Complete!")
    print("=" * 70)
    print("\nNext Steps:")
    print("1. Add the server to your Claude Desktop config:")
    print("   (%APPDATA%\\Claude\\claude_desktop_config.json)")
    print("")
    print('   {')
    print('     "mcpServers": {')
    print('       "powershell-terminal": {')

    # Try to detect the installed console-script path
    venv_path = os.path.dirname(os.path.dirname(sys.executable))
    if 'Scripts' in sys.executable:
        exe_path = os.path.join(venv_path, 'Scripts', 'powershell-terminal-mcp.exe')
        print(f'         "command": "{exe_path}"')
    else:
        print('         "command": "powershell-terminal-mcp"')

    print('       }')
    print('     }')
    print('   }')
    print("")
    print("2. Fully quit and relaunch Claude Desktop (system tray -> Exit).")
    print("3. Open a new conversation. On the first tool call the PowerShell")
    print("   session starts and the web terminal opens at http://localhost:8090")
    print("")
    print("Documentation: https://github.com/TiM00R/powershell-terminal-mcp")
    print("=" * 70 + "\n")
