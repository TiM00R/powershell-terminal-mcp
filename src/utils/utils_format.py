"""
Formatting Utility Functions
Byte formatting (the only formatter still in use).
"""


def format_bytes(bytes_count: int) -> str:
    """
    Format byte count in human-readable format

    Args:
        bytes_count: Number of bytes

    Returns:
        Formatted string (e.g., "1.5 KB", "2.3 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} PB"
