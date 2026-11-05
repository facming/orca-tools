"""Cycles device selection inside an isolated managed Blender; never save preferences."""

def configure(bpy, requested='metal'):
    scene = bpy.context.scene
    scene.cycles.device = 'CPU'
    result = {'backend': 'CPU', 'metal_available': None, 'metal_devices': 0,
              'cpu_fallback': False, 'fallback_reason': None}
    if requested == 'cpu':
        return result
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'METAL'  # Raises if this build has no Metal backend.
        prefs.get_devices()
        devices = [d for d in prefs.devices if d.type == 'METAL']
        result.update(metal_available=bool(devices), metal_devices=len(devices))
        if not devices:
            raise ValueError('No Metal devices')
        for device in prefs.devices:
            device.use = device.type == 'METAL'
        scene.cycles.device = 'GPU'
        result['backend'] = 'METAL'
    except Exception:
        scene.cycles.device = 'CPU'
        result.update(metal_available=False, cpu_fallback=True, fallback_reason='metal_unavailable')
    return result


def render(bpy, device):
    try:
        bpy.ops.render.render(write_still=True)
    except Exception:
        if device['backend'] != 'METAL':
            raise
        # A device may enumerate successfully and still fail on this scene.
        bpy.context.scene.cycles.device = 'CPU'
        device.update(backend='CPU', cpu_fallback=True, fallback_reason='metal_render_failed')
        bpy.ops.render.render(write_still=True)
