"""Read-only installed-engine qualification. Does not launch apps or read user config."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/local-gpu-bridge'))
from bridge_platform import macos_godot, macos_blender, macos_unity

report = {'platform': sys.platform, 'hardware_execution': 'NOT RUN', 'engines': {}}
for name, module in [('godot', macos_godot), ('blender', macos_blender)]:
    candidates = module.discover()
    report['engines'][name] = {'qualified_candidates': candidates, 'execution': 'NOT RUN',
                               'reason': 'No qualified application discovered' if not candidates else 'Requires an isolated approved project execution test'}
unity = []
for candidate in Path('/Applications/Unity/Hub/Editor').glob('*/Unity.app'):
    try:
        exe, version, _ = macos_unity.editor(str(candidate))
        unity.append({'version': version, 'qualified': True})
    except ValueError as error:
        unity.append({'directory_version': candidate.parent.name, 'qualified': False, 'reason': str(error)})
report['engines']['unity'] = {'installations': unity, 'execution': 'NOT RUN'}
report['engines']['comfyui'] = {'execution': 'NOT RUN', 'reason': 'No disposable managed ComfyUI environment supplied; existing user configuration/services are untouched'}
print(json.dumps(report, indent=2))
