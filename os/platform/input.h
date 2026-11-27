#ifndef Q2_INPUT_H
#define Q2_INPUT_H
#include <stdint.h>
struct q2_input_event { int64_t seconds; int32_t microseconds; uint16_t type, code; int32_t value; };
struct q2_input_state { int32_t position, touch; uint32_t keys; };
int q2_input_open(int kind, int monotonic);
int q2_input_state(int fd, int kind, struct q2_input_state *state);
int q2_input_read(int fd, struct q2_input_event *event);
unsigned q2_input_event_size(void);
unsigned q2_input_bridge_size(void);
int q2_clicker_open(void);
int q2_clicker_set(int fd, int on);
/* SND_TONE frequency in Hz; zero stops the waveform. */
int q2_clicker_tone(int fd, int hz);
/* Bit 0: 3.5 mm, bit 1: 4.4 mm; -1 when switch state is unavailable. */
int q2_headphones_state(int fd);
#endif
