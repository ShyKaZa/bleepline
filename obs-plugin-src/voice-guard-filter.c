// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (c) 2026 Gorgho
/*
 * Voice Guard OBS audio filter.
 *
 * Holds a source's audio back by a fixed delay and silences (or bleeps) the
 * sample ranges that the Voice Guard app reports as banned words. The app runs
 * the speech recognition; this filter only buffers audio, streams a mono copy
 * to the app over 127.0.0.1, and applies the mute ranges it sends back.
 *
 * Protocol (little-endian), see app/link_server.py:
 *   filter -> app  HELLO "VGL3" u32 rate u32 delay_ms u16 name_len name
 *   filter -> app  AUDIO u8 1 u64 end_index u32 n float32[n]
 *   app -> filter  MUTE  u8 2 u64 start_index u64 end_index
 *   app -> filter  SOUND u8 3 u32 n float32[n]   the app's censor sound (n = 0: silence)
 *   app -> filter  EARLY u8 4 u32 id u64 start_index u64 end_index   undoable "early" mute (same id = extend)
 *   app -> filter  UNDO  u8 5 u32 id                                 remove that early mute
 *
 * If the app isn't running the audio still passes through, delayed, so
 * starting or stopping Voice Guard never shifts audio/video sync.
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <obs-module.h>

OBS_DECLARE_MODULE()

#define VG_PORT 47811
#define VG_MAX_CHANNELS 8
#define VG_MSG_AUDIO 1
#define VG_MSG_MUTE 2
#define VG_MSG_SOUND 3
#define VG_MSG_EARLY 4
#define VG_MSG_UNDO 5
#define VG_MUTE_LEN 17
#define VG_EARLY_LEN 21
#define VG_UNDO_LEN 5
#define VG_MAX_EARLY 32
#define VG_MAX_SOUND (48000 * 30)
#define VG_OUTQ_CAP (1 << 20)

enum vg_mode { VG_MODE_APP, VG_MODE_SILENCE, VG_MODE_BEEP };
#define VG_RAMP_MS 5
#define VG_BEEP_HZ 1000.0
#define VG_BEEP_AMP 0.2f
#define VG_LOG(level, fmt, ...) blog(level, "[voice-guard] " fmt, ##__VA_ARGS__)

struct vg_filter {
	obs_source_t *context;
	uint32_t rate;
	size_t channels;
	int delay_ms;
	volatile LONG mode; /* enum vg_mode */

	/* Delay line, guarded by `lock`. Indices are absolute sample counts. */
	CRITICAL_SECTION lock;
	int64_t delay;
	int64_t size;
	float *buf[VG_MAX_CHANNELS];
	float *gain;
	int64_t written;
	int64_t mute_until;
	int64_t ramp;
	double beep_phase;
	float *mono;
	uint32_t mono_cap;
	float *sound; /* censor sound sent by the app; restarts at each mute, loops while it lasts */
	int64_t sound_len;
	int64_t sound_pos;
	bool sound_was_muted;
	struct {
		uint32_t id;
		int64_t start, end;
		bool used;
	} early[VG_MAX_EARLY]; /* undoable mutes, applied on top of the gain mask at playback */

	/* Link to the app. The socket belongs to the network thread only. */
	HANDLE thread;
	volatile LONG stop;
	volatile LONG connected;
	volatile LONG reset;
	CRITICAL_SECTION out_lock;
	uint8_t *outq;
	size_t out_len;
};

static float clamp01(double v)
{
	return v < 0.0 ? 0.0f : v > 1.0 ? 1.0f : (float)v;
}

/* --- delay line ----------------------------------------------------------- */

static void vg_free_buffers(struct vg_filter *f)
{
	for (size_t c = 0; c < VG_MAX_CHANNELS; c++) {
		free(f->buf[c]);
		f->buf[c] = NULL;
	}
	free(f->gain);
	f->gain = NULL;
}

/* Caller holds f->lock. */
static bool vg_alloc_buffers(struct vg_filter *f)
{
	vg_free_buffers(f);
	f->delay = (int64_t)f->rate * f->delay_ms / 1000;
	f->size = f->delay + (int64_t)f->rate * 3;
	for (size_t c = 0; c < f->channels; c++) {
		f->buf[c] = calloc((size_t)f->size, sizeof(float));
		if (!f->buf[c])
			return false;
	}
	f->gain = malloc((size_t)f->size * sizeof(float));
	if (!f->gain)
		return false;
	for (int64_t i = 0; i < f->size; i++)
		f->gain[i] = 1.0f;
	f->written = 0;
	f->mute_until = INT64_MIN / 2;
	return true;
}

static void vg_apply_mute(struct vg_filter *f, int64_t start, int64_t end)
{
	EnterCriticalSection(&f->lock);
	if (f->gain) {
		int64_t lo = start - f->ramp;
		int64_t read_pos = f->written - f->delay;
		int64_t oldest = f->written - f->size + 1;
		int64_t hi = end + f->ramp;
		if (lo < read_pos)
			lo = read_pos;
		if (lo < oldest)
			lo = oldest;
		if (hi > f->written)
			hi = f->written;
		for (int64_t p = lo; p < hi; p++) {
			float g_in = clamp01((double)(start - p) / f->ramp);
			float g_out = clamp01((double)(p - end) / f->ramp);
			float g = g_in > g_out ? g_in : g_out;
			int64_t idx = p % f->size;
			if (g < f->gain[idx])
				f->gain[idx] = g;
		}
		if (end > f->mute_until)
			f->mute_until = end; /* the part not captured yet is muted as it arrives */
	}
	LeaveCriticalSection(&f->lock);
}

static void vg_set_sound(struct vg_filter *f, const uint8_t *data, uint32_t n)
{
	float *copy = n ? malloc((size_t)n * sizeof(float)) : NULL;
	if (n && !copy)
		return;
	if (n)
		memcpy(copy, data, (size_t)n * sizeof(float)); /* memcpy: the bytes may be unaligned */
	EnterCriticalSection(&f->lock);
	free(f->sound);
	f->sound = copy;
	f->sound_len = n;
	f->sound_pos = 0;
	f->sound_was_muted = false;
	LeaveCriticalSection(&f->lock);
}

static void vg_set_early(struct vg_filter *f, uint32_t id, int64_t start, int64_t end)
{
	EnterCriticalSection(&f->lock);
	int slot = -1;
	for (int i = 0; i < VG_MAX_EARLY; i++) {
		if (f->early[i].used && f->early[i].id == id) {
			slot = i;
			break;
		}
		if (!f->early[i].used && slot < 0)
			slot = i;
	}
	if (slot >= 0) { /* if all 32 are busy the early mute is skipped; the normal mute still follows */
		f->early[slot].id = id;
		f->early[slot].start = start;
		f->early[slot].end = end;
		f->early[slot].used = true;
	}
	LeaveCriticalSection(&f->lock);
}

static void vg_undo_early(struct vg_filter *f, uint32_t id)
{
	EnterCriticalSection(&f->lock);
	for (int i = 0; i < VG_MAX_EARLY; i++)
		if (f->early[i].used && f->early[i].id == id)
			f->early[i].used = false;
	LeaveCriticalSection(&f->lock);
}

/* --- link to the app ------------------------------------------------------ */

struct vg_inbuf {
	uint8_t *data;
	size_t len;
	size_t cap;
};

static void vg_queue_audio(struct vg_filter *f, int64_t end_index, const float *mono, uint32_t n)
{
	size_t need = 1 + 8 + 4 + (size_t)n * 4;
	EnterCriticalSection(&f->out_lock);
	if (f->out_len + need <= VG_OUTQ_CAP) { /* if the app falls behind, drop rather than block OBS */
		uint8_t *p = f->outq + f->out_len;
		uint64_t idx = (uint64_t)end_index;
		p[0] = VG_MSG_AUDIO;
		memcpy(p + 1, &idx, 8);
		memcpy(p + 9, &n, 4);
		memcpy(p + 13, mono, (size_t)n * 4);
		f->out_len += need;
	}
	LeaveCriticalSection(&f->out_lock);
}

static SOCKET vg_connect(void)
{
	SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (s == INVALID_SOCKET)
		return s;
	u_long nonblocking = 1;
	ioctlsocket(s, FIONBIO, &nonblocking);
	BOOL nodelay = TRUE;
	setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *)&nodelay, sizeof(nodelay));

	struct sockaddr_in addr = {0};
	addr.sin_family = AF_INET;
	addr.sin_port = htons(VG_PORT);
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	connect(s, (struct sockaddr *)&addr, sizeof(addr));

	fd_set wr, ex;
	FD_ZERO(&wr);
	FD_ZERO(&ex);
	FD_SET(s, &wr);
	FD_SET(s, &ex);
	struct timeval tv = {0, 300000};
	if (select(0, NULL, &wr, &ex, &tv) != 1 || !FD_ISSET(s, &wr)) {
		closesocket(s);
		return INVALID_SOCKET;
	}
	return s;
}

static bool vg_send_all(SOCKET s, const uint8_t *data, size_t len)
{
	while (len > 0) {
		int sent = send(s, (const char *)data, (int)len, 0);
		if (sent == SOCKET_ERROR) {
			if (WSAGetLastError() != WSAEWOULDBLOCK)
				return false;
			Sleep(1);
			continue;
		}
		data += sent;
		len -= (size_t)sent;
	}
	return true;
}

static bool vg_send_hello(struct vg_filter *f, SOCKET s)
{
	obs_source_t *parent = obs_filter_get_parent(f->context);
	const char *name = parent ? obs_source_get_name(parent) : NULL;
	if (!name || !*name)
		name = "OBS source";
	uint16_t name_len = (uint16_t)strnlen(name, 200);
	uint32_t rate = f->rate, delay_ms = (uint32_t)f->delay_ms;

	uint8_t msg[4 + 4 + 4 + 2 + 200];
	memcpy(msg, "VGL3", 4);
	memcpy(msg + 4, &rate, 4);
	memcpy(msg + 8, &delay_ms, 4);
	memcpy(msg + 12, &name_len, 2);
	memcpy(msg + 14, name, name_len);
	return vg_send_all(s, msg, 14 + (size_t)name_len);
}

/* Handle every complete message in the buffer. False = malformed stream. */
static bool vg_parse(struct vg_filter *f, struct vg_inbuf *in)
{
	size_t pos = 0;
	while (in->len - pos >= 1) {
		size_t avail = in->len - pos;
		const uint8_t *p = in->data + pos;
		if (p[0] == VG_MSG_MUTE) {
			if (avail < VG_MUTE_LEN)
				break;
			uint64_t start, end;
			memcpy(&start, p + 1, 8);
			memcpy(&end, p + 9, 8);
			vg_apply_mute(f, (int64_t)start, (int64_t)end);
			pos += VG_MUTE_LEN;
		} else if (p[0] == VG_MSG_SOUND) {
			if (avail < 5)
				break;
			uint32_t n;
			memcpy(&n, p + 1, 4);
			if (n > VG_MAX_SOUND)
				return false;
			size_t need = 5 + (size_t)n * 4;
			if (avail < need)
				break;
			vg_set_sound(f, p + 5, n);
			pos += need;
		} else if (p[0] == VG_MSG_EARLY) {
			if (avail < VG_EARLY_LEN)
				break;
			uint32_t id;
			uint64_t start, end;
			memcpy(&id, p + 1, 4);
			memcpy(&start, p + 5, 8);
			memcpy(&end, p + 13, 8);
			vg_set_early(f, id, (int64_t)start, (int64_t)end);
			pos += VG_EARLY_LEN;
		} else if (p[0] == VG_MSG_UNDO) {
			if (avail < VG_UNDO_LEN)
				break;
			uint32_t id;
			memcpy(&id, p + 1, 4);
			vg_undo_early(f, id);
			pos += VG_UNDO_LEN;
		} else {
			return false;
		}
	}
	memmove(in->data, in->data + pos, in->len - pos);
	in->len -= pos;
	return true;
}

/* One select() round: flush queued audio, apply anything received. False = connection lost. */
static bool vg_pump(struct vg_filter *f, SOCKET s, struct vg_inbuf *in)
{
	EnterCriticalSection(&f->out_lock);
	bool pending = f->out_len > 0;
	LeaveCriticalSection(&f->out_lock);

	fd_set rd, wr;
	FD_ZERO(&rd);
	FD_ZERO(&wr);
	FD_SET(s, &rd);
	if (pending)
		FD_SET(s, &wr);
	struct timeval tv = {0, 10000};
	int ready = select(0, &rd, pending ? &wr : NULL, NULL, &tv);
	if (ready == SOCKET_ERROR)
		return false;

	if (FD_ISSET(s, &rd)) {
		if (in->cap - in->len < 65536) {
			size_t cap = in->cap * 2 + 65536;
			uint8_t *grown = realloc(in->data, cap);
			if (!grown)
				return false;
			in->data = grown;
			in->cap = cap;
		}
		int got = recv(s, (char *)in->data + in->len, (int)(in->cap - in->len), 0);
		if (got <= 0)
			return false;
		in->len += (size_t)got;
		if (!vg_parse(f, in))
			return false;
	}

	if (pending && FD_ISSET(s, &wr)) {
		EnterCriticalSection(&f->out_lock);
		int chunk = (int)(f->out_len < 65536 ? f->out_len : 65536);
		int sent = send(s, (const char *)f->outq, chunk, 0);
		if (sent > 0) {
			memmove(f->outq, f->outq + sent, f->out_len - (size_t)sent);
			f->out_len -= (size_t)sent;
		}
		LeaveCriticalSection(&f->out_lock);
		if (sent == SOCKET_ERROR && WSAGetLastError() != WSAEWOULDBLOCK)
			return false;
	}
	return true;
}

static DWORD WINAPI vg_net_thread(LPVOID param)
{
	struct vg_filter *f = param;
	struct vg_inbuf in = {0};
	SOCKET s = INVALID_SOCKET;
	bool was_connected = false;

	while (!f->stop) {
		if (s == INVALID_SOCKET) {
			InterlockedExchange(&f->reset, 0);
			s = vg_connect();
			if (s == INVALID_SOCKET) {
				for (int i = 0; i < 10 && !f->stop; i++)
					Sleep(100); /* app not running: retry every second */
				continue;
			}
			EnterCriticalSection(&f->out_lock);
			f->out_len = 0;
			LeaveCriticalSection(&f->out_lock);
			in.len = 0;
			if (!vg_send_hello(f, s)) {
				closesocket(s);
				s = INVALID_SOCKET;
				continue;
			}
			InterlockedExchange(&f->connected, 1);
			if (!was_connected)
				VG_LOG(LOG_INFO, "connected to the Voice Guard app");
			was_connected = true;
		}
		if (f->reset || !vg_pump(f, s, &in)) {
			InterlockedExchange(&f->connected, 0);
			closesocket(s);
			s = INVALID_SOCKET;
			if (!f->reset && was_connected) {
				VG_LOG(LOG_INFO, "lost the Voice Guard app; passing audio through (delayed)");
				was_connected = false;
			}
		}
	}
	if (s != INVALID_SOCKET)
		closesocket(s);
	free(in.data);
	return 0;
}

/* --- OBS source callbacks ------------------------------------------------- */

static const char *vg_get_name(void *type_data)
{
	UNUSED_PARAMETER(type_data);
	return "Voice Guard (profanity filter)";
}

static void vg_update(void *data, obs_data_t *settings)
{
	struct vg_filter *f = data;
	int delay_ms = (int)obs_data_get_int(settings, "delay_ms");
	const char *mode = obs_data_get_string(settings, "mode");
	LONG m = VG_MODE_APP;
	if (mode && strcmp(mode, "beep") == 0)
		m = VG_MODE_BEEP;
	else if (mode && strcmp(mode, "silence") == 0)
		m = VG_MODE_SILENCE;
	InterlockedExchange(&f->mode, m);
	if (delay_ms < 100)
		delay_ms = 100;
	if (delay_ms == f->delay_ms && f->gain)
		return;

	EnterCriticalSection(&f->lock);
	f->delay_ms = delay_ms;
	if (!vg_alloc_buffers(f)) {
		vg_free_buffers(f);
		VG_LOG(LOG_ERROR, "out of memory allocating the delay buffer");
	}
	LeaveCriticalSection(&f->lock);
	InterlockedExchange(&f->reset, 1); /* sample indices restarted: reconnect so the app starts fresh */
}

static void *vg_create(obs_data_t *settings, obs_source_t *source)
{
	struct vg_filter *f = calloc(1, sizeof(*f));
	if (!f)
		return NULL;
	f->context = source;
	audio_t *audio = obs_get_audio();
	f->rate = audio_output_get_sample_rate(audio);
	f->channels = audio_output_get_channels(audio);
	if (f->channels == 0)
		f->channels = 2;
	if (f->channels > VG_MAX_CHANNELS)
		f->channels = VG_MAX_CHANNELS;
	f->ramp = (int64_t)f->rate * VG_RAMP_MS / 1000;
	if (f->ramp < 1)
		f->ramp = 1;
	f->outq = malloc(VG_OUTQ_CAP);
	InitializeCriticalSection(&f->lock);
	InitializeCriticalSection(&f->out_lock);
	vg_update(f, settings);
	f->thread = CreateThread(NULL, 0, vg_net_thread, f, 0, NULL);
	return f;
}

static void vg_destroy(void *data)
{
	struct vg_filter *f = data;
	InterlockedExchange(&f->stop, 1);
	if (f->thread) {
		WaitForSingleObject(f->thread, 3000);
		CloseHandle(f->thread);
	}
	vg_free_buffers(f);
	free(f->mono);
	free(f->sound);
	free(f->outq);
	DeleteCriticalSection(&f->lock);
	DeleteCriticalSection(&f->out_lock);
	free(f);
}

static struct obs_audio_data *vg_filter_audio(void *data, struct obs_audio_data *audio)
{
	struct vg_filter *f = data;
	uint32_t n = audio->frames;
	if (n == 0)
		return audio;

	EnterCriticalSection(&f->lock);
	if (!f->gain || !f->outq) {
		LeaveCriticalSection(&f->lock);
		return audio;
	}
	if (f->mono_cap < n) {
		float *grown = realloc(f->mono, (size_t)n * sizeof(float));
		if (!grown) {
			LeaveCriticalSection(&f->lock);
			return audio;
		}
		f->mono = grown;
		f->mono_cap = n;
	}

	for (uint32_t i = 0; i < n; i++) {
		int64_t p = f->written + i;
		int64_t idx = p % f->size;
		float sum = 0.0f;
		for (size_t c = 0; c < f->channels; c++) {
			const float *src = (const float *)audio->data[c];
			float v = src ? src[i] : 0.0f;
			f->buf[c][idx] = v;
			sum += v;
		}
		f->mono[i] = sum / (float)f->channels;
		f->gain[idx] = clamp01((double)(p - f->mute_until) / f->ramp);
	}

	/* Early mutes overlapping this block; drop the ones already played past. */
	int active[VG_MAX_EARLY], n_active = 0;
	int64_t block_start = f->written - f->delay, block_end = block_start + n;
	for (int k = 0; k < VG_MAX_EARLY; k++) {
		if (!f->early[k].used)
			continue;
		if (f->early[k].end + f->ramp < block_start)
			f->early[k].used = false;
		else if (f->early[k].start - f->ramp < block_end)
			active[n_active++] = k;
	}

	LONG mode = f->mode;
	for (uint32_t i = 0; i < n; i++) {
		int64_t rp = f->written + i - f->delay;
		float g = 1.0f, tone = 0.0f;
		int64_t ridx = 0;
		if (rp >= 0) {
			ridx = rp % f->size;
			g = f->gain[ridx];
		}
		for (int a = 0; a < n_active; a++) {
			int k = active[a];
			float g_in = clamp01((double)(f->early[k].start - rp) / f->ramp);
			float g_out = clamp01((double)(rp - f->early[k].end) / f->ramp);
			float ge = g_in > g_out ? g_in : g_out;
			if (ge < g)
				g = ge;
		}
		bool muted = g < 0.999f;
		if (mode == VG_MODE_BEEP && muted) {
			tone = VG_BEEP_AMP * (float)sin(2.0 * M_PI * VG_BEEP_HZ * f->beep_phase / f->rate) * (1.0f - g);
		} else if (mode == VG_MODE_APP && muted && f->sound_len > 0) {
			if (!f->sound_was_muted)
				f->sound_pos = 0; /* each muted word starts the sound from the top */
			tone = f->sound[f->sound_pos % f->sound_len] * (1.0f - g);
			f->sound_pos++;
		}
		f->sound_was_muted = muted;
		f->beep_phase += 1.0;
		for (size_t c = 0; c < f->channels; c++) {
			float *dst = (float *)audio->data[c];
			if (dst)
				dst[i] = rp >= 0 ? f->buf[c][ridx] * g + tone : 0.0f;
		}
	}
	f->written += n;
	int64_t end = f->written;
	LeaveCriticalSection(&f->lock);

	if (f->connected)
		vg_queue_audio(f, end, f->mono, n);
	return audio;
}

static void vg_defaults(obs_data_t *settings)
{
	obs_data_set_default_int(settings, "delay_ms", 500);
	obs_data_set_default_string(settings, "mode", "app");
}

static obs_properties_t *vg_properties(void *data)
{
	UNUSED_PARAMETER(data);
	obs_properties_t *props = obs_properties_create();
	obs_property_t *delay = obs_properties_add_int_slider(props, "delay_ms", "Delay", 300, 1500, 10);
	obs_property_int_set_suffix(delay, " ms");
	obs_property_t *mode = obs_properties_add_list(props, "mode", "When a word is caught", OBS_COMBO_TYPE_LIST,
						       OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(mode, "Use the Voice Guard app's setting", "app");
	obs_property_list_add_string(mode, "Silence", "silence");
	obs_property_list_add_string(mode, "Beep (1 kHz)", "beep");
	obs_properties_add_text(props, "info",
				"Keep the Voice Guard app open in \"OBS Studio\" mode. Add OBS's Render Delay filter "
				"to your video sources with the same delay so lip-sync stays right.",
				OBS_TEXT_INFO);
	return props;
}

static struct obs_source_info vg_filter_info = {
	.id = "voice_guard_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_SOURCE_AUDIO,
	.get_name = vg_get_name,
	.create = vg_create,
	.destroy = vg_destroy,
	.update = vg_update,
	.get_defaults = vg_defaults,
	.get_properties = vg_properties,
	.filter_audio = vg_filter_audio,
};

const char *obs_module_description(void)
{
	return "Voice Guard: real-time profanity filter for audio sources";
}

bool obs_module_load(void)
{
	WSADATA wsa;
	WSAStartup(MAKEWORD(2, 2), &wsa);
	obs_register_source(&vg_filter_info);
	VG_LOG(LOG_INFO, "loaded");
	return true;
}

void obs_module_unload(void)
{
	WSACleanup();
}
