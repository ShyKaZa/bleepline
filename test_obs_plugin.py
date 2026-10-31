"""
End-to-end test of the OBS plugin in a real (portable) OBS:
loads the plugin, starts Voice Guard's link, adds a media source playing speech with
profanity, puts the Voice Guard filter on it, records through OBS, and checks the
recording. Uses tools/obs-33 (portable; your installed OBS, if any, is untouched).

    python test_obs_plugin.py [delay_ms] [model_size] [silence|beep|app] [early]

"app" = the filter uses the Voice Guard app's censor sound; the test gives the app a 600 Hz custom sound.
"early" = early muting on (exercises the plugin's undoable-mute messages).
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import av
import numpy as np
import obsws_python as obs
from better_profanity import profanity

import integration_test as it
from app.detector import load_model
from app.link_server import LinkServer

ROOT = Path(__file__).resolve().parent
OBS_ROOT = ROOT / "tools" / "obs-33"
OBS_EXE = OBS_ROOT / "bin" / "64bit" / "obs64.exe"
PLUGIN = ROOT / "obs-plugin" / "voice-guard-filter.dll"
CONFIG = OBS_ROOT / "config" / "obs-studio"
RECORDINGS = ROOT / "tools" / "obs-recordings"
RATE = 48000


def stop_stale_obs():
    """Kill a portable test OBS left over from an interrupted run (matched by its path only)."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='obs64.exe'\" | "
              f"Where-Object {{ $_.ExecutablePath -like '{OBS_ROOT}*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False)


def prepare_portable_obs():
    stop_stale_obs()
    plugins = OBS_ROOT / "obs-plugins" / "64bit"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN, plugins / PLUGIN.name)
    (OBS_ROOT / "portable_mode.txt").touch()
    ws = CONFIG / "plugin_config" / "obs-websocket"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.json").write_text(json.dumps({
        "alerts_enabled": False, "auth_required": False, "first_load": False,
        "server_enabled": True, "server_password": "", "server_port": 4455,
    }))
    for ini in ("global.ini", "user.ini"):  # skip the first-run wizard (OBS 30 and 31+ layouts)
        path = CONFIG / ini
        if not path.exists():
            path.write_text("[General]\nFirstRun=false\nEnableAutoUpdates=false\n")
    RECORDINGS.mkdir(parents=True, exist_ok=True)


def connect(timeout=60):
    """Connect once OBS has finished starting (it answers 207 'not ready' while loading)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            cl = obs.ReqClient(host="localhost", port=4455, timeout=5)
            cl.get_version()
            return cl
        except Exception:
            time.sleep(1)
    raise RuntimeError("OBS websocket did not come up")


def read_audio(path: Path) -> np.ndarray:
    with av.open(str(path)) as inp:
        rs = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        return np.concatenate([o.to_ndarray().reshape(-1) for f in inp.decode(audio=0) for o in rs.resample(f)])


def main():
    delay_ms = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    model_size = sys.argv[2] if len(sys.argv) > 2 else "tiny.en"
    mode = sys.argv[3] if len(sys.argv) > 3 else "silence"
    wav = Path(tempfile.gettempdir()) / "voiceguard_test_phrase.wav"
    if not wav.exists():
        it.synthesize(wav)
    speech = it.load_wav(wav, RATE)

    prepare_portable_obs()
    model, device, _ = load_model(model_size)
    events, catches = [], []
    censor = ("silence", 1000.0, 0.4, None)
    expected_hz = 1000
    if mode == "app":
        from app import censor_sound
        from test_censor_sound import tone_wav

        censor_sound.SOUNDS_DIR = Path(tempfile.mkdtemp())
        tone_wav(censor_sound.SOUNDS_DIR / "test tone.wav", 600, 0.3)
        censor, expected_hz = ("sound", 1000.0, 0.8, "test tone.wav"), 600
    early = len(sys.argv) > 4 and sys.argv[4] == "early"
    from app import link_server

    sent = {"early": 0, "undo": 0}
    real_early, real_undo = link_server._Session._early, link_server._Session._undo
    link_server._Session._early = lambda self, s, e, t: (sent.__setitem__("early", sent["early"] + 1),
                                                         real_early(self, s, e, t))[1]
    link_server._Session._undo = lambda self, t: (sent.__setitem__("undo", sent["undo"] + 1), real_undo(self, t))[1]
    server = LinkServer(model, on_event=events.append, on_catch=lambda s, w, late: catches.append((s, w, late)),
                        censor=censor, early_mute=early)
    server.start()

    proc = subprocess.Popen([str(OBS_EXE), "--portable", "--multi", "--disable-updater",
                             "--disable-shutdown-check", "--disable-missing-files-check",
                             "--collection", "VoiceGuardTest", "--profile", "VoiceGuardTest"],
                            cwd=OBS_EXE.parent)
    try:
        cl = connect()
        print("OBS", cl.get_version().obs_version, "| websocket connected")
        for inp in cl.get_input_list().inputs:
            # never record the user's real desktop audio or mic; drop leftovers from earlier runs
            if inp["inputKind"].startswith("wasapi_") or inp["inputName"] == "VG test speech":
                cl.remove_input(inp["inputName"])
        time.sleep(0.5)
        scene = cl.get_current_program_scene().current_program_scene_name
        cl.create_input(scene, "VG test speech", "ffmpeg_source",
                        {"local_file": str(wav), "is_local_file": True, "looping": False,
                         "restart_on_activate": False, "close_when_inactive": False}, True)
        kinds = cl.get_source_filter_kind_list().source_filter_kinds
        print("voice_guard_filter registered in OBS:", "voice_guard_filter" in kinds)
        cl.create_source_filter("VG test speech", "Voice Guard", "voice_guard_filter",
                                {"delay_ms": delay_ms, "mode": mode})

        end = time.time() + 15
        while time.time() < end and not any("connected" in e for e in events):
            time.sleep(0.2)
        print("link:", events)

        cl.set_record_directory(str(RECORDINGS))
        for _ in range(3):  # StartRecord is asynchronous; wait until OBS reports it actually running
            cl.start_record()
            end = time.time() + 5
            while time.time() < end and not cl.get_record_status().output_active:
                time.sleep(0.2)
            if cl.get_record_status().output_active:
                break
        else:
            raise RuntimeError("OBS did not start recording")
        time.sleep(0.5)
        cl.trigger_media_input_action("VG test speech", "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
        time.sleep(len(speech) / RATE + delay_ms / 1000 + 1.5)
        rec = Path(cl.stop_record().output_path)
        time.sleep(1.0)
    finally:
        proc.terminate()
        proc.wait(10)
        server.stop()

    log = max((CONFIG / "logs").glob("*.txt"), key=lambda p: p.stat().st_mtime).read_text(errors="replace")
    print("OBS log:", [line.split("] ", 1)[-1] for line in log.splitlines() if "voice-guard" in line])

    out = read_audio(rec)
    env = lambda x: np.abs(x[: len(x) // 480 * 480]).reshape(-1, 480).mean(1)  # noqa: E731
    e_out, e_ref = env(out), env(speech)
    offset = int(np.argmax(np.correlate(e_out - e_out.mean(), e_ref - e_ref.mean(), "valid"))) * 480
    segs, _ = model.transcribe(it.load_wav(wav, 16000), language="en", word_timestamps=True)
    ok = total = 0
    print(f"recording {rec.name}: speech found {offset / RATE:.2f}s in; catches {catches}")
    for w in (w for s in segs for w in s.words):
        word = it.clean(w.word)
        if not profanity.contains_profanity(word):
            continue
        a, b = int(w.start * RATE), int(w.end * RATE)
        region = out[offset + a:offset + b]
        total += 1
        if mode in ("beep", "app"):
            spectrum = np.abs(np.fft.rfft(region))
            peak_hz = np.argmax(spectrum) * RATE / len(region)
            ok += abs(peak_hz - expected_hz) < 30
            print(f"  {word:10s} {w.start:5.2f}s dominant frequency {peak_hz:6.0f} Hz")
        else:
            muted = 1 - np.abs(region).mean() / np.abs(speech[a:b]).mean()
            ok += muted > 0.95
            print(f"  {word:10s} {w.start:5.2f}s muted {muted * 100:5.1f}%")
    intact = np.abs(out[offset:offset + int(1.5 * RATE)]).mean() / np.abs(speech[: int(1.5 * RATE)]).mean()
    verdict = {"beep": "replaced by the 1 kHz beep", "app": "replaced by the app's 600 Hz custom sound"}.get(
        mode, "fully muted")
    print(f"{ok}/{total} {verdict} in the OBS recording; clean speech level {intact * 100:.0f}%"
          + (f" | early-mute messages sent to the plugin: {sent}" if early else ""))
    rec.unlink()


if __name__ == "__main__":
    main()
