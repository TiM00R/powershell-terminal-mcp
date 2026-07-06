"""
Utility Functions
Common helper functions for the remote terminal application
"""

# Import from split modules
from .utils_text import (
    split_lines,
    extract_head_tail,
    count_lines,
)

from .utils_format import (
    format_bytes,
)

from .utils_output import (
    is_error_output,
    extract_error_context,
    detect_command_type,
    parse_ls_output
)
