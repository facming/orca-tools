"""Route native jobs without sharing engine processes or adopting historical PIDs."""
import threading
from . import macos_godot
from .macos_godot import UnavailableGodot, Unsupported
from .macos_blender import BlenderControl
from .macos_unity import UnityControl


class UnavailableBlender(UnavailableGodot):
    def status(self, api_healthy=True):
        return {**super().status(api_healthy), 'engine': 'blender'}

    def __getattr__(self, name):
        raise Unsupported('unsupported: Blender storage requires local review; other engines remain independent.')


class UnavailableUnity(UnavailableGodot):
    def __getattr__(self, name):
        raise Unsupported('unsupported: Unity storage requires local review; other engines remain independent.')

    def status(self, api_healthy=True):
        return {**super().status(api_healthy), 'engine': 'unity'}


class NativeControl:
    def __init__(self, config):
        self.lock = threading.RLock()
        try:
            self.godot = macos_godot.GodotControl(config)
        except Exception:
            self.godot = UnavailableGodot()
        try:
            self.blender = BlenderControl(config)
        except Exception:
            self.blender = UnavailableBlender()
        try:
            self.unity = UnityControl(config)
        except Exception:
            self.unity = UnavailableUnity()

    def status(self, api_healthy=True):
        # Keep the Godot 5A status envelope while exposing independent engines.
        godot = self.godot.status(api_healthy)
        return {**godot, 'engines': [godot, self.blender.status(api_healthy), self.unity.status(api_healthy)]}

    def projects(self):
        found = {}
        for control in (self.godot, self.blender, self.unity):
            try:
                rows = control.projects()
            except Unsupported:
                continue
            for row in rows:
                if row['id'] in found:
                    found[row['id']]['project_types'] += row['project_types']
                else:
                    found[row['id']] = row
        return list(found.values())

    def queue(self, body):
        with self.lock:
            return self._queue(body)

    def _queue(self, body):
        engine = body.get('engine')
        if engine not in ('godot', 'blender', 'unity'):
            raise Unsupported('unsupported: engine is not exposed on macOS.')
        # A request ID cannot ambiguously refer to both engine stores.
        for other in (self.godot, self.blender, self.unity):
            if other is not getattr(self, engine) and body.get('request_id') in other.__dict__.get('jobs', {}):
                raise ValueError('Request ID already used by another engine.')
        return getattr(self, engine).queue(body)

    def owner(self, ident):
        owners = [c for c in (self.godot, self.blender, self.unity) if ident in c.__dict__.get('jobs', {})]
        if len(owners) != 1:
            raise ValueError('Unknown or ambiguous task ID.')
        return owners[0]

    def job_status(self, ident):
        return self.owner(ident).job_status(ident)

    def logs(self, ident):
        return self.owner(ident).logs(ident)

    def cancel(self, ident):
        return self.owner(ident).cancel(ident)

    def open_artifact(self, ident, index):
        return self.owner(ident).open_artifact(ident, index)

    def stop(self, project_id):
        return self.godot.stop(project_id)

    def blender_tasks(self, project_id=None):
        return self.blender.blender_tasks(project_id)

    def cancel_blender(self, ident):
        if self.owner(ident) is not self.blender:
            raise ValueError('Not a Blender task.')
        return self.blender.cancel(ident)

    def close(self):
        try:
            self.godot.close()
        finally:
            try:
                self.blender.close()
            finally:
                self.unity.close()
