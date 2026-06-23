"""Partial PUT /programs/:id config merges with stored config before validation."""

from src.core.program_manager import ProgramManager


class _FakeDb:
    def __init__(self, program):
        self.program = dict(program)

    def get_program(self, program_id):
        if self.program.get('id') == program_id:
            return dict(self.program)
        return None

    def update_program(self, program_id, updates):
        if self.program.get('id') != program_id:
            return False
        for k, v in updates.items():
            self.program[k] = v
        return True


def _minimal_config(**extra):
    cfg = {
        'triggerType': 'internal',
        'triggerInterval': 1000,
        'brightnessMode': 'normal',
        'focusValue': 50,
        'exposureTimeUs': 5000,
        'analogGain': 1.0,
        'digitalGain': 1.0,
        'tools': [
            {
                'type': 'outline',
                'roi': {'x': 10, 'y': 10, 'width': 100, 'height': 100},
                'threshold': 50,
            }
        ],
        'outputs': {'OUT1': 'Not Used', 'OUT2': 'Not Used'},
    }
    cfg.update(extra)
    return cfg


def test_update_program_merges_partial_config():
    db = _FakeDb(
        {
            'id': 14,
            'name': '12345',
            'config': _minimal_config(toolTemplateId=4),
        }
    )
    pm = ProgramManager(db, {'master_images': '/tmp/iv_test_master'})
    updated = pm.update_program(14, {'config': {'toolTemplateId': 5}})
    assert updated['config']['toolTemplateId'] == 5
    assert len(updated['config']['tools']) == 1
