"""Tool Template Manager - Save and load reusable tool configuration presets."""

import base64
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

import cv2
import numpy as np

from src.core.tool_roi import (
    REFERENCE_MASTER_H,
    REFERENCE_MASTER_W,
    ROI_SPACE_NORMALIZED,
    ROI_SPACE_WIZARD,
    WIZARD_CANVAS_H,
    WIZARD_CANVAS_W,
    infer_template_roi_space,
    validate_template_tools_roi,
)
from src.utils.logger import get_logger

logger = get_logger('tool_template_manager')


class ToolTemplateManager:
    """
    Persists tool-configuration templates (ROIs, thresholds, optional metadata)
    as JSON under storage/tool_templates/. Reference images are optional for legacy
    templates only; new templates store tools only.
    """

    def __init__(self, storage_config: Dict, program_manager):
        self.program_manager = program_manager
        base = storage_config.get('tool_templates', './storage/tool_templates')
        self.templates_path = base
        self.images_path = os.path.join(base, 'images')
        os.makedirs(self.templates_path, exist_ok=True)
        os.makedirs(self.images_path, exist_ok=True)
        logger.info('Tool template manager initialized at %s', self.templates_path)

    def _template_file(self, template_id: int) -> str:
        return os.path.join(self.templates_path, f'template_{template_id}.json')

    def _next_id(self) -> int:
        max_id = 0
        if not os.path.isdir(self.templates_path):
            return 1
        for name in os.listdir(self.templates_path):
            m = re.match(r'^template_(\d+)\.json$', name)
            if m:
                max_id = max(max_id, int(m.group(1)))
        return max_id + 1

    def _decode_reference_image(self, image_data: str) -> np.ndarray:
        if not image_data:
            raise ValueError('Reference image is required')
        b64 = image_data
        if ',' in b64:
            b64 = b64.split(',', 1)[1]
        try:
            image_bytes = base64.b64decode(b64)
        except Exception as exc:
            raise ValueError('Invalid reference image data') from exc
        nparr = np.frombuffer(image_bytes, np.uint8)
        image_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise ValueError('Could not decode reference image')
        return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    def _save_reference_image(self, template_id: int, image_rgb: np.ndarray) -> str:
        filename = f'template_{template_id}.png'
        file_path = os.path.join(self.images_path, filename)
        self.program_manager.write_reference_image_file(file_path, image_rgb, format='png')
        return file_path

    def _load_reference_image_base64(self, image_path: Optional[str]) -> Optional[str]:
        if not image_path or not os.path.exists(image_path):
            return None
        image_bgr = cv2.imread(image_path)
        if image_bgr is None:
            return None
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        ok, buf = cv2.imencode('.png', cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR))
        if not ok:
            return None
        return base64.b64encode(buf).decode('ascii')

    def validate_tools(self, tools: List[Dict], *, roi_space: Optional[str] = None) -> str:
        if not tools:
            raise ValueError('At least one tool is required')
        if len(tools) > self.program_manager.MAX_TOOLS_PER_PROGRAM:
            raise ValueError(
                f'Maximum {self.program_manager.MAX_TOOLS_PER_PROGRAM} tools allowed, got {len(tools)}'
            )
        position_count = sum(1 for t in tools if t.get('type') == 'position_adjust')
        if position_count > self.program_manager.MAX_POSITION_TOOLS:
            raise ValueError(
                f'Maximum {self.program_manager.MAX_POSITION_TOOLS} position adjustment tool allowed'
            )
        for i, tool in enumerate(tools):
            self.program_manager._validate_tool(tool, i)
        return validate_template_tools_roi(tools, roi_space=roi_space)

    def _snapshot_tools_for_storage(
        self, tools: List[Dict], *, roi_space: Optional[str] = None
    ) -> List[Dict]:
        """
        Persist each tool with explicit ROI layout and threshold.
        Order matches the configured tools list. Validates before snapshot.
        """
        self.validate_tools(tools, roi_space=roi_space)
        snapshot: List[Dict] = []
        for i, t in enumerate(tools):
            roi = t['roi']
            row: Dict = {
                'id': str(t.get('id') or f'tool-{i}'),
                'type': t['type'],
                'name': str(t.get('name') or str(t['type'])),
                'color': str(t.get('color') or '#3b82f6'),
                'roi': {
                    'x': int(round(float(roi['x']))),
                    'y': int(round(float(roi['y']))),
                    'width': int(round(float(roi['width']))),
                    'height': int(round(float(roi['height']))),
                },
                'threshold': int(round(float(t['threshold']))),
            }
            upper = t.get('upperLimit')
            if upper is not None:
                row['upperLimit'] = int(round(float(upper)))
            params = t.get('parameters')
            if isinstance(params, dict) and params:
                row['parameters'] = params
            snapshot.append(row)
        return snapshot

    def get_tools_for_master(
        self,
        template_id: int,
        master_width: int,
        master_height: int,
    ) -> List[Dict]:
        """Return template tools with ROIs scaled to the given master image size."""
        from src.core.tool_roi import normalize_tools_to_master_pixels

        template = self.get_template(template_id, include_image=False)
        if not template:
            raise ValueError(f'Template with ID {template_id} not found')
        tools = template.get('tools') or []
        roi_space = template.get('roi_space') or 'wizard_640x480'
        return normalize_tools_to_master_pixels(
            tools,
            master_width,
            master_height,
            roi_space=roi_space,
        )

    def list_templates(self) -> List[Dict]:
        templates: List[Dict] = []
        if not os.path.isdir(self.templates_path):
            return templates
        for name in sorted(os.listdir(self.templates_path)):
            if not name.startswith('template_') or not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(self.templates_path, name), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                templates.append(self._summary(data))
            except Exception as exc:
                logger.warning('Skipping corrupt template file %s: %s', name, exc)
        templates.sort(key=lambda t: t.get('updated_at', ''), reverse=True)
        return templates

    def _summary(self, data: Dict) -> Dict:
        tools = data.get('tools') or []
        return {
            'id': data.get('id'),
            'name': data.get('name'),
            'description': data.get('description', ''),
            'tool_count': len(tools),
            'has_reference_image': bool(data.get('reference_image_path')),
            'program_id': data.get('program_id'),
            'owned_by_program': bool(data.get('owned_by_program')),
            'created_at': data.get('created_at'),
            'updated_at': data.get('updated_at'),
        }

    def find_template_by_program_id(self, program_id: int) -> Optional[Dict]:
        """Return full template record owned by this program, if any."""
        if not os.path.isdir(self.templates_path):
            return None
        for name in os.listdir(self.templates_path):
            if not name.startswith('template_') or not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(self.templates_path, name), 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                continue
            if data.get('owned_by_program') and data.get('program_id') == program_id:
                return data
        return None

    def list_templates_for_program_ui(
        self, *, configuring_program_id: Optional[int] = None
    ) -> List[Dict]:
        """
        List templates for UI. Excludes other programs' private (owned) templates so
        configuring program 1222 cannot apply program test121's auto template.
        """
        all_tpl = self.list_templates()
        if configuring_program_id is None:
            return all_tpl
        pid = int(configuring_program_id)
        return [
            t
            for t in all_tpl
            if not t.get('owned_by_program') or t.get('program_id') == pid
        ]

    def get_template(self, template_id: int, include_image: bool = True) -> Optional[Dict]:
        path = self._template_file(template_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        result = dict(data)
        if include_image:
            result['reference_image'] = self._load_reference_image_base64(
                data.get('reference_image_path')
            )
        return result

    def create_template(
        self,
        name: str,
        tools: List[Dict],
        description: str = '',
        roi_space: Optional[str] = None,
        *,
        program_id: Optional[int] = None,
        owned_by_program: bool = False,
    ) -> Dict:
        if not name or not str(name).strip():
            raise ValueError('Template name is required')
        clean_name = str(name).strip()
        resolved_space = validate_template_tools_roi(
            tools, roi_space=roi_space or infer_template_roi_space(tools)
        )
        tools_snapshot = self._snapshot_tools_for_storage(tools, roi_space=resolved_space)

        template_id = self._next_id()
        now = datetime.now().isoformat()
        layout_w = WIZARD_CANVAS_W if resolved_space == ROI_SPACE_WIZARD else None
        layout_h = WIZARD_CANVAS_H if resolved_space == ROI_SPACE_WIZARD else None
        roi_layout: Dict = {
            'description': (
                'ROIs in wizard 640×480 pixels; scaled to full master on run '
                f'(e.g. {REFERENCE_MASTER_W}×{REFERENCE_MASTER_H} IMX296).'
                if resolved_space == ROI_SPACE_WIZARD
                else 'ROIs as fractions of master width/height (0–1); resolution-independent.'
                if resolved_space == ROI_SPACE_NORMALIZED
                else 'ROIs in native master pixels; not rescaled on apply.'
            ),
        }
        if layout_w is not None:
            roi_layout['width'] = layout_w
            roi_layout['height'] = layout_h
            roi_layout['reference_master'] = {
                'width': REFERENCE_MASTER_W,
                'height': REFERENCE_MASTER_H,
                'note': 'Typical register-master size; ROIs stretch by master_w/640 and master_h/480.',
            }
        # Tools-only: layout / capture images are not stored server-side.
        data = {
            'id': template_id,
            'name': clean_name,
            'description': (description or '').strip(),
            'template_schema_version': 2,
            'roi_space': resolved_space,
            'roi_layout': roi_layout,
            'tools': tools_snapshot,
            'reference_image_path': None,
            'program_id': int(program_id) if program_id is not None else None,
            'owned_by_program': bool(owned_by_program),
            'created_at': now,
            'updated_at': now,
        }
        with open(self._template_file(template_id), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        logger.info('Tool template created: %s (ID: %s)', clean_name, template_id)
        return self.get_template(template_id, include_image=True)

    def update_template(
        self,
        template_id: int,
        *,
        name: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        description: Optional[str] = None,
        roi_space: Optional[str] = None,
        program_id: Optional[int] = None,
        owned_by_program: Optional[bool] = None,
    ) -> Dict:
        path = self._template_file(template_id)
        if not os.path.exists(path):
            raise ValueError(f'Template with ID {template_id} not found')

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if name is not None:
            clean = str(name).strip()
            if not clean:
                raise ValueError('Template name is required')
            data['name'] = clean

        if description is not None:
            data['description'] = (description or '').strip()

        if program_id is not None:
            data['program_id'] = int(program_id)
        if owned_by_program is not None:
            data['owned_by_program'] = bool(owned_by_program)

        if tools is not None:
            resolved_space = validate_template_tools_roi(
                tools, roi_space=roi_space or data.get('roi_space') or infer_template_roi_space(tools)
            )
            data['roi_space'] = resolved_space
            data['tools'] = self._snapshot_tools_for_storage(tools, roi_space=resolved_space)

        data['updated_at'] = datetime.now().isoformat()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        logger.info('Tool template updated: %s (ID: %s)', data.get('name'), template_id)
        return self.get_template(template_id, include_image=True)

    def upsert_program_template(
        self,
        program_id: int,
        program_name: str,
        tools: List[Dict],
        *,
        template_id_hint: Optional[int] = None,
        roi_space: Optional[str] = None,
        description: str = '',
    ) -> Dict:
        """
        Create or update the one tool template owned by this program.
        Template name always matches the program name; other programs are unaffected.
        """
        if not tools:
            existing = self.find_template_by_program_id(program_id)
            if existing:
                return self.get_template(existing['id'], include_image=False)
            raise ValueError('At least one tool is required to save a program template')

        clean_name = str(program_name).strip()
        if not clean_name:
            raise ValueError('Program name is required for program template')

        existing_data: Optional[Dict] = None
        if template_id_hint is not None:
            hinted = self.get_template(int(template_id_hint), include_image=False)
            if hinted and hinted.get('owned_by_program'):
                if hinted.get('program_id') in (None, program_id):
                    existing_data = hinted

        if existing_data is None:
            existing_data = self.find_template_by_program_id(program_id)

        desc = (description or '').strip() or f'Tool configuration for program {clean_name}'

        if existing_data:
            return self.update_template(
                int(existing_data['id']),
                name=clean_name,
                tools=tools,
                description=desc,
                roi_space=roi_space,
                program_id=program_id,
                owned_by_program=True,
            )

        return self.create_template(
            clean_name,
            tools,
            description=desc,
            roi_space=roi_space,
            program_id=program_id,
            owned_by_program=True,
        )

    def delete_program_template(self, program_id: int) -> bool:
        """Remove the template owned by this program (if any)."""
        existing = self.find_template_by_program_id(program_id)
        if not existing:
            return False
        return self.delete_template(int(existing['id']))

    def delete_template(self, template_id: int) -> bool:
        path = self._template_file(template_id)
        if not os.path.exists(path):
            raise ValueError(f'Template with ID {template_id} not found')

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        image_path = data.get('reference_image_path')
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError as exc:
                logger.warning('Failed to delete template reference image: %s', exc)

        os.remove(path)
        logger.info('Tool template deleted (ID: %s)', template_id)
        return True
