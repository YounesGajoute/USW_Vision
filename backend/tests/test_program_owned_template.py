"""Program-owned tool templates: one per program, named after program."""

import json
import os
import tempfile

import pytest

from src.core.tool_template_manager import ToolTemplateManager


class _FakeProgramManager:
    MAX_TOOLS_PER_PROGRAM = 16
    MAX_POSITION_TOOLS = 1

    def _validate_tool(self, tool, index):
        pass


def test_upsert_program_templates_are_isolated():
    with tempfile.TemporaryDirectory() as tmp:
        ttm = ToolTemplateManager({'tool_templates': tmp}, _FakeProgramManager())

        tools_a = [
            {
                'id': 'a1',
                'type': 'area',
                'name': 'A',
                'color': '#3b82f6',
                'roi': {'x': 10, 'y': 10, 'width': 100, 'height': 80},
                'threshold': 75,
            }
        ]
        tools_b = [
            {
                'id': 'b1',
                'type': 'area',
                'name': 'B',
                'color': '#22c55e',
                'roi': {'x': 20, 'y': 20, 'width': 120, 'height': 90},
                'threshold': 85,
            }
        ]

        ta = ttm.upsert_program_template(12, 'test121', tools_a)
        tb = ttm.upsert_program_template(11, '1222', tools_b)

        assert ta['id'] != tb['id']
        assert ta['name'] == 'test121'
        assert tb['name'] == '1222'
        assert ta['program_id'] == 12
        assert tb['program_id'] == 11

        ta2 = ttm.upsert_program_template(
            12,
            'test121',
            [{**tools_a[0], 'threshold': 90}],
            template_id_hint=ta['id'],
        )
        assert ta2['id'] == ta['id']
        assert ta2['tools'][0]['threshold'] == 90

        tb_reload = ttm.get_template(tb['id'], include_image=False)
        assert tb_reload['tools'][0]['threshold'] == 85


def test_list_templates_hides_other_program_owned():
    with tempfile.TemporaryDirectory() as tmp:
        ttm = ToolTemplateManager({'tool_templates': tmp}, _FakeProgramManager())
        ttm.upsert_program_template(12, 'test121', [
            {
                'id': 'a1',
                'type': 'area',
                'name': 'A',
                'color': '#3b82f6',
                'roi': {'x': 10, 'y': 10, 'width': 100, 'height': 80},
                'threshold': 75,
            }
        ])
        ttm.create_template('Shared import', [
            {
                'id': 's1',
                'type': 'area',
                'name': 'S',
                'color': '#000',
                'roi': {'x': 5, 'y': 5, 'width': 50, 'height': 50},
                'threshold': 70,
            }
        ])

        for_prog_12 = ttm.list_templates_for_program_ui(configuring_program_id=12)
        names = {t['name'] for t in for_prog_12}
        assert 'test121' in names
        assert 'Shared import' in names

        for_prog_11 = ttm.list_templates_for_program_ui(configuring_program_id=11)
        names_11 = {t['name'] for t in for_prog_11}
        assert 'test121' not in names_11
        assert 'Shared import' in names_11
