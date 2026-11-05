"""
Web Terminal page - one static HTML page hosting xterm.js.
"""

import html
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FRAGMENTS = Path(__file__).parent.parent / 'static' / 'fragments'
BASE_TITLE = 'PowerShell Terminal'


def _read_fragment(name: str) -> str:
    """Read an HTML fragment from static/fragments."""
    return (FRAGMENTS / name).read_text(encoding='utf-8')


def page_title(owner_label: str = "") -> str:
    """Tab title; names the owning session when known."""
    return "%s — %s" % (BASE_TITLE, owner_label) if owner_label else BASE_TITLE


def render_index_html(owner_label: str = "") -> str:
    """The terminal page: header, xterm.js assets, terminal container, terminal.js.

    owner_label names the Claude session that owns the terminal; terminal.js
    updates it live when ownership moves to another session."""
    owner = html.escape(owner_label)
    return (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>' + html.escape(page_title(owner_label)) + '</title>\n'
        '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">\n'
        + _read_fragment('head.html') + '\n'
        '</head>\n'
        '<body>\n'
        '<header class="pt-header">'
        '<span class="pt-title">PowerShell Terminal (local)</span>'
        '<span class="pt-owner" id="pt-owner">' + owner + '</span>'
        '</header>\n'
        + _read_fragment('terminal_container.html') + '\n'
        '<script src="/static/terminal.js"></script>\n'
        '</body>\n'
        '</html>\n'
    )
