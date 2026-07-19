"""
Text Utility Functions
ANSI code handling, text processing, and line manipulation
"""

from typing import List, Tuple


def split_lines(text: str) -> List[str]:
    """
    Split text into lines, handling different line endings

    Args:
        text: Text to split

    Returns:
        List of lines
    """
    # Handle \r\n, \r, and \n
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.split('\n')


def extract_head_tail(text: str, head_lines: int = 30, tail_lines: int = 20) -> Tuple[str, int, bool]:
    """
    Extract head and tail lines from text

    Args:
        text: Full text
        head_lines: Number of lines from start
        tail_lines: Number of lines from end

    Returns:
        Tuple of (extracted_text, total_lines, was_truncated)
    """
    lines = split_lines(text)
    total = len(lines)

    if total <= (head_lines + tail_lines):
        return text, total, False

    head = '\n'.join(lines[:head_lines])
    tail = '\n'.join(lines[-tail_lines:])
    omitted = total - head_lines - tail_lines

    result = f"{head}\n\n[... {omitted} lines omitted ...]\n\n{tail}"
    return result, total, True


def count_lines(text: str) -> int:
    """
    Count number of lines in text

    Args:
        text: Text to count

    Returns:
        Number of lines
    """
    return len(split_lines(text))
