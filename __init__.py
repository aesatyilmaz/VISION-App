from vision.vision import VisionController

_vision_instance = None

def start_vision(camera_index=0, show_window=True):
    global _vision_instance
    _vision_instance = VisionController(camera_index=camera_index, show_window=show_window)
    _vision_instance.start()
    return _vision_instance

def stop_vision():
    global _vision_instance
    if _vision_instance:
        _vision_instance.stop()
        _vision_instance = None

def toggle_vision():
    if _vision_instance:
        _vision_instance.toggle()

def get_vision():
    return _vision_instance