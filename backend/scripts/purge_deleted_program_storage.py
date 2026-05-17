#!/usr/bin/env python3
"""Remove master images and image_history for soft-deleted programs (is_active=0)."""

import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

import yaml
from src.database.db_manager import DatabaseManager
from src.core.program_manager import ProgramManager


def main() -> int:
    config_path = os.path.join(_BACKEND_DIR, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_path = os.path.join(_BACKEND_DIR, config['database']['path'].lstrip('./'))
    db = DatabaseManager(db_path)
    pm = ProgramManager(db, config.get('storage', {}))

    purged = pm.purge_storage_for_inactive_programs()
    print(f'Purged storage for {purged} inactive program(s).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
