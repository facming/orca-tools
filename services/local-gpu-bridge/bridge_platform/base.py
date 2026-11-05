"""Small capability interface used by the shared HTTP/task implementation."""
class Platform:
    comfy_only = False

    def authorize_peer(self, config, peer):
        return peer in config['allowed_peers']

    def native_controls(self, config, save_config):
        raise NotImplementedError
