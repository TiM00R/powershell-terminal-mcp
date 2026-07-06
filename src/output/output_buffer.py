"""
Output Buffer - Terminal output management
Split into modules for better organization
"""

# Import from split modules
from .output_buffer_base import (
    OutputLine,
    OutputBuffer
)

# Re-export for backward compatibility
__all__ = [
    'OutputLine',
    'OutputBuffer',
]
