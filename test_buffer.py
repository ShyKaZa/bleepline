"""Offline tests for the delay buffer / mute / drift logic. No audio hardware, no model."""
import numpy as np

from app.audio_engine import AudioEngine

RATE = 48000
BLOCK = 480


def make(delay_ms=200):
    return AudioEngine(delay_ms=delay_ms, samplerate=RATE, model_bundle=(None, "none", ""))


def pre_read_lag(eng):
    # Measured after an output block was consumed, so add that block back.
    return eng.total_written - eng.total_read + BLOCK


def step(eng, value=0.1, in_frames=BLOCK, out_frames=BLOCK, do_in=True):
    if do_in:
        eng._in_callback(np.full((in_frames, 2), value, np.float32), in_frames, None, None)
    out = np.zeros((out_frames, 2), np.float32)
    eng._out_callback(out, out_frames, None, None)
    return out


def test_steady_state_and_wraparound():
    eng = make()
    outs = [step(eng) for _ in range(1500)]  # 15s, wraps the 3.2s buffer several times
    assert pre_read_lag(eng) == eng.delay
    assert np.allclose(outs[0], 0.0)  # first block is still inside the delay
    assert np.allclose(np.concatenate(outs[100:]), 0.1)


def test_mute_inside_buffer_with_ramps():
    eng = make(delay_ms=500)
    for _ in range(100):
        step(eng)
    start = eng.total_read + 4800  # 100ms ahead of the reader, already written
    end = start + 4800
    eng._apply_mute(start, end)
    out = np.concatenate([step(eng) for _ in range(40)])[:, 0]
    rel = lambda a: a - (eng.total_read - len(out))  # noqa: E731
    assert np.allclose(out[rel(start): rel(end)], 0.0)
    assert np.allclose(out[rel(end) + eng.ramp:], 0.1)
    assert np.allclose(out[: rel(start) - eng.ramp], 0.1)


def test_mute_reaching_into_unwritten_future():
    eng = make(delay_ms=500)
    for _ in range(100):
        step(eng)
    start = eng.total_written - 2400
    end = eng.total_written + 9600  # 200ms of audio that hasn't been captured yet
    eng._apply_mute(start, end)
    for _ in range(30):
        step(eng)
    idx = np.arange(start, end) % eng.buffer_len
    assert np.allclose(eng.gain_buf[idx], 0.0)


def test_drift_correction_both_directions():
    for out_frames in (BLOCK + 1, BLOCK - 1):  # output clock slightly fast / slow
        eng = make()
        for _ in range(3000):
            step(eng, out_frames=out_frames)
        lag = pre_read_lag(eng)
        assert abs(lag - eng.delay) <= eng.drift_tol + 2, (out_frames, lag, eng.delay)


def test_input_stall_holds_instead_of_shrinking_delay():
    eng = make(delay_ms=300)
    for _ in range(100):
        step(eng)
    silent = [step(eng, do_in=False) for _ in range(60)]  # 600ms with no input
    assert np.allclose(np.concatenate(silent[-20:]), 0.0)
    for _ in range(200):
        step(eng)
    assert pre_read_lag(eng) >= eng.delay - eng.drift_tol


def test_early_mute_undo_and_confirm():
    eng = make(delay_ms=500)
    for _ in range(100):
        step(eng)
    a = eng.total_read + 9600  # 200 ms ahead of what's being played
    undone = eng.add_tentative(a, a + 4800)
    kept = eng.add_tentative(a + 9600, a + 14400)
    eng.retract(undone)  # turned out harmless before it was played: must be heard normally
    confirmed = eng.add_tentative(a + 19200, a + 24000)
    eng._apply_mute(a + 19200, a + 24000)  # the real mute takes over...
    eng.retract(confirmed)  # ...so undoing the early one changes nothing
    out = np.concatenate([step(eng) for _ in range(60)])[:, 0]
    base = eng.total_read - len(out)
    seg = lambda s, e: out[s - base + eng.ramp: e - base - eng.ramp]  # noqa: E731
    assert np.allclose(seg(a, a + 4800), 0.1), "an undone early mute was still applied"
    assert np.allclose(seg(a + 9600, a + 14400), 0.0), "a pending early mute was not applied"
    assert np.allclose(seg(a + 19200, a + 24000), 0.0), "a confirmed word lost its mute"
    assert not eng._tentative, "played-out early mutes should be cleaned up"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
