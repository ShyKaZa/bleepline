"""`Bleepline.exe --selftest`: checks the installed app end to end and writes selftest.log."""
import time
import traceback

from .paths import DATA_DIR


def run() -> int:
    lines: list[str] = []
    ok = True
    try:
        import numpy as np

        from . import devices
        from . import setup_tasks as st

        lines.append(f"data dir: {DATA_DIR}")
        lines.append(f"gpu: {st.nvidia_gpu_name()} | gpu runtime installed: {st.gpu_runtime_installed()}")
        import soundcard

        try:  # a PC without sound devices (or a build server) still gets the speech engine checked below
            inputs, outputs = devices.list_devices()
            lines.append(f"devices: {len(inputs)} inputs, {len(outputs)} outputs | "
                         f"VB-CABLE: {devices.find_cable_capture()}")
            loopbacks = [m.name for m in soundcard.all_microphones(include_loopback=True) if m.isloopback]
            lines.append(f"desktop audio capture: {len(loopbacks)} output devices can be captured")
        except Exception as exc:
            ok = False
            lines.append(f"audio devices: FAILED ({type(exc).__name__}: {exc})")
        import av

        lines.append(f"recording cleaner: PyAV {av.__version__}")
        lines.append(f"OBS plugin bundled: {st.bundled_obs_plugin() is not None} | "
                     f"installed in OBS: {st.obs_plugin_installed()}")

        from . import wordlists
        from .detector import load_model

        matcher = wordlists.build_matcher()
        lines.append(f"banned word lists loaded: {bool(matcher.match_word('shit'))} "
                     f"({len(wordlists.imported_lists())} extra lists)")

        installed = st.installed_models()
        size = installed[0] if installed else None
        lines.append(f"installed models: {', '.join(installed) or 'none'}")
        if size:
            t0 = time.time()
            model, device, note = load_model(size)
            lines.append(f"model loaded on {device} in {time.time() - t0:.1f}s {note}")
            segments, _ = model.transcribe(np.zeros(16000, np.float32), language="en", vad_filter=True)
            list(segments)
            lines.append("voice activity detector: ok")

            import ctranslate2

            from .fast_asr import FastTranscriber

            t0 = time.time()
            cpu_model, _, _ = load_model(size, "cpu")
            fast = FastTranscriber(cpu_model, "en")
            fast.words(np.zeros(16000 * 2, np.float32))  # silence: exercises the speech gate
            short = np.zeros((1, 80, 400), np.float32)
            cpu_model.model.encode(ctranslate2.StorageView.from_array(short))  # 4 s encoder input
            lines.append(f"live CPU engine (short window): ok, loaded in {time.time() - t0:.1f}s")
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append(f"RESULT: {'OK' if ok else 'FAILED'}")
    (DATA_DIR / "selftest.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1
