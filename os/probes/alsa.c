/* Generated PCM through the Buildroot libasound/time64 ABI. Emulator only. */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#define _TIME_BITS 64
#include <alsa/asoundlib.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define OK(call) do { int rc = (call); if (rc < 0) { \
    fprintf(stderr, "ALSA_FAIL %s: %s\n", #call, snd_strerror(rc)); exit(1); } } while (0)
static unsigned rate, width;
static snd_pcm_format_t format;
static snd_pcm_t *pcm;
static unsigned char samples[4096 * 8];

static void gate(const char *name)
{
    printf("ALSA_%s\n", name);
    assert(getchar() == '\n');
}
static void mixer(void)
{
    snd_ctl_t *ctl;
    snd_ctl_elem_value_t *value;
    snd_ctl_elem_info_t *info;
    OK(snd_ctl_open(&ctl, "hw:Q2", 0));
    OK(snd_ctl_elem_value_malloc(&value));
    OK(snd_ctl_elem_info_malloc(&info));
    for (unsigned i = 0; i < 4; i++) {
        char name[64];
        snprintf(name, sizeof(name), "DAC%u Master Playback Volume", i);
        snd_ctl_elem_info_clear(info);
        snd_ctl_elem_info_set_interface(info, SND_CTL_ELEM_IFACE_MIXER);
        snd_ctl_elem_info_set_name(info, name);
        OK(snd_ctl_elem_info(ctl, info));
        assert(snd_ctl_elem_info_get_type(info) == SND_CTL_ELEM_TYPE_INTEGER);
        assert(snd_ctl_elem_info_get_count(info) == 2);
        assert(snd_ctl_elem_info_get_min(info) == 0 && snd_ctl_elem_info_get_max(info) == 255);
        snd_ctl_elem_value_clear(value);
        snd_ctl_elem_value_set_interface(value, SND_CTL_ELEM_IFACE_MIXER);
        snd_ctl_elem_value_set_name(value, name);
        snd_ctl_elem_value_set_integer(value, 0, 195); /* -30 dB, generated fixture */
        snd_ctl_elem_value_set_integer(value, 1, 195);
        OK(snd_ctl_elem_write(ctl, value));
        snd_ctl_elem_value_set_integer(value, 0, 0);
        snd_ctl_elem_value_set_integer(value, 1, 0);
        OK(snd_ctl_elem_read(ctl, value));
        assert(snd_ctl_elem_value_get_integer(value, 0) == 195);
        assert(snd_ctl_elem_value_get_integer(value, 1) == 195);
    }
    snd_ctl_elem_value_free(value);
    snd_ctl_elem_info_free(info);
    OK(snd_ctl_close(ctl));
}
static void setup(void)
{
    snd_pcm_hw_params_t *hw;
    snd_pcm_sw_params_t *sw;
    OK(snd_pcm_open(&pcm, "hw:Q2,0", SND_PCM_STREAM_PLAYBACK, 0));
    OK(snd_pcm_hw_params_malloc(&hw));
    OK(snd_pcm_sw_params_malloc(&sw));
    OK(snd_pcm_hw_params_any(pcm, hw));
    assert(snd_pcm_hw_params_test_rate(pcm, hw, 32000, 0) < 0);
    assert(snd_pcm_hw_params_test_channels(pcm, hw, 1) < 0);
    OK(snd_pcm_hw_params_set_access(pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED));
    OK(snd_pcm_hw_params_set_format(pcm, hw, format));
    OK(snd_pcm_hw_params_set_channels(pcm, hw, 2));
    OK(snd_pcm_hw_params_set_rate(pcm, hw, rate, 0));
    OK(snd_pcm_hw_params_set_period_size(pcm, hw, 1024, 0));
    OK(snd_pcm_hw_params_set_buffer_size(pcm, hw, 4096));
    OK(snd_pcm_hw_params(pcm, hw));
    OK(snd_pcm_sw_params_current(pcm, sw));
    OK(snd_pcm_sw_params_set_avail_min(pcm, sw, 1024));
    OK(snd_pcm_sw_params_set_start_threshold(pcm, sw, 4096));
    OK(snd_pcm_sw_params_set_stop_threshold(pcm, sw, 4096));
    OK(snd_pcm_sw_params_set_tstamp_mode(pcm, sw, SND_PCM_TSTAMP_ENABLE));
    OK(snd_pcm_sw_params_set_tstamp_type(pcm, sw, SND_PCM_TSTAMP_TYPE_GETTIMEOFDAY));
    OK(snd_pcm_sw_params(pcm, sw));
    snd_pcm_hw_params_free(hw);
    snd_pcm_sw_params_free(sw);
    OK(snd_pcm_prepare(pcm));
}
static void write_frames(unsigned first, unsigned frames)
{
    for (unsigned i = 0; i < frames; i++) {
        for (unsigned ch = 0; ch < 2; ch++) {
            uint32_t v;
            if (rate == 44100) v = ((first + i) * (ch ? 319 : 173)) % 60001 - 30000;
            else if (rate == 48000) v = ((first + i) * (ch ? 81983 : 44461)) % 16000001 - 8000000;
            else v = (first + i) * (ch ? 22695477U : 1664525U) + 1013904223U;
            for (unsigned b = 0; b < width; b++) samples[(i * 2 + ch) * width + b] = v >> (b * 8);
        }
    }
    snd_pcm_sframes_t rc = snd_pcm_writei(pcm, samples, frames);
    if (rc != (snd_pcm_sframes_t)frames) {
        fprintf(stderr, "ALSA_FAIL write %u: %ld %s\n", frames, (long)rc, rc < 0 ? snd_strerror(rc) : "short");
        exit(1);
    }
}
int main(int argc, char **argv)
{
    _Static_assert(sizeof(time_t) == 8, "requires time64");
    setbuf(stdout, NULL);
    assert(argc == 2 && getenv("Q2_EMULATOR_TEST") && !strcmp(getenv("Q2_EMULATOR_TEST"), "1"));
    rate = strtoul(argv[1], NULL, 10);
    assert(rate == 44100 || rate == 48000 || rate == 96000);
    width = rate == 44100 ? 2 : 4;
    format = rate == 44100 ? SND_PCM_FORMAT_S16_LE : rate == 48000 ? SND_PCM_FORMAT_S24_LE : SND_PCM_FORMAT_S32_LE;
    struct timespec future = { .tv_sec = 2208988801LL };
    assert(clock_settime(CLOCK_REALTIME, &future) == 0);
    mixer();
    setup();
    write_frames(0, 4096);
    OK(snd_pcm_pause(pcm, 1));
    snd_pcm_status_t *status;
    OK(snd_pcm_status_malloc(&status));
    OK(snd_pcm_status(pcm, status));
    assert(snd_pcm_status_get_state(status) == SND_PCM_STATE_PAUSED);
    snd_pcm_sframes_t delay = snd_pcm_status_get_delay(status);
    /* Guard both sides: a library/header time ABI mismatch must not corrupt
       adjacent memory or pass because an uninitialized high word was zero. */
    struct { uint64_t before; snd_htimestamp_t ts; uint64_t after; } stamp;
    memset(&stamp, 0xa5, sizeof(stamp));
    snd_pcm_status_get_htstamp(status, &stamp.ts);
    assert(stamp.before == UINT64_C(0xa5a5a5a5a5a5a5a5) && stamp.after == stamp.before);
    assert(stamp.ts.tv_sec >= future.tv_sec && stamp.ts.tv_sec < future.tv_sec + 60);
    assert(stamp.ts.tv_nsec >= 0 && stamp.ts.tv_nsec < 1000000000);
    printf("ALSA_TIME64=%lld.%09ld\n", (long long)stamp.ts.tv_sec, stamp.ts.tv_nsec);
    gate("PAUSED");
    usleep(50000);
    OK(snd_pcm_status(pcm, status));
    assert(snd_pcm_status_get_state(status) == SND_PCM_STATE_PAUSED && snd_pcm_status_get_delay(status) == delay);
    OK(snd_pcm_pause(pcm, 0));
    for (unsigned first = 4096; first < rate / 2;) {
        unsigned n = rate / 2 - first;
        if (n > 1024) n = 1024;
        write_frames(first, n);
        first += n;
    }
    OK(snd_pcm_drain(pcm));
    assert(snd_pcm_state(pcm) == SND_PCM_STATE_SETUP);
    gate("DRAINED");
    OK(snd_pcm_prepare(pcm));
    write_frames(0, 4096);
    usleep(200000);
    assert(snd_pcm_state(pcm) == SND_PCM_STATE_XRUN);
    OK(snd_pcm_recover(pcm, -EPIPE, 1));
    write_frames(0, 4096);
    OK(snd_pcm_drain(pcm));
    OK(snd_pcm_hw_free(pcm));
    OK(snd_pcm_close(pcm));
    sleep(6);
    gate("CLOSED");
    setup();
    write_frames(0, 4096);
    OK(snd_pcm_pause(pcm, 1));
    gate("REOPENED");
    OK(snd_pcm_pause(pcm, 0));
    OK(snd_pcm_drain(pcm));
    OK(snd_pcm_close(pcm));
    snd_pcm_status_free(status);
    puts("ALSA_PASS");
    return 0;
}
