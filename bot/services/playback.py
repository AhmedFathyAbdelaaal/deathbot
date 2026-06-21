"""
Playback controller registry.

The Music cog registers itself here at startup as the single playback
controller. The shared queue service and the web API reach the live playback
engine through this indirection, so neither imports the cog directly. With one
Discord server (the locked v1 scope) there is exactly one controller.

The controller object is expected to expose:
    async ensure_playing() -> bool      # start playback if idle + queue non-empty
    def       is_active()  -> bool
    async skip_current()   -> bool
    async stop_all()       -> bool
    def       now_playing() -> QueueEntry | None
"""

_controller = None


def set_controller(controller) -> None:
    global _controller
    _controller = controller


def get_controller():
    return _controller
