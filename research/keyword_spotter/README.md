# Keyword-spotter prototype (research, not in the app)

Idea: Whisper only names a word about 40 ms after it ends, so short delays let the start of swear words through.
A small detector reading the live engine's own Whisper encoder output at every check could fire while the word is
still being said, and mute it at once with the app's existing undoable "early" mutes (Whisper then confirms or
takes the mute back).

## How it works

1. `make_dataset.py`: sentences with and without swear words (plus look-alikes such as *shift*, *bitter*,
   *cocktail*) spoken by random speakers of the LibriTTS-R voice for [piper](https://github.com/OHF-Voice/piper1-gpl),
   timed with base.en word timestamps, cut into the same 1.2 s windows the live engine checks. Features: tiny.en
   encoder output at the window edge (1152 numbers). Speakers 0-699 train, 700-903 validate.
   `KWS_AUGMENT=1` adds speed/pitch shifts, tone colouring and room echo.
2. `train_kws.py`: a NumPy network (1152 → 32 → 1, dropout, input noise). `KWS_SKIP=2` ignores each swear word's
   first 80 ms, so it waits for the vowel ("shi-t" vs "shee-t").
3. `kws_replay.py`, `kws_probs.py`, `kws_false.py`: the detector inside the live loop on `test_replay.py`'s 12 clips
   (Windows voices, never used in training), scored like `test_replay.py`.

Setup (dev only; piper is GPL and only generates training audio):

```bash
venv/Scripts/python -m pip install --target research/keyword_spotter/piperlib --no-deps piper-tts==1.8.0 "pathvalidate>=3,<4"
# research/keyword_spotter/voice/: en_US-libritts_r-medium.onnx (+ .json) from huggingface.co/rhasspy/piper-voices
KWS_AUGMENT=1 venv/Scripts/python research/keyword_spotter/make_dataset.py data.npz 1600 400
KWS_SKIP=2 venv/Scripts/python research/keyword_spotter/train_kws.py data.npz kws.npz
venv/Scripts/python research/keyword_spotter/kws_replay.py <full path to kws.npz>
```

## Results (2026-11-01, tiny.en, ~3,000 synthetic sentences)

On held-out synthetic speakers it looks excellent: AUC 0.98, 418/429 swear words caught, first firing about 140 ms
into the word. It costs under 1 ms per check.

On the 12 Windows-voice test clips (24 swear words), with "Whisper veto" (no early mute when Whisper has already
written the word as something harmless that can't become a swear):

| | 300 ms | 400 ms | 500 ms | 600 ms | innocent words cut |
|---|---|---|---|---|---|
| Whisper only (today) | 1-2/24 | 8/24 | 11-12/24 | 14/24 | 16 |
| + detector, threshold 0.9 | 6/24 | 12/24 | 14/24 | 15/24 | 31-34 |
| + detector 0.9 + Whisper veto | 5/24 | 11/24 | 13/24 | 15/24 | 20-26 |

So it works in the right direction at short delays, but isn't good enough to ship:

- **It learned the training voice.** Only about 60% of the test clips' swear words trigger it confidently: synthetic
  speakers from one voice model don't cover other voices well, and real human voices differ even more.
- **Look-alike starts.** "she", "sheet", "shoot" sound like "shit" until the vowel, and Whisper hasn't written
  them yet when the detector fires, so the veto can't save them.

## Real voices (People's Speech, CC-BY)

Real recordings with swearing come from [People's Speech](https://huggingface.co/datasets/MLCommons/peoples_speech)
(MLCommons, CC-BY licensed "clean" part: archive.org talks and public meetings), without downloading the 450 MB
shards: `scan_text.py` reads only the transcript column over HTTP range requests (~0.3 MB per shard, needs
`pyarrow`, installed to a scratch folder with `pip install --target`), and `fetch_audio.py` downloads just the
matching clips through the Hugging Face dataset viewer, looking around each estimated row for the exact transcript
(rows per shard vary, so positions drift). `make_real_dataset.py` builds windows like `make_dataset.py`, with whole
recordings kept either for training or for testing (`split_real.py`), and `real_replay.py` scores held-out real
recordings.

About 1 clip in 1,500 swears (mostly mild: crap, damn, pissed, and people called Dick); the Hugging Face side
throttles, so scanning 268 of 804 shards took about 1.7 hours. First run: 100 clips downloaded, 70 usable
(50 for training from 35 recordings, 20 for testing from 18 other recordings, 15 swear words).

On the held-out real recordings:

| | 300 ms | 400 ms | 600 ms | swear audio muted at 300 / 400 ms | innocent words cut |
|---|---|---|---|---|---|
| Whisper only (today) | 2/15 | 5/15 | 7/15 | 34% / 49% | 3 |
| detector, synthetic voices only (0.9) | 3/15 | 6/15 | 8/15 | 63% / 78% | 55-60 |
| detector + 50 real clips (0.9) | 3/15 | 5/15 | 7/15 | 60% / 71% | 16-17 |
| detector + real clips + Whisper veto (0.9) | 2/15 | 5/15 | 7/15 | 42% / 57% | 8-9 |

- Real speech is much harder than synthetic for everything, today's Whisper path included.
- The synthetic-only detector falls apart on real voices; 50 real clips cut its false alarms by about 70%.
- It mutes clearly more of each swear word but rarely all of it, and still cuts innocent words: not shippable.
  It would need far more real examples (hundreds to thousands; a full People's Speech scan might give ~1,000
  clips, mostly mild meeting-room swearing rather than game chat).

## Full scan (2026-11-02)

`scan_all.py` reads every shard's transcripts (resumable: one cache file per shard in `shards/`, exact row numbers
from each shard's own footer) and `fetch_all.py` downloads the matching clips, named by clip id (use full paths:
some scripts switch to the repo folder):

```bash
venv/Scripts/python research/keyword_spotter/scan_all.py hits_all.json 10
venv/Scripts/python research/keyword_spotter/fetch_all.py hits_all.json clips_all/
venv/Scripts/python research/keyword_spotter/make_real_dataset.py clips_all/ hits_all.json real_all.npz
venv/Scripts/python research/keyword_spotter/split_real.py real_all.npz real_all_train.npz real_all_test.npz
KWS_SKIP=2 venv/Scripts/python research/keyword_spotter/train_kws.py data.npz data_aug.npz real_all_train.npz real_all_train.npz real_all_train.npz kws_all_x3.npz
venv/Scripts/python research/keyword_spotter/real_replay.py real_all.clips.json clips_all/ kws_mixed.npz kws_all_x3.npz
```

All 804 shards (1.5 million transcripts) took 2.8 hours with no throttling; 1,044 clips swear (1 in 1,440) and
1,040 downloaded in an hour (287 MB). 548 are usable (the timing model has to hear the swear word), from 427
recordings: 271 swearing clips for training (5× the first run), and 87 clips from other recordings for testing, with
101 swear words in 20.7 minutes of speech. The test words are mostly dick (30, mostly the name), shit 15, bitch 14,
crap 10, fucking 6, pissed 5, bullshit 5.

Fully muted swear words out of 101, on held-out real recordings:

| | 300 ms | 400 ms | 600 ms | swear audio muted at 300 / 400 ms | innocent words cut at 300 / 600 ms |
|---|---|---|---|---|---|
| Whisper only (today) | 9 | 25 | 45 | 35% / 52% | 31 / 35 |
| detector + 50 real clips (0.9) | 28 | 46 | 54 | 64% / 74% | 124 / 72 |
| detector + 271 real clips ×3 (0.9) | 28 | 45 | 55 | 63% / 73% | 89 / 57 |
| detector + 271 real clips ×6 (0.9) | 34 | 49 | 58 | 65% / 74% | 112 / 67 |
| detector + 271 real clips ×6 (0.8) | 41 | 55 | 62 | 71% / 79% | 160 / 85 |
| detector + 271 real clips ×3 (0.9) + Whisper veto | 15 | 32 | 48 | 47% / 62% | 58 / 41 |

(Innocent cuts drop at longer delays because Whisper's undo then arrives before playback.)

- With 5× more real data the detector catches as much as before with a quarter to a third fewer innocent cuts.
  The first 50 clips cut them by about 70%: the returns are shrinking.
- Validation loss is best after 5 of 25 epochs and then rises: the small network on Whisper encoder features
  memorises quickly. More of the same data alone won't make it shippable.
- At 300 ms it triples fully muted words (9 → 28) and nearly doubles the swear sound muted (35% → 63%), but adds
  about 3 innocent cuts per minute of speech.
- Whisper alone also scores lower on these recordings than on the synthetic clips (600 ms: 45/101 fully muted, 70%
  of the sound, against 14/24 there). Meeting audio is harder, and base.en reference timings on it are less
  precise, so take real-voice numbers as a lower bound.

Next idea: a dedicated spotter on its own sound features (like wake-word detectors), checking every 10-20 ms
instead of riding on Whisper's checks every 100 ms or more, with Whisper or a cheaper streaming recognizer confirming
or undoing its mutes and handling users' custom words.

For comparison, simply starting mutes 250 ms instead of 200 ms before the word (the "Mute before word" option)
gains 3 words at 600 and 800 ms on the synthetic clips without extra innocent cuts.

## Fast spotter + Whisper (2026-11-02)

Two layers: a small spotter on its own sound features checks every 20 ms and mutes (undoably) as soon as it fires;
Whisper keeps those mutes when it confirms a banned word there, mutes what the spotter missed, and the rest are taken
back 0.5 s after the spotter's last firing. Run from this folder (all data in `data/`, which is gitignored):

```bash
PY=../../venv/Scripts/python.exe
$PY fetch_negatives.py data/real/negatives.json data/real/negatives 1200   # everyday clips, no swearing
$PY make_synth_audio.py data/synthetic/synth.pkl 4000 400                  # piper sentences kept as audio
$PY make_spot_dataset.py data/real/real_all.clips.json data/real/clips_all data/real/negatives.json \
  data/real/negatives data/synthetic/synth.pkl data/spot_ds.pkl
bash run_spotter.sh spot_w64 64       # train_spotter.py, then spot_replay.py
SPOT_CHANNEL=1 SPOT_SELECT=real SPOT_SHARE=0.45,0.3,0.25 bash run_spotter.sh spot_ch 64
# Google speech embedding front end: data/gse/ = model.safetensors, LICENSE, NOTICE from
# huggingface.co/androiddrew/google-speech-embedding-pytorch (Apache-2.0)
$PY make_emb_dataset.py data/real/real_all.clips.json data/real/clips_all data/real/negatives.json \
  data/real/negatives data/synthetic/synth.pkl data/emb_ds.pkl
SPOT_SELECT=real $PY train_emb_spotter.py data/emb_ds.pkl data/emb_w64.npz 64
$PY spot_replay.py data/real/real_all.clips.json data/real/clips_all data/real/negatives.json \
  data/real/negatives data/real/whisper_cache data/emb_w64.npz
```

- `spotter.py`: the app-side part. Log-mel (40 bins, 10 ms hop) into a causal network of dilated convolutions
  that looks back 0.65 s (30k parameters, plain NumPy). `train_spotter.py` trains it in PyTorch (dev only,
  `torchlib/`) and exports it with batch norm folded in, checking that the NumPy copy matches.
- `speech_embedding.py`: Google's speech_embedding/1 (Lin et al. 2020, trained on ~200 million YouTube keyword
  clips), re-implemented from the PyTorch conversion. One 96-number embedding per 80 ms; four interleaved
  sequences give one every 20 ms. Plus a small causal head (`train_emb_spotter.py`, 44k parameters).
- Training data: real People's Speech swearing (267 swear words), 2.5 h of real everyday talk, and 3,354 piper
  sentences (2,208 swear words). Whole recordings stay in one split. Augmentation: level, babble, frequency masks;
  `SPOT_CHANNEL=1` adds microphone tilt, phone band limits, steady noise and dropouts.
- `spot_replay.py`: the test. 87 held-out swearing clips (101 swear words, as in the full scan) and 322 held-out
  everyday clips (76 minutes). Whisper's checks are recorded once per clip, with live-loop timing, into
  `data/real/whisper_cache/`. Record them with nothing else running; the cached run reproduces the full scan's
  Whisper-only numbers exactly.

Fully muted swear words out of 101, most sensitive setting of each (threshold 0.6, 2 firings in a row):

| | 200 ms | 300 ms | 400 ms | 600 ms | innocent words cut at 200 / 600 ms | false mutes per minute of everyday talk |
|---|---|---|---|---|---|---|
| Whisper only (today) | 0 | 9 | 25 | 45 | 31 / 35 | 0.10 |
| + log-mel spotter | 8 | 17 | 28 | 48 | 48 / 39 | 0.73 |
| + log-mel spotter, microphone simulation | 5 | 17 | 29 | 48 | 49 / 39 | 0.72 |
| + speech-embedding spotter | 4 | 12 | 27 | 46 | 49 / 46 | 0.93 |
| + speech-embedding, smaller head, more noise | 1 | 10 | 25 | 45 | 81 / 61 | 1.41 |

Stricter settings (0.8 or 0.9) fall back to about the Whisper-only numbers. The Whisper veto removes the spotter's
gain together with its false mutes. For comparison, the detector on Whisper's encoder features (full scan) fully
muted 28 / 45 / 55 at 300 / 400 / 600 ms, with 89 / 57 innocent cuts.

Why, from each spotter's highest probability during each swear word:

| swear words reaching 0.5 | log-mel | + microphone simulation | speech embedding |
|---|---|---|---|
| synthetic voices (validation) | 83% | | 74% |
| real voices, training | | | 80% |
| real voices, held out | 37% | 44% | 11% |

- Speed is not the problem. On synthetic voices the log-mel spotter first passes 0.5 a median 135 ms into the
  word (82% within 250 ms), which would be fast enough for 200 ms.
- Real voices are. 267 real swear words from meeting recordings are too few. The networks learn those speakers,
  not the words: the embedding head scores 80% on its training words and 11% on new voices. Microphone simulation
  lifts real-voice confidence a little (41% of words below 0.3 instead of 49%) but not the results. Google's
  embedding is made for whole keywords in 1-2 s clips and did worse here than plain log-mel.
- At short delays a wrong mute can't be taken back. At 200 ms the muted audio is played before Whisper can confirm
  or undo it, so every false firing is heard (0.7 per minute at the sensitive setting). Undo only saves audio at
  600 ms and more.
- CPU per 20 ms check, one thread, quiet machine: log-mel spotter 0.33 ms (1.7% of a core); speech embedding
  3.2 ms (16%) recomputing its 0.78 s window, or 0.49 ms (2.4%) if every layer's past outputs are kept.

### More real swearing: YODAS2

YODAS2 (`espnet/yodas2` on Hugging Face, CC-BY 3.0) is YouTube videos (gaming, vlogs, podcasts), closer to the
app's users than meeting recordings. Its audio only comes in ~1.4 GB archives (one per transcript file, ~80
videos). `scan_yodas.py data/yodas 10` reads 10 random transcript files from each of the 38 English folders
(`en000`-`en007` manual subtitles, `en100`-`en129` automatic captions):

- 4.28 million utterances, 4,213 with swearing (1 in 1,015). damn 1,543, crap 533, dick 396, shit 350,
  `[ __ ]` 345, fuck 206, pissed 131, fucking 125.
- `[ __ ]` is how YouTube's captions hide a swear word (in this sample all 345 are in `en000`-`en007`). The
  spotter only needs to know that some swear word is being said, not which one, so these still teach it (base.en
  hears and times the actual word, as with People's Speech).
- The best 10 archives of this sample hold 724 swearing utterances (72 each, ~14 GB of audio), the best 50 hold
  1,829 and the best 100 hold 2,651. A scan of every transcript file would find denser archives first.

That is 3 to 10 times the real swear words trained on so far, the one thing these results say is missing.

### Does more real data help? (2026-11-02)

Training now runs on an NVIDIA GPU when `torchlib_cuda/` exists (`pip install --target torchlib_cuda
"torch==2.15.0+cu134" --index-url https://download.pytorch.org/whl/cu134`, 3.2 GB). Batches are built on a
background thread while the GPU trains. One run takes 6.5 min with four sharing an RTX 3070 Ti, against 32 min on
the CPU. `SPOT_REAL_PART=k/n` trains on one of n shares of the real recordings, and `SPOT_SEED` repeats a run:

```bash
SPOT_REAL_PART=0/2 SPOT_SEED=2 bash run_spotter.sh g_h0_s2
$PY spot_diag.py g_h0_s2 > data/g_h0_s2.diag.txt   # confidence on held-out words, firing on everyday talk
$PY spot_roc.py g_q3 g_q2 g_q1 g_q0 g_h1_s1 g_h0_s1 g_all_s1 ...
```

A sensitive spotter catches more words and also fires more on everyday talk, so `spot_roc.py` compares runs at the
threshold where held-out everyday talk triggers a set number of firings (two 10 ms frames in a row) per minute.
"Early" means firing within 180 ms of the word's start, which a 200 ms delay needs:

| Real swear words trained on (runs) | Held-out words reaching 0.5 | Caught at 0.5 firings/min (early) | at 1 firing/min (early) |
|---|---|---|---|
| 35 (1) | 6% | 4% (0%) | 6% (0%) |
| 50 (1) | 19% | 6% (0%) | 13% (1%) |
| 81 (1) | 20% | 13% (3%) | 22% (6%) |
| 101 (1) | 27% | 15% (2%) | 20% (4%) |
| 116 (2) | 26%, 26% | 10%, 9% (0%, 0%) | 15%, 16% (1%, 0%) |
| 151 (2) | 42%, 37% | 25%, 22% (5%, 3%) | 31%, 27% (9%, 7%) |
| 267, all (3) | 45%, 48%, 40% | 18%, 19%, 18% (4%, 6%, 4%) | 30%, 30%, 28% (4%, 8%, 6%) |

- Real data helps up to about 150 swear words and then stops: all 267 do no better than the better half at the
  same false-alarm rate. Which recordings are in the training set matters about as much as how many.
- Firing early enough for 200 ms stays at 0-9% of real words at every size. In the first 180 ms of a word the
  spotter hears one syllable ("fu-", "shi-"), which real voices share with innocent words.
- So more of the same kind of data won't reach the 200 ms goal, and the YODAS download is not done. YODAS is
  closer to the app's users, so it could still help a little at 300-600 ms, but this curve gives no reason to
  expect much.

### Slurs (2026-11-02)

The spotter has never seen slurs: the piper sentences use swear words only, and real clips with listed words the
swear scan didn't look for were skipped. Slurs rely on Whisper alone. `slur_test.py` checks it: piper sentences
(held-out speakers) with each slur-list word, and the same templates with swear words, replayed through the live
detector with tiny.en (results in `data/slur_test.txt`):

| | Whole-clip tiny.en writes a listed word | Fully muted at 600 ms | at 1000 ms |
|---|---|---|---|
| Slurs (121 sentences) | 56% | 35% | 49% |
| Swear words (80 sentences) | 85% | 61% | 75% |

- 61 slur and 20 swear sentences were dropped because base.en heard a different number of words (so no reliable
  timing). Those are likely the harder ones, so the real slur gap is probably larger.
- Caught well: both n-word forms, faggot, retard, tranny. Rarely: dyke ("thanks", "dikes"), gook ("good"), fags
  ("tags"), pakis ("packies"), spics ("speaks"), raghead ("ragged"), kike ("kite").
- tiny.en also writes spellings the list misses ("nigas", "nigra", "spicks") and glues words together
  ("bronigas", "manspaz", "chinklet"), which a prefix list can't catch.
- In YODAS slurs are rare: 1.03 million transcript lines from 120 of the scanned files hold 147 with a slur-list
  word, 121 of them from one video. Elsewhere about 1 line in 40,000 (swearing: 1 in 1,015).

### Moonshine streaming instead of Whisper (2026-11-02)

`moon_replay.py` replays the held-out real recordings through Moonshine's streaming models (moonshine-voice 0.1.5,
MIT, installed with `pip install --target moonlib --no-deps moonshine-voice platformdirs google-crc32c`; English
models via `get_model_for_language(..., include_word_timestamps=True)` into `data/moonshine/`: tiny 45 MB, small
142 MB, medium 269 MB, plus the word-timing decoder). It uses the same mute rules as Whisper, alone or under the
spotter. First 25 swearing clips (25 swear words) and 25 everyday clips:

- Recognition is about the same as Whisper's. Moonshine small writes 22 of the 25 swear words in its final text;
  tiny.en's live checks mute near 23 of them.
- Its word timings are unusable on real speech. Whole sentences come back stamped at one moment (for example every
  word at 0.05-0.1 s), so its mutes miss the words: 0 of 25 fully muted at any delay, and 105-153 innocent words
  cut.
- It is much heavier here. On the same 60 s, nothing else running, tiny.en in the app's live loop takes 0.37 s of
  wall time and 0.56 s of CPU per second of audio. Moonshine tiny takes 0.31-0.63 s wall and 2.9-3.1 s CPU (its
  ONNX Runtime keeps many cores busy), small 0.49-0.81 s wall and 4.7-5.0 s CPU, updating every 0.5-0.1 s without
  word timings. Single updates take up to 0.4 s (tiny) and 0.8 s (small), longer than a 300-500 ms delay allows.

So Moonshine is not a better layer 2 for live muting on this PC; Whisper tiny.en stays.

### Whisper on the GPU (2026-11-02)

`SPOT_ASR_DEVICE=gpu` runs layer 2 on the GPU (RTX 3070 Ti, 8 GB, float16) in the same live loop, through
`lean_asr.py` (the app's FastTranscriber plus knobs, see below). Whisper only, full held-out set (87 swearing clips,
101 swear words; 323 everyday clips, 76.4 min):

| | 200 ms | 300 ms | 400 ms | 500 ms | 600 ms | innocent words cut | false mutes/min | check (median / 95%) | video memory |
|---|---|---|---|---|---|---|---|---|---|
| tiny.en, CPU (today) | 0 | 9 | 25 | - | 45 | 31-35 | 0.10 | 55 ms | - |
| tiny.en, GPU | 2 | 14 | 23 | 35 | 47 | 32-35 | 0.10 | 20 / 43 ms | +255 MB |
| **base.en, GPU** | 3 | **20** | **43** | **69** | **77** | **11-16** | **0.05-0.07** | 26 / 56 ms | +320 MB |
| small.en, GPU | 2 | 18 | 42 | 58 | 69 | 15-16 | 0.07-0.08 | 46 / 86 ms | up to +990 MB |

- A faster check alone doesn't help: tiny.en on the GPU checks about 3 times as fast and catches about the same.
  base.en does, because it writes a word down sooner after it ends (median 100 ms after its end, against 140 ms
  for tiny.en). small.en is better at nothing here and slower per check.
- The spotter adds little under base.en: at threshold 0.8 x2, 2 more words at 300-400 ms for 0.17 instead of 0.07
  false mutes/min.
- Early mute (the app's `early_mute`, simulated from the cached checks by the scratch `whisper_diag.py`) adds 2-4
  words at 300-500 ms and raises false mutes to 0.9-1.2/min. Not worth it.

Where the wait goes, per swear word base.en writes (cached checks): the word itself takes a median 310 ms (75%:
465 ms), the first check that writes it has a window ending a median 100 ms after the word (75%: 140 ms), and the
check takes 29 ms. So mute-from-word-start comes a median 437 ms after the word starts; 41% within 400 ms, 64%
within 500 ms. Most of the wait is the word itself, and a mute that waits for the whole word can't beat that. The
100 ms is about one check interval.

The reference timings are themselves base.en's (offline, beam 5), and some are stretched (a 1.3 s "crap", a 1.6 s
"fucking", a 1.8 s "bastardized"), which no short delay can fully cover. Several misses are "Dick" as a name in
council recordings. 94 of the 101 swear words get fully muted at some delay under 3 s.

Next to a game: `burn_gpu.py` keeps the GPU at 100% with half-precision matrix products. With it running, swearing
clips only, checks back to back (not paced as live):

| | check, GPU idle | check, GPU busy | fully muted at 500 ms, idle / busy | at 600 ms | burner's rate (alone ~317/s) |
|---|---|---|---|---|---|
| tiny.en | 20 ms | 63 ms | 35 / 31 | 47 / 44 | 182/s |
| small.en | 46 ms | 156 ms | 58 / 26 | 69 / 45 | 172/s |

A saturated GPU makes each check about 3 times slower, which costs catches. While checks run, the burner loses
about 43-46%: the GPU switches between the two processes rather than sharing. Live, checks run only part of the
time, and `paced_cost.py` measures that.

In real time (`paced_cost.py`, 120 s of held-out speech, each check waiting for its audio as in the app), alone
and next to `burn_gpu.py` (alone it manages 318 products/s):

| base.en unless noted | check, GPU idle | check, next to the game | CPU (of one core), idle | game loses |
|---|---|---|---|---|
| float16, 4 s input (the app's GPU mode) | 35 ms | 86 ms | 27% | 34% |
| + word timings only for banned words | 28 ms | 75 ms | 24% | 31% |
| + that, int8_float16 | 38 ms | 94 ms | 32% | 34% |
| + that, 2 s input | 31 ms | 82 ms | 26% | 32% |
| + that, checks every 50 ms | 27 ms | 71 ms (11 checks/s, not 20) | 47% | 34% |
| small.en, timings only for banned words | 46 ms | 134 ms (6 checks/s) | 46% | 36% |
| tiny.en, timings only for banned words | 24 ms | 57 ms | 18% | 23% |

- The game's loss barely depends on how much work a check does. Of a 28 ms base.en check, the encoder is 3 ms and
  the decoder 24 ms: one GPU round trip per token, each of which makes the GPU switch away from the game. Next to
  the game the decoder takes 62 ms.
- Word timings only for banned words saves 7 ms a check at no cost: only banned words need timings. int8_float16 and a
  2 s input don't help.

### GPU + CPU ("split") and CPU only (2026-11-02)

`SPOT_ASR_DEVICE=split` keeps the encoder on the GPU and decodes on a CPU copy of the model (int8, 2 threads), so a
check makes one GPU round trip instead of one per token. `cpu` runs it all on the CPU, as the app's CPU mode. Word
timings only for banned words throughout. In real time (`paced_cost.py`, 120 s), alone and next to `burn_gpu.py`
(318 products/s alone):

| base.en unless noted | check, idle | check, next to the game | CPU (of one core), idle / next to the game | game loses |
|---|---|---|---|---|
| split, checks every 100 ms | 31 ms | 34 ms | 41% / 51% | 3% |
| split, checks every 50 ms | 30 ms | 34 ms | 80% / 92% | 6% |
| all on the CPU | 66 ms | 67 ms | 101% / 106% | 0% |
| tiny.en, all on the CPU | 31 ms | 32 ms | 48% / 49% | 0% |

A split check is spectrogram 3 ms, encoder 4 ms (6 next to the game), decoder 23 ms on the CPU, word timings under
1 ms. The game barely slows the checks down any more, and they barely slow the game.

Catches (87 swearing clips, 101 swear words; the first 120 everyday clips, 29 min):

| | 200 ms | 300 ms | 400 ms | 500 ms | 600 ms | swear audio at 500 ms | innocent words cut at 500 ms | false mutes at 500 ms |
|---|---|---|---|---|---|---|---|---|
| base.en, GPU, 100 ms (above) | 3 | 20 | 43 | 69 | 77 | 84% | 15 | 2 in 29 min |
| split, 100 ms | 2 | 16 | 40 | 62 | 73 | 82% | 18 | 2 |
| **split, 50 ms** | 3 | **24** | **51** | **68** | **79** | **86%** | 25 | 3 |
| all on the CPU, 100 ms | 0 | 8 | 30 | 52 | 70 | 78% | 24 | 2 |

- The CPU's int8 decoder writes words a little later or less often than float16 on the GPU: at 100 ms, split
  catches 3-7 fewer. Checking every 50 ms more than makes up for it at 300-400 ms and ties at 500 ms, for about one
  CPU core and more innocent words cut in the swearing clips (the 15 all-GPU cuts plus words like "whining",
  "managing", "sends"). Everyday talk barely changes: 3 false mutes in 29 min against 2.
- All on the CPU costs the game nothing, but 66 ms checks cost about 100 ms of delay (70 at 600 ms against 68-69 at
  500 ms).
- Most misses at 500 ms, any setup: "dick" (7-11, many of them a name), "bitch" (5-8), "shit" (4-5), "crap" (3-7).
- The app now uses split with 50 ms checks by default ("GPU + CPU"), with all on the CPU (100 ms checks) and all on
  the GPU as options; `test_engine.py` in the repo root checks each placement.

### The "caught late" note (2026-11-02)

The app logs "caught N ms late" when audio past the model's start of the word had already been played when the
mute went out. With base.en on GPU + CPU, the scratch `late_*.py` scripts replayed it on the cached checks of the
87 swearing clips; right = the word was not fully muted:

| at 500 ms | notes | right | wrong | real leaks without a note |
|---|---|---|---|---|
| the first sighting's start (the app) | 35 | 29 | 6 | 2 |
| start no earlier than end - 300 / 400 ms | 22 / 35 | 17 / 29 | 5 / 6 | 14 / 2 |
| start moved past the last pause in the word's span (150 ms, -30 dB) | 30 | 25 | 5 | 6 |
| median start of the sightings over the next 800 ms | 32 | 28 | 4 | 3 |
| re-timed over 3 s once the word has finished | 33 | 28 | 5 | 3 |
| no note under 20 ms | 32 | 28 | 4 | 3 |

At 600 ms the app's note is right in 18 of 23 and misses 2 leaks; none of the others does better. tiny.en placed
starts a median 180 ms late, so its notes missed 35 of 57 leaks at 500 ms; base.en's starts are a median 0 ms off
(10%: -64 ms, 90%: +50 ms), 6 of 99 more than 200 ms early. Three of the six wrong notes are words 95.8-97.8% muted
(a little was heard) and two say "1 ms". The early ones come from the model leaving out a comma and stretching the
next word back over the pause; pauses inside real words ("fucking") make cutting at pauses miss real leaks. So the
note stays as it is, and the app now says to raise the delay if it keeps happening rather than after one note.

### Slurs with the 1.3 engine (2026-11-03)

`slur_test.py` with base.en on GPU + CPU and checks every 50 ms (the app's default since 1.3) in place of tiny.en:

| | Whole-clip base.en writes a listed word | Fully muted at 400 ms | 600 ms | 800 ms | 1000 ms |
|---|---|---|---|---|---|
| Slurs (126 sentences) | 55% | 26% | 48% | 58% | 65% |
| Swear words (78 sentences) | 85% | 55% | 79% | 90% | 90% |

1.2's engine fully muted 35% of slurs at 600 ms. The gap to swear words is recognition: the model writes the slur
at all in only about half the sentences. Three ways to close it were tried; one is in the app.

**Word hints (dropped).** The slurs, passed to the decoder as earlier text (`<|startofprev|>` plus the list, as
faster-whisper's `hotwords` do), make it write them far more often. Decoding 77 slur sentences whole, it wrote the
slur in 56 instead of 30 with the base forms (78 tokens), and the same with every form (108 tokens). On real speech
it fails (87 swearing clips with 101 swear words, 120 everyday clips, 29 min):

| | check (median) | fully muted at 400 / 500 / 600 ms | innocent words cut at 500 ms | false mutes/min at 500 ms |
|---|---|---|---|---|
| today | 28 ms | 51 / 68 / 79 | 25 | 0.10 |
| with the hint | 72 ms | 23 / 38 / 54 | 42 | 1.21 |

In quiet or unclear stretches the model copies the hint list into the transcript ("tard, tard."), so 38 of the
120 everyday clips got a banned word against 3. The longer prompt and output also nearly triple each check, which
costs catches. A shorter list would leak the same way.

**Early mute for slurs only (dropped).** The app's early mute, but tentative mutes only for words that start like
a slur, simulated on recorded checks (the scratch `whisper_diag.py` method). On 168 fresh slur sentences it fully
mutes 9 / 30 / 58 / 77 at 300 / 400 / 500 / 600 ms against 9 / 30 / 57 / 77 without it. Whisper doesn't write half
a slur at the window edge: it writes a whole everyday word. On real everyday talk (base.en GPU cache, 76.4 min)
false mutes go from 0.05-0.07 to 0.27-0.43 per minute (early mute for all words: 0.67-1.24).

**Spellings the model writes (in the app).** `slur_record.py` records the live checks on fresh sentences with every
check's words kept; `alias_eval.py` adds candidate spellings and rebuilds the mutes offline, on those sentences and
on real speech (base.en GPU cache: 87 swearing clips, 323 everyday clips, 76.4 min, every word kept), and looks
them up in the human transcripts of all 1,077 everyday clips:

    $PY slur_record.py data/slur_rec31.pkl 31
    $PY alias_eval.py data/slur_rec31.pkl packy=paki sandiger=sandnigger ...

Candidates came from the slur test and a first recorded set (seed 23): spellings written for a slur in more than
one sentence that never occur in real everyday talk. On a second, fresh set (seed 31, 176 slur sentences):

| | 400 ms | 500 ms | 600 ms | 800 ms | false mutes/min, everyday talk | innocent words cut, swearing clips |
|---|---|---|---|---|---|---|
| list before | 26 | 47 | 82 | 102 | 0.05 | 15 |
| + the spellings | 30 | 53 | 93 | 116 | 0.05 | 15 |

None of the spellings fires anywhere in the real speech or appears in the human transcripts. Added to `slurs.txt`:
nager, sandiger\*, sandigger\*, spicks, packy\*, packies, dike, dikes, tranies, tards ("dike" is also a flood
bank, so the list's header says so; spicks and tards are plain forms that were missing). The rest of the misses are
written as common words ("good" and "cook" for gook, "being" for beaner, "trainees" for trannies, "like" for
dykes), which no list can take. Only a model that has learned these words would write them.
