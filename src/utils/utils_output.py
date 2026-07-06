"""
Output Processing Utility Functions
Error detection, command type detection, and output parsing
"""

from typing import List
from .utils_text import split_lines


def is_error_output(text: str, error_patterns: List[str]) -> bool:
    """
    Check if text contains any of the given error patterns (case-sensitive match,
    as the patterns themselves encode the desired casing).

    Args:
        text: Text to check
        error_patterns: List of error patterns to look for

    Returns:
        True if any error pattern is found in the text
    """
    if not text or not error_patterns:
        return False
    return any(pattern in text for pattern in error_patterns)


def extract_error_context(output: str, error_lines: int = 20) -> str:
    """
    Extract error context from command output

    Args:
        output: Full command output
        error_lines: Number of lines to include around error

    Returns:
        Error context
    """
    lines = split_lines(output)

    # Find lines with common error indicators
    error_indicators = ['error', 'failed', 'cannot', 'denied', 'fatal']
    error_line_indices = []

    for i, line in enumerate(lines):
        if any(indicator in line.lower() for indicator in error_indicators):
            error_line_indices.append(i)

    if not error_line_indices:
        # No specific error found, return last N lines
        return '\n'.join(lines[-error_lines:])

    # Get context around first error
    first_error = error_line_indices[0]
    start = max(0, first_error - error_lines // 2)
    end = min(len(lines), first_error + error_lines // 2)

    return '\n'.join(lines[start:end])


def detect_command_type(command: str) -> str:
    """
    Detect command type for smart filtering (PowerShell-oriented).

    Returns one of: install, system_info, network, file_listing, file_viewing,
    log_search, generic. Matches PowerShell cmdlets/aliases and common Windows
    package managers. Aliases (ls, cat, dir, gci, etc.) are included since users
    type them interactively.

    Args:
        command: Command string

    Returns:
        Command type identifier
    """
    c = command.lower().strip()

    # Installation / package managers (winget, choco, scoop, pip, npm, dotnet add)
    install_markers = [
        'winget install', 'choco install', 'scoop install',
        'pip install', 'npm install', 'npm i ', 'dotnet add',
        'install-module', 'install-package', 'install-script',
    ]
    if any(m in c for m in install_markers):
        return 'install'

    # Build output (verbose, treat like install for head/tail summary)
    if any(m in c for m in ['dotnet build', 'dotnet publish', 'msbuild', 'cargo build', 'make']):
        return 'install'

    # System info (concise; truncate only if very long)
    sysinfo = [
        'get-computerinfo', 'systeminfo', 'get-process', 'gps ', 'ps ',
        'get-service', 'gsv ', 'get-volume', 'get-disk', 'get-psdrive',
        'get-hotfix', 'get-host', '$psversiontable', 'get-date',
    ]
    if c.startswith('get-process') or c.startswith('get-service') or any(m in c for m in sysinfo):
        return 'system_info'

    # Network info
    network = [
        'get-netipaddress', 'get-netipconfiguration', 'get-nettcpconnection',
        'get-netadapter', 'get-netroute', 'test-netconnection', 'test-connection',
        'resolve-dnsname', 'ipconfig', 'netstat', 'nslookup', 'ping ',
    ]
    if any(m in c for m in network):
        return 'network'

    # File listing
    listing = ['get-childitem', 'gci ', 'dir ', 'ls ', 'get-childitem']
    if (c.startswith('get-childitem') or c.startswith('gci') or c.startswith('dir')
            or c.startswith('ls') or 'tree' in c):
        return 'file_listing'
    if any(m in c for m in listing):
        return 'file_listing'

    # File viewing
    viewing = ['get-content', 'gc ', 'cat ', 'type ', 'select-object -first',
               'select-object -last', '-head ', '-tail ']
    if c.startswith('get-content') or c.startswith('gc ') or c.startswith('cat') or c.startswith('type'):
        return 'file_viewing'
    if any(m in c for m in viewing):
        return 'file_viewing'

    # Log / text search
    search = ['select-string', 'sls ', 'findstr', 'where-object', '| where', '| ?']
    if c.startswith('select-string') or c.startswith('sls') or c.startswith('findstr'):
        return 'log_search'
    if any(m in c for m in search):
        return 'log_search'

    return 'generic'


def parse_ls_output(output: str) -> dict:
    """
    Parse ls -la output into structured data

    Args:
        output: ls command output

    Returns:
        Dictionary with file statistics
    """
    lines = split_lines(output)
    stats = {
        'total_items': 0,
        'directories': 0,
        'files': 0,
        'total_size': 0
    }

    for line in lines[1:]:  # Skip first line (total)
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 9:
            continue

        stats['total_items'] += 1

        # Check if directory (first char is 'd')
        if line.startswith('d'):
            stats['directories'] += 1
        else:
            stats['files'] += 1

        # Try to get file size (5th column)
        try:
            stats['total_size'] += int(parts[4])
        except (ValueError, IndexError):
            pass

    return stats
