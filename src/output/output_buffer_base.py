"""
Basic Output Buffer

The human side of the split stream: a bounded scrollback of everything the shell
has printed, used for the web terminal and for status reporting. Bounded on
purpose -- a long-lived session would otherwise grow without limit -- so old lines
are dropped once max_lines is reached.

Because dropping loses information, the buffer also counts every line it has ever
seen (total_lines_added). The difference between that count and the current size
is how callers can tell that overflow happened rather than silently mis-numbering
lines.
"""

import logging
from collections import deque
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class OutputLine:
    """Represents a single line of terminal output"""

    def __init__(self, text: str, timestamp: Optional[datetime] = None):
        """Timestamped at creation so scrollback can show when output arrived."""
        self.text = text
        self.timestamp = timestamp or datetime.now()

    def __str__(self) -> str:
        """The bare text, so a line can be joined or printed directly."""
        return self.text

    def __repr__(self) -> str:
        """Truncated form for debugging, since output lines can be very long."""
        return f"OutputLine('{self.text[:50]}...')"


class OutputBuffer:
    """
    Bounded scrollback of the session's output, in whole lines.

    Text arrives from the pty in arbitrary chunks that rarely align to line
    boundaries, so incomplete text is held in current_output until its newline
    shows up. Only complete lines enter the buffer.
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
