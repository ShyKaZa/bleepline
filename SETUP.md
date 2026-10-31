# Voice Guard: full guide

Local, real-time profanity filter for streamers and creators. It catches banned words in game audio,
Discord, or any other app before they reach your stream or your upload. Speech recognition runs on
your own CPU (or GPU); nothing leaves your PC.

## Downloads

| File | What it is |
|---|---|
| `VoiceGuard-Setup.exe` (~119 MB) | Installer. Installs for your Windows user only (no admin), adds Start menu / desktop shortcuts and an entry in **Settings → Apps** with an uninstaller. Re-run a newer one to update; downloads and settings are kept. |
| `VoiceGuard-portable-win64.zip` (~108 MB) | Same app, no install: unzip anywhere and run `VoiceGuard.exe`. |
| `VoiceGuard-OBS-plugin.zip` | Only the OBS filter, with manual install steps. The app can install it for you instead. |

The builds aren't code-signed yet, so Windows SmartScreen may say "Windows protected your PC" the
first time: click **More info → Run anyway**.

### What gets put on your PC, and removing it

| Thing | Where | How to remove |
|---|---|---|
| The app | `%LOCALAPPDATA%\Programs\Voice Guard` (installer) or wherever you unzipped it | Settings → Apps → Voice Guard → Uninstall, or Manage tab → Uninstall |
| Speech models, GPU runtime, settings | `%LOCALAPPDATA%\VoiceGuard` | Manage tab (per item, or "Remove everything"), or tick the box when uninstalling |
| OBS plugin | `C:\ProgramData\obs-studio\plugins\voice-guard-filter` | Manage tab, or tick the box when uninstalling |
| VB-CABLE (optional) | Windows driver from VB-Audio | Its own installer: run `VBCABLE_Setup_x64.exe` as administrator → **Remove Driver** |

The **Manage** tab lists everything with its size. The uninstaller removes the app, its shortcuts and
its Apps entry, and lets you choose whether to remove the downloads and the OBS plugin too.

## Pick a mode

| You use | Mode | Needs | You hear | Stream / file gets |
|---|---|---|---|---|
| OBS Studio | **OBS Studio** (plugin) | OBS plugin | everything live | cleaned audio |
| Streamlabs, XSplit, Twitch Studio, other | **Streamlabs, XSplit, ...** | VB-CABLE | everything live | cleaned audio |
| Recordings for YouTube (ShadowPlay, Game Bar, replays) | **Clean a recording** | nothing extra | n/a | cleaned copy of the file |
| Voice chat only, cleaned in your own ears | **Filter what I hear** | VB-CABLE | cleaned, delayed | cleaned (via Desktop Audio) |

Filtering game audio in your own ears would make you hear the game late, so the streaming modes
clean only the copy that goes to viewers.

### OBS Studio

1. Setup tab → **Install everything** (or **OBS plugin → Install**). This copies the plugin to
   `C:\ProgramData\obs-studio\plugins\voice-guard-filter\bin\64bit\`, where OBS 30–33 load it.
   Restart OBS.
2. In OBS, right-click an audio source (Desktop Audio, your mic, a Discord or game Application Audio
   Capture) → **Filters** → **+** → **Voice Guard (profanity filter)**. Set the delay and
   silence/beep there (default 500 ms, silence).
3. Add OBS's built-in **Render Delay** filter to your webcam and game capture with the same delay so
   video stays in sync with the delayed audio.
4. Keep Voice Guard open in **OBS Studio** mode and press **Start filtering**. The Live tab shows
   which OBS sources are connected and every word that gets muted.

If Voice Guard isn't running, the filter still passes audio through (delayed), so starting or
stopping the app never shifts your audio/video sync. It reconnects by itself within a second.

### Streamlabs, XSplit, Twitch Studio or any other app

1. Install VB-CABLE (Setup tab → **Get VB-CABLE**, run the installer as administrator, reboot).
2. In Voice Guard pick **Streamlabs, XSplit, ...** and choose the device you listen on (your headset).
   Voice Guard records what plays there and sends a cleaned, delayed copy into **CABLE Input**.
3. In your streaming app, turn off its own desktop/system audio and add **CABLE Output (VB-Audio
   Virtual Cable)** as an audio input instead. Delay your video by the same amount if the app can.

### Clean a recording

Pick a file and press **Clean recording**. Voice Guard analyses the whole file (all audio tracks)
with full sentence context, so it's more accurate than live filtering. It writes
`<name> (clean).<ext>` with the video stream copied byte-for-byte and only the audio re-encoded, plus
a `- muted words.txt` list with timestamps so you can check before uploading.

### Filter what I hear

Set Discord's *Output Device* to **CABLE Input (VB-Audio Virtual Cable)**. Voice Guard plays the
cleaned call into your headset, and OBS's Desktop Audio picks it up. Don't capture Discord in OBS with
*Application Audio Capture*: that records the raw call.

## Customising

### Banned words tab
- **Lists in use:** the built-in English list (916 words) plus any lists you import (`.txt` or
  `.csv`, one word or phrase per line) or download per language (25 languages from the LDNOOBW
  project, CC BY 4.0). Each list can be switched off or removed.
- **Your words**, with simple syntax: `word` matches the exact word, `word*` anything starting with it
  (`fuck*` also catches fucker, fucking), and several words form a phrase. Case and accents are
  ignored (`kurva` = `Kurvá`). **Export to file** saves your list to share it.
- **Allow list:** words that are never muted, even if a list contains them (e.g. hell, damn).
- **Mute words written with \*:** the speech model sometimes writes swear words as `f**k` itself.
- **Test a word or phrase:** tells you whether it would be muted, and which list it comes from.

### Models tab
Download any of these, or add your own:

| Model | Language | Size | Best for |
|---|---|---|---|
| tiny.en | English | 75 MB | live filtering (recommended) |
| base.en | English | 145 MB | recordings; live at 700 ms+ |
| small.en | English | 485 MB | recordings |
| distil-small.en | English | 330 MB | recordings, faster than small.en |
| tiny | Multilingual | 75 MB | live filtering in any language |
| base / small / medium | Multilingual | 145 MB / 485 MB / 1.5 GB | recordings in any language |
| large-v3-turbo | Multilingual | 1.6 GB | recordings, best accuracy (GPU) |

**Your own model:** any faster-whisper (CTranslate2) folder containing `model.bin`, `config.json`
and `tokenizer.json`. Use **Add model folder**, or drop it into the models folder (**Open models
folder**). It then shows up everywhere the built-in models do.

### Options tab
- **Language:** auto-detect or a fixed language (25 listed). English-only models always use English;
  for other languages pick a multilingual model and download that language's word list.
- **Sensitivity:** how sure the speech model must be before muting.
- **Early muting (experimental, off by default):** mutes a word as soon as it *starts* like a banned
  word ("fu…", "kur…"). If the finished word turns out harmless, the mute is undone before it's played
  whenever there's time. Measured on the CPU engine (tiny), it lifted "bullshit" from 52–66% to
  88–89% muted at 250–300 ms. The cost: about 1 extra innocent word in 13 look-alikes (words that
  genuinely start like a banned word, e.g. "she") got cut. Works in every live mode, including OBS
  (plugin protocol v3).
- **Mute before / after word:** extra audio cut around each word.
- **Censor sound:** silence, a beep (pitch 300–3000 Hz) or **your own sound file** (any audio
  format, first 5 seconds, restarted for each word and repeated for long ones). There's a volume
  slider and a Preview button. It applies to live filtering, OBS (the filter's default "Use the
  Voice Guard app's setting") and cleaned recordings.
- **Appearance:** dark (default), light, or same as Windows; switches instantly.
- **Startup:** start with Windows, optionally minimized. Combined with "Start filtering
  automatically" it's set-and-forget.

## Models and resource use

Live filtering runs on the **CPU by default** and leaves the GPU free for games. Normally Whisper
pads every check to 30 seconds of audio. Voice Guard feeds it only the live window (3–4 s of
context), which is about 36× less work for the same words. Idle engine threads sleep between checks,
and nothing runs at all while no one is speaking.

Measured on real Windows audio devices at 600 ms delay (16-thread CPU, RTX 3070 Ti), with the test
audio containing four swear words:

| Live engine | Caught | CPU | GPU | Per check |
|---|---|---|---|---|
| tiny.en on CPU (default) | 3/4 | ~3.7% | 0% | 33 ms |
| tiny (multilingual) on CPU | 4/4 | ~3.9% | 0% | 31 ms |
| base.en on CPU | 4/4 | ~7.4% | 0% | 70 ms |
| tiny.en on GPU | 4/4 | ~1.9% | ~4% | 16 ms |

The old approach (padding to 30 s) needed ~90% GPU with a game running, and on CPU it took 375 ms
per check and caught nothing. The GPU option (Options → Live filtering runs on) is only worth it for
bigger models. Cleaning recordings uses the GPU when one is available, because it's a one-off batch
job.

## How reliable is it?

A word can only be recognised once most of it has been spoken, so the delay has to be longer than
your longest banned word plus the time to check it. Results from the tests in this folder, using
synthesized speech with four swear words:

- OBS plugin in real OBS 33, recorded by OBS, tiny.en: 4/4 fully muted in earlier runs (also with the
  beep and with a custom sound sent from the app). The latest runs give **3/4** at 500 and 600 ms:
  the speech model mishears the last word in OBS's copy of the audio. Being looked into.
- Multilingual `tiny` on auto-detect, live at 600 ms: 4/4.
- Desktop-audio mode on real Windows audio devices, tiny.en: **24/24 words fully muted** at 500 and 600 ms.
- Same with base.en at 600 ms: 15/16 (base.en reacts too slowly live; use it for recordings).
- Recording cleaner, base.en: 4/4 muted, video untouched, clean speech kept at 100%.

Every live catch is logged. If one says **caught late**, the start of the word may have been heard;
raise the delay. Real voices are messier than test speech, and loud game audio makes recognition
harder, so treat this as a strong safety net, not a guarantee.

## Running from source / building

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt
venv/Scripts/python main.py
```

From source, data lives in this folder; the packaged app keeps it in `%LOCALAPPDATA%\VoiceGuard`.

```bash
venv/Scripts/pip install pyinstaller
venv/Scripts/python build_obs_plugin.py
venv/Scripts/python build.py
```

This writes the three downloads above to `release/`. Models, the GPU runtime and VB-CABLE are
downloaded from the Setup tab on first run.

Command-line switches: `VoiceGuard.exe --selftest` checks an install and writes `selftest.log` to the
data folder; `VoiceGuard.exe --uninstall [--silent] [--keep-data]` is the uninstaller;
`VoiceGuard-Setup.exe --silent [--dir PATH] [--desktop] [--launch]` installs without a window.

### Building the OBS plugin

The plugin (`obs-plugin-src/voice-guard-filter.c`) is plain C, built with Zig instead of Visual Studio,
against OBS 30 headers so it loads in OBS 30 and newer:

```bash
venv/Scripts/pip install ziglang==0.16.0
git clone --depth 1 --branch 30.0.2 --filter=blob:none --sparse https://github.com/obsproject/obs-studio.git tools/obs-src-30
git -C tools/obs-src-30 sparse-checkout set libobs
# unzip OBS-Studio-33.0.1-Windows-x64.zip (from the OBS GitHub release) into tools/obs-33
venv/Scripts/python build_obs_plugin.py
```

Only `obs.dll`'s export names are used, to build the import library. Run `build_obs_plugin.py` before
`build.py` so the exe ships the plugin.

## Tests

| Command | What it checks |
|---|---|
| `python test_buffer.py` | delay buffer, mute ramps, future mutes, clock drift, input stalls |
| `python integration_test.py [delay] [model]` | live engine end to end with real-time pacing |
| `python test_link.py [delay] [model]` | OBS link protocol, with a Python stand-in for the plugin |
| `python test_file_cleaner.py [model]` | builds a test video, cleans it, verifies video untouched and words muted |
| `python test_maintenance.py` | storage report and removal (models, GPU runtime, OBS plugin, everything) on a temp folder |
| `python test_wordlists.py` | matching rules, built-in list, file import (incl. old Windows-1250 files), language download, allow list |
| `python test_censor_sound.py [model]` | censor sound restart/loop logic, custom sound file in a cleaned recording |
| `python test_obs_plugin.py [delay] [model] [silence\|beep\|app]` | the real plugin inside portable OBS (`tools/obs-33`), driven over obs-websocket, checked in OBS's own recording |

`test_obs_plugin.py` launches the portable OBS in `tools/obs-33` (your installed OBS, if any, isn't
touched). OBS writes a shader cache to `C:\ProgramData\obs-studio\shader-cache` even in portable mode.

## Limitations

- Other languages work with the multilingual models and per-language word lists, but they've only
  been tested on English speech so far. The language word lists are community lists of varying size
  and quality; add your own words for slang.
- Mumbled, very fast or accented speech, and voices buried under loud game audio, can slip through.
- Streamlabs Desktop mostly doesn't run OBS plugins, so Streamlabs users need the VB-CABLE mode.
- VB-CABLE is a driver, so it needs administrator rights and a reboot.

## About VB-CABLE

VB-CABLE is made by [VB-Audio](https://vb-audio.com/Cable/), not by Voice Guard, and you download it
from their site. It's donationware: free for personal use with every feature, and VB-Audio asks for a
paid license when it's used professionally (in a company, institution or organization). If it's
useful to you, please consider supporting them.
