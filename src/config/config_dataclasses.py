"""
Configuration Dataclasses
All configuration dataclass definitions
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class BufferConfig:
    """Output buffer configuration"""
    max_lines: int = 10000
    cleanup_on_full: bool = True


@dataclass
class ClaudeConfig:
    """Claude AI integration configuration.

    thresholds keys must match detect_command_type() outputs
    (install, system_info, network, file_listing, file_viewing, log_search,
    generic). truncation is head/tail line counts. error_patterns is a
    case-sensitive substring list tuned for PowerShell / .NET / Windows.
    """
    auto_send_errors: bool = True
    thresholds: Dict[str, int] = None
    truncation: Dict[str, int] = None
    error_patterns: list = None

    def __post_init__(self):
        """Merge user values over the built-in defaults rather than replacing them,
        so a config.yaml that sets only one threshold (or omits a section) still
        gets a complete, usable set instead of Nones."""
        # Default thresholds
        default_thresholds = {
            "install": 100,
            "system_info": 50,
            "network": 100,
            "file_listing": 50,
            "file_viewing": 100,
            "log_search": 100,
            "generic": 50,
        }

        # Merge with defaults instead of replacing
        if self.thresholds is None:
            self.thresholds = default_thresholds
        else:
            self.thresholds = {**default_thresholds, **self.thresholds}

        # Default truncation
        default_truncation = {
            "head_lines": 30,
            "tail_lines": 20
        }

        if self.truncation is None:
            self.truncation = default_truncation
        else:
            self.truncation = {**default_truncation, **self.truncation}

        # Default error patterns (PowerShell / .NET / Windows console)
        if self.error_patterns is None:
            self.error_patterns = [
                "is not recognized", "The term", "CategoryInfo",
                "FullyQualifiedErrorId", "CommandNotFoundException",
                "ObjectNotFound", "ItemNotFoundException",
                "UnauthorizedAccessException", "Exception", "at line:",
                "Cannot find path", "does not exist", "Access is denied",
                "ERROR", "Error", "error:",
                "FAILED", "Failed", "failed", "FATAL", "Fatal",
                "Cannot", "cannot", "Denied", "denied",
                "Unable to", "unable to", "Could not", "could not", "Aborting",
            ]


@dataclass
class InteractiveConfig:
    """Interactive-input (opt-in REPL/prompt) execution defaults.

    idle_ms - buffer-idle window before classifying the tail. Higher than the pure
              turn-latency value because an early classify costs more here.
    max_s   - safety cap; a long-but-silent step returns RUNNING rather than hanging.
    poll_ms - buffer poll cadence for the wait_interactive loop.
    """
    idle_ms: int = 600
    max_s: int = 30
    poll_ms: int = 30


@dataclass
class ServerConfig:
    """Web server configuration"""
    host: str = "localhost"
    port: int = 8090
    auto_open_browser: bool = True
    debug: bool = False
    # Web terminal on-connect replay (fixes blank reopened tabs). replay_lines:
    # -1 = full buffer (faithful, cursor-safe), 0 = current prompt line only,
    # N = last N lines (byte-capped by replay_max_bytes).
    replay_lines: int = 40
    replay_max_bytes: int = 8192


@dataclass
class DatabaseConfig:
    """Local SQLite store location. path=None -> <project_root>/data/commands.db
    (computed in db.default_db_path). Set an absolute path to keep the DB stable
    regardless of install location or working directory.
    retention_days: startup auto-prune drops conversations (and their commands)
    whose last activity is older than this many days. 0 disables pruning. The
    active conversation is never pruned."""
    path: str = None
    retention_days: int = 30
