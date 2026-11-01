<p align="center">
  <img src="assets/bleepline-256.png" width="128" alt="Bleepline logo">
</p>

<h1 align="center">Bleepline</h1>

<p align="center">
  <b>Real-time profanity filter for streamers and creators. Free, open source, and 100% local.</b><br>
  Mutes or bleeps banned words in Discord, game voice chat or any app before they reach your stream or your upload.<br>
  <sub>Formerly called Voice Guard.</sub>
</p>

<p align="center">
  <a href="https://github.com/ShyKaZa/bleepline/releases/latest"><b>Download for Windows</b></a> ·
  <a href="SETUP.md">Full guide</a> ·
  <a href="#license-and-credits">License</a>
</p>

<p align="center">
  <img src="docs/live-dark.png" width="49%" alt="Live filter tab in dark mode">
  <img src="docs/options-dark.png" width="49%" alt="Options tab in dark mode">
</p>

## Why

Streaming platforms hand out strikes for words *other people* say: a teammate in voice chat, a friend
on Discord, a random player in game. Bleepline listens to that audio, recognises speech on your own
PC with OpenAI's Whisper, and silences banned words in the copy your viewers get, all within about
half a second.

## Features

- **Filters what you receive**, not just your mic: Discord, in-game voice chat, desktop audio.
- **OBS Studio plugin**: add the "Bleepline" filter to any audio source; no virtual cable needed.
- **Streamlabs, XSplit, Twitch Studio and others** via VB-CABLE.
- **Clean a recording** for YouTube: ShadowPlay, Game Bar or OBS recordings; the video is copied
  untouched and you get a list of every muted word with timestamps.
- **Light on your PC**: about 3-4% CPU with the default model; your GPU stays free for games.
- **500-600 ms delay** for live filtering, with optional *early muting* for shorter delays.
- **Silence, beep or your own sound** as the censor.
- **Your words, your rules**: built-in English lists made for voice chat (strong swearing, slurs and sexual
  words on; mild words like hell or damn optional), 25 language lists, wildcards (`word*`), phrases, an allow
  list, and import from text files. Everyday game talk like "kill", "sniper" or "god" is left alone.
- **Any Whisper model**: download from the app (tiny to large-v3-turbo) or add your own folder.
- **Dark and light themes**, a small installer, and an uninstaller that can remove everything it downloaded.
- **Private**: no account, no cloud, nothing you hear leaves your computer. The only network traffic is
  downloads you start and, if you leave it on, a once-a-day check for a new version on GitHub.

## Get started

1. Download **Bleepline-Setup.exe** from the [latest release](https://github.com/ShyKaZa/bleepline/releases/latest)
   and run it. It installs for your Windows user only, so no admin rights are needed.
   The app isn't code-signed yet, so Windows may say *"Windows protected your PC"*: click
   **More info → Run anyway**.
2. In the app, open **Setup** and click **Install everything** to download the speech model
   (and the OBS plugin if you use OBS).
3. On **Live filter**, pick where your audio goes and press **Start filtering**.

| You use | Pick | Extra needed |
|---|---|---|
| OBS Studio | OBS Studio | the OBS plugin (installed from the app) |
| Streamlabs, XSplit, Twitch Studio, ... | Streamlabs, XSplit, ... | [VB-CABLE](https://vb-audio.com/Cable/)* |
| Voice chat cleaned in your own headset | Filter what I hear | VB-CABLE* |
| A recording for YouTube | the Clean a recording tab | nothing |

\* [VB-CABLE](https://vb-audio.com/Cable/) is a separate virtual audio driver made by VB-Audio. It's
donationware: free for personal use, with a paid license for use in companies and organizations. If
it's useful to you, please consider [supporting VB-Audio](https://shop.vb-audio.com). The OBS mode
doesn't need it.

Step-by-step setup for each mode, all options, and measured results are in the **[full guide](SETUP.md)**.

<p align="center">
  <img src="docs/live-light.png" width="49%" alt="Live filter tab in light mode">
  <img src="docs/about-dark.png" width="49%" alt="About tab">
</p>

## How well does it work?

Measured on 12 synthesized clips with 24 swear words (`test_replay.py`, default tiny.en model on the CPU):

| Delay | Swear words fully muted | Swear-word audio muted |
|---|---|---|
| 500 ms | 12 of 24 | 78% |
| 600 ms (default) | 14 of 24 | 84% |
| 800 ms | 19 of 24 | 93% |

What slips through is usually just the first part of a word, because a word can only be recognised once
most of it has been said. A longer delay gives the filter more time, so if your setup allows it, 800 ms is
noticeably safer (the Live filter tab has a button for it). Real voices are messier still: mumbling, heavy
accents or loud game audio can let a word slip through, and words that sound alike can get muted. Treat it
as a strong safety net, **not a guarantee**. Every catch is logged so you can see what it did. More results
are in the [guide](SETUP.md#how-reliable-is-it).

English is the best tested. Other languages work with the multilingual models and per-language word
lists; picking the language instead of auto-detect helps a lot.

## Build from source

Windows, Python 3.12:

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt
venv/Scripts/python main.py
```

Building the installer (Inno Setup) and the OBS plugin is described in the
[full guide](SETUP.md#running-from-source--building).

## License and credits

Bleepline is made by **Gorgho** and released under the [MIT License](LICENSE). The OBS plugin in
`obs-plugin-src/` is GPL-2.0-or-later, because it builds on OBS Studio's libobs.

It stands on the shoulders of
[OpenAI Whisper](https://github.com/openai/whisper),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [CTranslate2](https://github.com/OpenNMT/CTranslate2)
(whose official build includes Intel oneMKL and oneDNN),
[Silero VAD](https://github.com/snakers4/silero-vad),
[better_profanity](https://github.com/snguyenthanh/better_profanity) (the optional strict word list),
the [LDNOOBW word lists](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) (CC BY 4.0),
[sounddevice](https://github.com/spatialaudio/python-sounddevice), [SoundCard](https://github.com/bastibe/SoundCard),
[PyAV](https://github.com/PyAV-Org/PyAV) / FFmpeg,
the [Sun Valley ttk theme](https://github.com/rdbende/Sun-Valley-ttk-theme)
and [OBS Studio](https://obsproject.com). Some modes use [VB-CABLE](https://vb-audio.com/Cable/) by
VB-Audio, which you download from their site. Full license texts of every bundled component are in
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

Bleepline is not affiliated with OBS, Discord, Twitch, YouTube or OpenAI. It is provided as is,
without warranty.

## Privacy

Bleepline does not collect or send anything about you or what you hear. It goes online only when you
ask it to (downloading speech models, the GPU runtime or word lists) and, if you leave the option on, to ask
GitHub once a day whether there's a newer version. The installer asks about that check, and it can be
switched off in Options.

## Code signing policy

The downloads aren't code-signed yet, so Windows may warn before running them. Bleepline is applying for
free open-source code signing from [SignPath Foundation](https://signpath.org) (see [SIGNING.md](SIGNING.md)).
Once that's in place, releases are built and signed only by this repository's GitHub workflow, and each
release is approved by the maintainer ([Gorgho](https://github.com/ShyKaZa)).

## Feedback

Found a word it misses, a bug, or want a feature? [Open an issue](https://github.com/ShyKaZa/bleepline/issues).
If Bleepline saves your stream, a star on the repo helps others find it.
