#!/usr/bin/env python3
"""Keep one master image file per active program (program_{id}.png)."""

import glob
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
    master_dir = pm.master_images_path

    for program in db.list_programs(active_only=True):
        pid = program['id']
        files = glob.glob(os.path.join(master_dir, f'program_{pid}*'))
        if not files:
            continue

        keep = program.get('master_image_path')
        if not keep or not os.path.isfile(keep):
            keep = max(files, key=os.path.getmtime)

        target = os.path.join(master_dir, f'program_{pid}.png')
        for path in files:
            if os.path.normpath(path) != os.path.normpath(keep):
                os.remove(path)
                print(f'removed duplicate: {path}')

        if os.path.normpath(keep) != os.path.normpath(target):
            if os.path.isfile(target):
                os.remove(target)
            os.rename(keep, target)
            db.update_program(pid, {'master_image_path': target})
            print(f'program {pid}: renamed to {target}')
        elif db.get_program(pid).get('master_image_path') != target:
            db.update_program(pid, {'master_image_path': target})

        remaining = glob.glob(os.path.join(master_dir, f'program_{pid}*'))
        print(f'program {pid}: {len(remaining)} master file(s)')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
