"""
Configuration initialization helper
Copies default config files to REMOTE_TERMINAL_ROOT on first run
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_package_config_dir() -> Path:
    """Get the package's default config directory"""
    # This file is in src/config/config_init.py
    # Need to find the config/ directory which could be:
    # - When running from source: project_root/config/
    # - When installed via pip: site-packages/config/
    current_file = Path(__file__)
    
    # Try to find config/ directory in order of likelihood
    # FIXED: Added 3-level-up path for pip installations
    possible_locations = [
        current_file.parent.parent.parent / 'config',  # 3 up: site-packages/config/ (pip install)
        current_file.parent.parent / 'config',          # 2 up: project_root/config/ (source)
        current_file.parent / 'config',                 # 1 up: If config is in same dir (fallback)
    ]
    
    for location in possible_locations:
        if location.exists() and location.is_dir():
            return location
    
    raise FileNotFoundError("Could not find default config directory")


