"""Output handling module"""

from .output_buffer import OutputLine, OutputBuffer
from .output_filter import SmartOutputFilter
from .output_filter_commands import (
    filter_installation, filter_file_listing, filter_file_viewing,
    filter_system_info, filter_network_info, filter_log_search
)
from .output_filter_decision import should_send_output, filter_with_errors, truncate_output

__all__ = [
    'OutputLine',
    'OutputBuffer',
    'SmartOutputFilter',
    'filter_installation',
    'filter_file_listing',
    'filter_file_viewing',
    'filter_system_info',
    'filter_network_info',
    'filter_log_search',
    'should_send_output',
    'filter_with_errors',
    'truncate_output',
]
