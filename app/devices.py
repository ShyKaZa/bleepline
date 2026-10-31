"""Audio device discovery. Imports sounddevice lazily so the GUI can open before packages exist."""

CABLE_CAPTURE_NAME = "CABLE Output"  # what Bleepline records from
CABLE_PLAYBACK_NAME = "CABLE Input"  # what the voice app (Discord) plays into


def refresh():
    """PortAudio caches the device list at startup; re-scan after e.g. installing VB-CABLE."""
    import sounddevice as sd

    sd._terminate()
    sd._initialize()


def _wasapi_index(sd):
    for i, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return i
    return None


def list_devices():
    """(inputs, outputs) as lists of names. WASAPI only on Windows: lowest latency, no duplicates."""
    import sounddevice as sd

    wasapi = _wasapi_index(sd)
    inputs, outputs = [], []
    for d in sd.query_devices():
        if wasapi is not None and d["hostapi"] != wasapi:
            continue
        if d["max_input_channels"] > 0:
            inputs.append(d["name"])
        if d["max_output_channels"] > 0:
            outputs.append(d["name"])
    return inputs, outputs


def _default_name(kind: str):
    import sounddevice as sd

    wasapi = _wasapi_index(sd)
    if wasapi is None:
        idx = sd.default.device[0 if kind == "input" else 1]
    else:
        idx = sd.query_hostapis(wasapi)[f"default_{kind}_device"]
    return sd.query_devices(idx)["name"] if idx is not None and idx >= 0 else None


def default_output_name():
    return _default_name("output")


def default_input_name():
    return _default_name("input")


def windows_default_problems() -> list[str]:
    """Plain-language warnings when Windows' default devices are the virtual cable (VB-CABLE's
    installer often makes them the defaults, which quietly reroutes every app)."""
    problems = []
    out, inp = default_output_name(), default_input_name()
    if out and CABLE_PLAYBACK_NAME in out:
        problems.append("Windows' default speaker is CABLE Input, so all your PC sound (games too) goes through "
                        "Bleepline and you hear it late. Set your headset as the default output.")
    if inp and CABLE_CAPTURE_NAME in inp:
        problems.append("Windows' default microphone is CABLE Output, so apps using the default mic (in-game voice "
                        "chat, Discord on \"Default\") send your PC sound instead of your voice: friends hear "
                        "echoes or a robotic voice. Set your real microphone as the default input.")
    return problems


def find_cable_capture():
    inputs, _ = list_devices()
    return next((n for n in inputs if CABLE_CAPTURE_NAME in n), None)


def find_cable_playback():
    _, outputs = list_devices()
    return next((n for n in outputs if CABLE_PLAYBACK_NAME in n), None)


def resolve(name, kind):
    """Device name -> sounddevice index (WASAPI preferred). kind: 'input' or 'output'."""
    import sounddevice as sd

    if name is None:
        return None
    wasapi = _wasapi_index(sd)
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d[key] > 0 and (wasapi is None or d["hostapi"] == wasapi):
            return i
    raise ValueError(f"{kind} device not found: {name}")


def is_wasapi(index):
    import sounddevice as sd

    return "WASAPI" in sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"]
