"""
Configuration Loader
Main configuration class with loading and parsing logic
"""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional
import logging

from .config_dataclasses import (
    BufferConfig, ClaudeConfig, InteractiveConfig, ServerConfig, DatabaseConfig
)

logger = logging.getLogger(__name__)


class Config:
    """The parsed contents of config.yaml, as typed sections.

    Turns loose YAML into dataclasses so the rest of the app reads settings as
    attributes and never worries about missing keys. Any load failure falls back to
    working defaults -- a bad or absent config file degrades the terminal's tuning,
    but must never stop it from running.
    """

    def __init__(self, config_file: str = "config.yaml"):
        """Load immediately, so a Config object is always usable once constructed."""
        self.config_file = config_file
        self._raw_config: Dict[str, Any] = {}

        # Configuration sections (only the sections used by powershell-terminal)
        self.buffer: Optional[BufferConfig] = None
        self.claude: Optional[ClaudeConfig] = None
        self.interactive: Optional[InteractiveConfig] = None
        self.server: Optional[ServerConfig] = None
        self.database: Optional[DatabaseConfig] = None

        self.load()

    def load(self) -> None:
        """Load configuration from YAML file"""
        config_path = Path(self.config_file)

        if not config_path.exists():
            logger.warning(f"Config file not found: {self.config_file}, using defaults")
            self._load_defaults()
            return

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self._raw_config = yaml.safe_load(f) or {}

            self._parse_config()
            logger.info(f"Configuration loaded from {self.config_file}")

        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self._load_defaults()

    def _parse_config(self) -> None:
        """Parse configuration into dataclass objects"""

        # Buffer configuration
        buffer_data = self._raw_config.get('buffer', {})
        self.buffer = BufferConfig(
            max_lines=buffer_data.get('max_lines', 10000),
            cleanup_on_full=buffer_data.get('cleanup_on_full', True)
        )

        # Claude configuration
        claude_data = self._raw_config.get('claude', {})
        self.claude = ClaudeConfig(
            auto_send_errors=claude_data.get('auto_send_errors', True),
            thresholds=claude_data.get('thresholds'),
            truncation=claude_data.get('truncation'),
            error_patterns=claude_data.get('error_patterns')
        )

        # Interactive-input execution configuration
        interactive_data = self._raw_config.get('interactive', {})
        self.interactive = InteractiveConfig(
            idle_ms=interactive_data.get('idle_ms', 600),
            max_s=interactive_data.get('max_s', 30),
            poll_ms=interactive_data.get('poll_ms', 30)
        )

        # Server configuration
        server_data = self._raw_config.get('server', {})
        self.server = ServerConfig(
            host=server_data.get('host', 'localhost'),
            port=server_data.get('port', 8090),
            auto_open_browser=server_data.get('auto_open_browser', True),
            debug=server_data.get('debug', False),
            replay_lines=server_data.get('replay_lines', 40),
            replay_max_bytes=server_data.get('replay_max_bytes', 8192)
        )

        # Database (local SQLite command store)
        database_data = self._raw_config.get('database', {})
        self.database = DatabaseConfig(
            path=database_data.get('path', None),
            retention_days=database_data.get('retention_days', 30)
        )

    def _load_defaults(self) -> None:
        """Load default configuration"""
        self.buffer = BufferConfig()
        self.claude = ClaudeConfig()
        self.interactive = InteractiveConfig()
        self.server = ServerConfig()
        self.database = DatabaseConfig()
