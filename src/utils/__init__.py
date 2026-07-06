"""Utilities module"""

from .utils_text import (
    split_lines, extract_head_tail, count_lines
)
from .utils_format import format_bytes
from .utils_output import (
    is_error_output, extract_error_context,
    detect_command_type, parse_ls_output
)

__all__ = [
    # Text utilities
    'split_lines',
    'extract_head_tail',
    'count_lines',
    # Format utilities
    'format_bytes',
    # Output utilities
    'is_error_output',
    'extract_error_context',
    'detect_command_type',
    'parse_ls_output',
]
