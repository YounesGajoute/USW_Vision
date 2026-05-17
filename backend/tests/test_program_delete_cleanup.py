"""Program delete removes DB row and all related storage."""

import glob
import os
import tempfile

import numpy as np
import pytest

from src.core.program_manager import ProgramManager
from src.database.db_manager import DatabaseManager


def _minimal_config():
    return {
        'triggerType': 'internal',
        'triggerInterval': 100,
        'brightnessMode': 'normal',
        'tools': [
            {
                'type': 'area',
                'name': 'Area 1',
                'color': '#3b82f6',
                'roi': {'x': 0, 'y': 0, 'width': 10, 'height': 10},
                'threshold': 50,
            }
        ],
        'outputs': {'OUT1': 'Not Used'},
    }


def test_delete_program_removes_storage_and_database_rows():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, 'test.db')
        master_dir = os.path.join(tmp, 'master_images')
        history_dir = os.path.join(tmp, 'image_history')
        legacy_history_dir = os.path.join(tmp, 'inspection_history')
        os.makedirs(master_dir)
        os.makedirs(history_dir)
        os.makedirs(legacy_history_dir)

        db = DatabaseManager(db_path)
        pm = ProgramManager(
            db,
            {
                'master_images': master_dir,
                'image_history': history_dir,
                'inspection_history': legacy_history_dir,
            },
        )

        program_id = db.create_program('cleanup-test', _minimal_config())

        master_path = os.path.join(master_dir, f'program_{program_id}_20260101_120000.png')
        with open(master_path, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')
        db.update_program(program_id, {'master_image_path': master_path})

        old_master = os.path.join(master_dir, f'program_{program_id}_20251201_120000.png')
        with open(old_master, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        snap_primary = os.path.join(history_dir, str(program_id), 'insp_OK_20260101.png')
        os.makedirs(os.path.dirname(snap_primary), exist_ok=True)
        with open(snap_primary, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        snap_legacy = os.path.join(legacy_history_dir, str(program_id), 'insp_NG_20260101.png')
        os.makedirs(os.path.dirname(snap_legacy), exist_ok=True)
        with open(snap_legacy, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        db.log_inspection_result(
            program_id=program_id,
            status='OK',
            processing_time_ms=1.0,
            tool_results=[],
            trigger_type='internal',
            image_path=snap_primary,
        )
        db.log_inspection_result(
            program_id=program_id,
            status='NG',
            processing_time_ms=1.0,
            tool_results=[],
            trigger_type='internal',
            image_path=snap_legacy,
        )

        assert pm.delete_program(program_id) is True
        assert pm.get_program(program_id) is None
        assert not os.path.exists(master_path)
        assert not os.path.exists(old_master)
        assert not os.path.isdir(os.path.join(history_dir, str(program_id)))
        assert not os.path.isdir(os.path.join(legacy_history_dir, str(program_id)))

        with db._get_cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) FROM inspection_results WHERE program_id = ?',
                (program_id,),
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT COUNT(*) FROM tools WHERE program_id = ?', (program_id,))
            assert cursor.fetchone()[0] == 0


def test_save_master_image_keeps_only_one_file():
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, 'test.db'))
        master_dir = os.path.join(tmp, 'master_images')
        os.makedirs(master_dir)
        pm = ProgramManager(
            db,
            {
                'master_images': master_dir,
                'image_history': os.path.join(tmp, 'image_history'),
            },
        )
        program_id = db.create_program('single-master', _minimal_config())
        img = np.zeros((100, 100, 3), dtype=np.uint8)

        legacy = os.path.join(master_dir, f'program_{program_id}_20260101_120000.png')
        with open(legacy, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        path = pm.save_master_image(program_id, img, 'png')
        assert path.endswith(f'program_{program_id}.png')
        assert not os.path.exists(legacy)

        pm.save_master_image(program_id, img, 'png')
        masters = glob.glob(os.path.join(master_dir, f'program_{program_id}*'))
        assert len(masters) == 1
        assert masters[0] == path


def test_delete_program_raises_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, 'test.db'))
        pm = ProgramManager(
            db,
            {
                'master_images': os.path.join(tmp, 'master_images'),
                'image_history': os.path.join(tmp, 'image_history'),
            },
        )
        with pytest.raises(ValueError, match='not found'):
            pm.delete_program(99999)
