"""
Basic Output Buffer
Manages terminal output with scrollback and line tracking
FIXED: Added total_lines_added to handle buffer overflow
"""

import logging
from collections import deque
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class OutputLine:
    """Represents a single line of terminal output"""

    def __init__(self, text: str, timestamp: Optional[datetime] = None):
        self.text = text
        self.timestamp = timestamp or datetime.now()

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"OutputLine('{self.text[:50]}...')"


class OutputBuffer:
    """
    Manages terminal output with scrollback buffer
    """

    def __init__(self, max_lines: int = 1000):
        """
        Initialize Output Buffer

        Args:
            max_lines: Maximum number of lines to keep in buffer
        """
        self.max_lines = max_lines
        self.lines: deque[OutputLine] = deque(maxlen=max_lines)
        self.current_output = ""  # Accumulates output until newline
        self.total_lines_added = 0  # Track total lines ever added (for overflow detection)

    def add(self, text: str) -> List[OutputLine]:
        """
        Add text to buffer

        Args:
            text: Text to add (may contain multiple lines)

        Returns:
            List of newly created OutputLine objects
        """
        new_lines = []
        self.current_output += text

        # Process complete lines
        while '\n' in self.current_output:
            line_text, self.current_output = self.current_output.split('\n', 1)
            line = OutputLine(line_text)
            self.lines.append(line)
            self.total_lines_added += 1  # Track total lines added
            new_lines.append(line)

        return new_lines

    def get_buffer_offset(self) -> int:
        """
        Get the offset between total lines added and current buffer size
        This represents how many lines have been dropped due to buffer overflow

        Returns:
            Number of lines dropped from the beginning
        """
        return self.total_lines_added - len(self.lines)

    def get_text(self, start: int = 0, end: Optional[int] = None) -> str:
        """
        Get text from buffer

        Args:
            start: Start line index (relative to current buffer)
            end: End line index (None for all)

        Returns:
            Concatenated text
        """
        lines_list = list(self.lines)[start:end]
        return '\n'.join(line.text for line in lines_list)

    def get_stats(self) -> dict:
        """
        Get buffer statistics

        Returns:
            Dictionary with statistics
        """
        return {
            'total_lines': len(self.lines),
            'max_lines': self.max_lines,
            'partial_line_length': len(self.current_output),
            'total_lines_added': self.total_lines_added,
            'buffer_offset': self.get_buffer_offset()
        }
