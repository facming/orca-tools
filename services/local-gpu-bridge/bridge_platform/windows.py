"""Preserve the existing Windows native controls and allowlist behavior."""
from .base import Platform

class WindowsPlatform(Platform):
    def native_controls(self, config, save_config):
        from native_control import NativeControl
        from native_tripo import TripoControl
        native, tripo = NativeControl(config, save_config), TripoControl(config)
        if config.get('tripo'):
            tripo.start_keepalive()
        return native, tripo
