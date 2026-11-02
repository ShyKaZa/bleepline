# Bleepline: full guide

Local, real-time profanity filter for streamers and creators. It catches banned words in game audio,
Discord, or any other app before they reach your stream or your upload. Speech recognition runs on
your own CPU (or GPU); nothing leaves your PC.

## Downloads

| File | What it is |
|---|---|
| `Bleepline-Setup.exe` (~67 MB) | Installer. Installs for your Windows user only (no admin), adds Start menu / desktop shortcuts and an entry in **Settings → Apps** with an uninstaller. Re-run a newer one to update: it closes Bleepline, updates it in place, and keeps your downloads and settings. |
| `Bleepline-portable-win64.zip` (~100 MB) | Same app, no install: unzip anywhere and run `Bleepline.exe`. |
| `Bleepline-OBS-plugin.zip` | Only the OBS filter, with manual install steps. The app can install it for you instead. |

The builds aren't code-signed yet, so Windows SmartScreen may say "Windows protected your PC" the
first time: click **More info → Run anyway**.

### What gets put on your PC, and removing it

| Thing | Where | How to remove |
|---|---|---|
| The app | `%LOCALAPPDATA%\Programs\Bleepline` (installer) or wherever you unzipped it | Settings → Apps → Bleepline → Uninstall, or Manage tab → Uninstall |
| Speech models, GPU runtime, settings | `%LOCALAPPDATA%\Bleepline` | Manage tab (per item, or "Remove everything"), or tick the box when uninstalling |
| OBS plugin | `C:\ProgramData\obs-studio\plugins\bleepline-filter` | Manage tab, or tick the box when uninstalling |
| VB-CABLE (optional) | Windows driver from VB-Audio | Its own installer: run `VBCABLE_Setup_x64.exe` as administrator → **Remove Driver** |

The **Manage** tab lists everything with its size. The uninstaller removes the app (only the files it
installed), its shortcuts and its Apps entry, and asks whether to remove the downloads and the OBS plugin
too. A silent uninstall keeps them.

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
   `C:\ProgramData\obs-studio\plugins\bleepline-filter\bin\64bit\`, where OBS 30–33 load it.
   Restart OBS.
2. In OBS, right-click an audio source (Desktop Audio, your mic, a Discord or game Application Audio
   Capture) → **Filters** → **+** → **Bleepline (profanity filter)**. Set the delay and
   silence/beep there (default 500 ms, silence).
3. Add OBS's built-in **Render Delay** filter to your webcam and game capture with the same delay so
   video stays in sync with the delayed audio. Render Delay goes up to 500 ms; for a longer delay, add a
   second Render Delay filter (500 + 300 = 800 ms). 800 ms catches noticeably more (see
   [How reliable is it?](#how-reliable-is-it)).
4. Keep Bleepline open in **OBS Studio** mode and press **Start filtering**. The Live tab shows
   which OBS sources are connected and every word that gets muted.

If Bleepline isn't running, the filter still passes audio through (delayed), so starting or
stopping the app never shifts your audio/video sync. It reconnects by itself within a second.

### Streamlabs, XSplit, Twitch Studio or any other app

1. Install VB-CABLE (Setup tab → **Get VB-CABLE**, run the installer as administrator, reboot).
2. In Bleepline pick **Streamlabs, XSplit, ...** and choose the device you listen on (your headset).
   Bleepline records what plays there and sends a cleaned, delayed copy into **CABLE Input**.
3. In your streaming app, turn off its own desktop/system audio and add **CABLE Output (VB-Audio
   Virtual Cable)** as an audio input instead. Delay your video by the same amount if the app can.

### Clean a recording

Pick a file and press **Clean recording**. Bleepline analyses the whole file (all audio tracks)
with full sentence context, so it's more accurate than live filtering. It writes
`<name> (clean).<ext>` with the video stream copied byte-for-byte and only the audio re-encoded, plus
a `- muted words.txt` list with timestamps so you can check before uploading.

### Filter what I hear

Set Discord's *Output Device* to **CABLE Input (VB-Audio Virtual Cable)**. Bleepline plays the
cleaned call into your headset, and OBS's Desktop Audio picks it up. Don't capture Discord in OBS with
*Application Audio Capture*: that records the raw call.

## Customising

### Banned words tab
- **Built-in English lists**, made for voice chat (in `assets/wordlists/`):
  - **Strong swearing** (on): fuck, shit, bitch, cunt, asshole...
  - **Slurs** (on): racist, homophobic, transphobic and ableist slurs.
  - **Sexual words** (on): porn, blowjob, dildo...
  - **Mild words** (off): hell, damn, crap, ass, wtf, oh my god... Turn it on for a family-friendly stream.
  - **Strict text-chat list** (off): better_profanity's 916-word list, the default before 1.2. It also mutes
    everyday game talk like kill, sniper, god, stupid or drunk.
- **More lists:** import your own (`.txt` or `.csv`, one word or phrase per line) or download one per
  language (25 languages from the LDNOOBW project, CC BY 4.0). Each list can be switched off or removed.
- **Your words**, with simple syntax: `word` matches the exact word, `word*` anything starting with it
  (`fuck*` also catches fucker, fucking), and several words form a phrase. Case and accents are
  ignored (`kurva` = `Kurvá`). **Export to file** saves your list to share it.
- **Allow list:** words that are never muted, even if a list contains them (e.g. hell, damn).
- **Mute words written with \*:** the speech model sometimes writes swear words as `f**k` itself.
- **Test a word or phrase:** tells you whether it would be muted, which list it comes from, or which
  switched-off built-in list has it.
- Contractions are read as words ("he'll" is not "hell", "who're" is not "whore").

### Models tab
Download any of these, or add your own:

| Model | Language | Size | Best for |
|---|---|---|---|
| tiny.en | English | 75 MB | live filtering, lightest |
| base.en | English | 145 MB | live filtering (recommended) and recordings |
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
  Bleepline app's setting") and cleaned recordings.
- **Appearance:** dark (default), light, or same as Windows; switches instantly.
- **Startup:** start with Windows, optionally minimized. Combined with "Start filtering
  automatically" it's set-and-forget. Opening Bleepline while it's already running brings the open
  window to the front instead of starting a second copy.
- **Updates:** check GitHub once a day for a new version (the installer asks; the portable version starts
  with it off). A link appears at the top of the window when there is one. **About → Check for updates**
  checks right away. It's one request to GitHub's public release list; nothing about you is sent.

The **Delay** slider on the Live filter tab has a **Safer: 800 ms** button.

## Models and resource use

Normally Whisper pads every check to 30 seconds of audio. Bleepline feeds it only the live window
(3–4 s of context), which is about 36× less work for the same words. Idle engine threads sleep between
checks, and nothing runs at all while no one is speaking.

Live filtering runs on **GPU + CPU by default** (Options → Live filtering runs on). Writing the words
down takes one trip to the GPU per word piece, and next to a game the GPU switches between the two for
every one of them, which slows the game far more than the work itself. So the GPU only turns each
check's audio into features (about 4 ms) and the CPU writes the words. Without an NVIDIA GPU (or its
runtime from the Setup tab) everything runs on the CPU.

Measured with base.en in real time on held-out speech (16-thread CPU, RTX 3070 Ti), next to a stand-in
for a game that keeps the GPU at 100%; catches are of 101 swear words in 87 real recordings:

| Live filtering runs on | Per check | CPU (of one core) | The game loses | Fully muted at 500 / 600 ms |
|---|---|---|---|---|
| GPU + CPU (default) | 30 ms, checks every 50 ms | 80–90% | ~6% | 68 / 79 |
| CPU only | 66 ms, checks every 100 ms | ~100% | 0% | 52 / 70 |
| GPU only | 26 ms | ~25% | 31–34% | 69 / 77 (checks every 100 ms) |

GPU only is meant for a second PC that only streams. GPU + CPU's one core is about 6% of a 16-thread
processor. tiny.en on the CPU only needs about half of that but catches far fewer (45 against 79 of 101 at
600 ms). The old approach (padding to 30 s) needed ~90% GPU with a game running, and on CPU it took 375 ms
per check and caught nothing. Cleaning recordings uses the GPU when one is available, because it's a one-off batch
job; without one it uses half the CPU's threads. It decodes in batches (about twice as fast) and frees the
model, and with it the GPU memory, as soon as the file is done.

## How reliable is it?

A word can only be recognised once most of it has been spoken, so the delay has to be longer than
your longest banned word plus the time to check it.

The broadest measurement is on 87 real recordings with 101 swear words and 29 minutes of everyday talk,
none of it used to tune the filter (`research/keyword_spotter`, with the live loop's real timing): with the
default base.en on GPU + CPU, 51 of the 101 words are fully muted at 400 ms, 68 at 500 ms and 79 at 600 ms
(77%, 86% and 91% of their audio), and the everyday talk got 3 false mutes. Most of the wait is the word
itself: a median swear word lasts 310 ms.

`test_replay.py` uses 12 synthesized clips (6 phrases, 2 voices) with 24 swear words and look-alikes such
as "shift", "class" and "cocktail". It replays the live detector with its real timing, and a word only counts
as muted where the mute arrived before that audio was played. With tiny.en on the CPU, the live default
before GPU + CPU:

| Delay | Fully muted (≥95% of the word) | Swear-word audio muted |
|---|---|---|
| 400 ms | 8 of 24 | 68% |
| 500 ms | 12 of 24 | 78% |
| 600 ms (default) | 14 of 24 | 84% |
| 800 ms | 19 of 24 | 93% |
| 1000 ms | 20 of 24 | 94% |

Below 800 ms, what slips through is mostly the start of a word; the few words still missed at 1000 ms were
misheard by the speech model. base.en fully mutes a few more words here at 600 ms (17 of 24); on the real
recordings it catches twice as many as tiny.en (69 against 35 of 101 at 500 ms, both on the GPU), so it is
now the live default. Words right after a swear word can
be cut too ("Fuck, I am dead" loses "I am"), because a word still being spoken at the edge of a check is
muted eagerly: a wrong mute is cheap, a leak is not.

Other tests in this folder, with one phrase holding four swear words:

- The app itself in OBS mode, fed by the Python stand-in for the plugin (the phrase three times, real time),
  base.en on GPU + CPU at 500 ms: **12/12**. On GPU only, next to a stand-in game keeping the GPU at 100%:
  9/12, and the app said once that checks were slow.
- Live engine with real-time pacing (`integration_test.py`), base.en on GPU + CPU: 4/4 at 500 and 600 ms.
- OBS plugin in real OBS 33, recorded by OBS, tiny.en: **3/4** at 500 and 600 ms (also with the beep and
  with a custom sound sent from the app). The speech model mishears "shit" in "shit happens" in OBS's copy
  of the audio; the same phrase fed through the Python stand-in for the plugin (`test_link.py`) gets 4/4.
- Live engine with real-time pacing (`integration_test.py`), tiny.en at 600 ms: 4/4.
- Desktop-audio mode on real Windows audio devices, tiny.en: 24/24 words fully muted at 500 and 600 ms.
- Recording cleaner, base.en: 4/4 muted, video untouched, clean speech kept at 100%.

Every live catch is logged. If one says **caught late**, the start of the word may have been heard; if
that keeps happening, raise the delay. The note goes by the speech model's own timing of the word, so a
single one can be wrong: on the real recordings at 500 ms it was right in 29 of 35 notes and flagged 29 of
the 31 words whose start was heard. base.en sometimes dates a word that follows a pause too early ("shit"
in "Okay whatever, shit happens" by about half a second); the mute then starts early and covers the word,
but the log still calls it late. Real voices are messier than test speech, and loud game audio makes recognition
harder, so treat this as a strong safety net, not a guarantee.

## Running from source / building

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt
venv/Scripts/python main.py
```

From source, data lives in this folder; the packaged app keeps it in `%LOCALAPPDATA%\Bleepline`.

```bash
winget install JRSoftware.InnoSetup
venv/Scripts/pip install -r requirements-build.txt
venv/Scripts/python build_obs_plugin.py
venv/Scripts/python build.py
```

This writes the three downloads above to `release/`. `build.py app` builds only the app (`dist/Bleepline`)
and `build.py package` only the installer and zips from it; the release workflow signs the app's files in
between. The installer is made with [Inno Setup](https://jrsoftware.org/isinfo.php) from
`installer/bleepline.iss`. `requirements-build.txt` pins the exact versions release builds use. Models, the
GPU runtime and VB-CABLE are downloaded from the Setup tab on first run.

**Releases are built by GitHub**, not on a PC: bump `VERSION` in `app/version.py`, add
`docs/release-notes/v<version>.md`, commit, then push a matching tag (`git tag v1.0.1` and
`git push origin v1.0.1`). The [Release workflow](.github/workflows/release.yml) runs the quick tests,
builds everything on a Windows runner, self-tests the app, tests the installer (install, upgrading a
running copy, upgrading from 1.1, uninstall) and publishes the release with the three downloads. Code
signing switches on once SignPath is set up: see [SIGNING.md](SIGNING.md).

Command-line switches: `Bleepline.exe --selftest` checks an install and writes `selftest.log` to the
data folder. `Bleepline-Setup.exe /VERYSILENT [/DIR="path"] [/TASKS="desktopicon,checkupdates"]` installs
without a window; `unins000.exe /VERYSILENT` in the install folder uninstalls without one (keeping downloads).

### Building the OBS plugin

The plugin (`obs-plugin-src/bleepline-filter.c`) is plain C, built with Zig instead of Visual Studio,
against OBS 30 headers so it loads in OBS 30 and newer:

```bash
venv/Scripts/pip install ziglang==0.16.0
git clone --depth 1 --branch 30.0.2 --filter=blob:none --sparse https://github.com/obsproject/obs-studio.git tools/obs-src-30
git -C tools/obs-src-30 sparse-checkout set libobs
# unzip OBS-Studio-33.0.1-Windows-x64.zip (from the OBS GitHub release) into tools/obs-33
venv/Scripts/python build_obs_plugin.py
```

Only `obs.dll`'s export names are used, to build the import library. They're saved in
`obs-plugin-src/obs.def`, so without `tools/obs-33` the build uses that file and needs only the
headers. Run `build_obs_plugin.py` before `build.py` so the exe ships the plugin.

## Tests

| Command | What it checks |
|---|---|
| `python test_buffer.py` | delay buffer, mute ramps, future mutes, clock drift, input stalls |
| `python test_settings.py` | settings from older versions: the old live defaults move to the new ones once |
| `python test_engine.py [model]` | the live engine on CPU, GPU + CPU and GPU (whichever this PC has): same mutes with word timings only for banned words, same words found in each |
| `python integration_test.py [delay] [model] [split\|cpu\|gpu]` | live engine end to end with real-time pacing (default: base.en on GPU + CPU) |
| `python test_link.py [delay] [model]` | OBS link protocol, with a Python stand-in for the plugin |
| `python test_file_cleaner.py [model]` | builds a test video, cleans it, verifies video untouched and words muted |
| `python test_maintenance.py` | storage report and removal (models, GPU runtime, OBS plugin, everything) on a temp folder |
| `python test_wordlists.py` | matching rules, built-in lists (swears caught, everyday words and contractions left alone), file import (incl. old Windows-1250 files), language download, allow list |
| `python test_censor_sound.py [model]` | censor sound restart/loop logic, custom sound file in a cleaned recording |
| `python test_replay.py [model] [delays...]` | how much of 24 swear words in 12 clips the live detector mutes at each delay, with its real timing |
| `python test_obs_plugin.py [delay] [model] [silence\|beep\|app] [early] [trim]` | the real plugin inside portable OBS (`tools/obs-33`), driven over obs-websocket, checked in OBS's own recording; `trim` ends the clip right after its last word |
| `powershell -File installer\test_installer.ps1` | the installer: fresh install, upgrade with the app running, upgrade from 1.1, uninstall. It really installs, so the release workflow runs it on GitHub's machine; use a virtual machine locally |

`test_obs_plugin.py` launches the portable OBS in `tools/obs-33` (your installed OBS, if any, isn't
touched). OBS writes a shader cache to `C:\ProgramData\obs-studio\shader-cache` even in portable mode.

## Limitations

- Other languages work with the multilingual models and per-language word lists, but they've only
  been tested on English speech so far. The language word lists are community lists of varying size
  and quality; add your own words for slang.
- Mumbled, very fast or accented speech, and voices buried under loud game audio, can slip through.
- Streamlabs Desktop mostly doesn't run OBS plugins, so Streamlabs users need the VB-CABLE mode.
- OBS sources that play a clip and then go quiet (alerts, TTS donations, media files): the last part of
  each clip, as long as the delay, stays in the Bleepline filter until the source plays again, and then
  comes out just before the next clip. OBS only hands audio to a filter while its source is playing, so a
  filter can't push it out on time (`test_obs_plugin.py ... trim` shows it). Sources that always play,
  like Desktop Audio, a microphone or a game, aren't affected.
- VB-CABLE is a driver, so it needs administrator rights and a reboot.

## About VB-CABLE

VB-CABLE is made by [VB-Audio](https://vb-audio.com/Cable/), not by Bleepline, and you download it
from their site. It's donationware: free for personal use with every feature, and VB-Audio asks for a
paid license when it's used professionally (in a company, institution or organization). If it's
useful to you, please consider supporting them.
