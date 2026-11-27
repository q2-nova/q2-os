/* Q2 wheel (drivers/input/misc/shanling-q2-wheel.c) over a generated I2C bus.
 *
 * Position and ID reads are two STOP-terminated messages under one bus
 * segment lock, retried once as a whole; errors and positions above 200
 * cancel the gesture; normal mode sends 00, waits 1 to 2 ms, then ff 00.
 */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define I2C_M_RD 1
#define I2C_LOCK_SEGMENT 1
#define ABS_X 0
#define ABS_PRESSURE 24
#define BTN_TOUCH 330
#define dev_warn_ratelimited(...) (warnings++)
struct device { int unused; };
struct gpio_desc;
struct i2c_adapter { int unused; };
struct i2c_client { unsigned short addr; struct i2c_adapter *adapter; struct device dev; };
struct i2c_msg { unsigned short addr, flags, len; u8 *buf; };
struct input_dev { int unused; };
static unsigned locked, lock_calls, unlock_calls, calls, normal_calls, sleeps, warnings;
static int results[8], result_count;
static unsigned expected_register;
static u8 byte_value;
static unsigned trace[8], position_events, pressure, touch, syncs;
static void i2c_lock_bus(struct i2c_adapter *a, int flag) { assert(flag == I2C_LOCK_SEGMENT && !locked); locked = 1; lock_calls++; }
static void i2c_unlock_bus(struct i2c_adapter *a, int flag) { assert(flag == I2C_LOCK_SEGMENT && locked); locked = 0; unlock_calls++; }
static int __i2c_transfer(struct i2c_adapter *a, struct i2c_msg *m, int count)
{
	assert(locked && count == 1 && m->addr == 0x15 && m->len == 1 && calls < (unsigned)result_count);
	assert(m->flags == 0 || m->flags == I2C_M_RD);
	trace[calls] = m->flags;
	if (!m->flags)
		assert(m->buf[0] == expected_register);
	int ret = results[calls++];
	if (m->flags == I2C_M_RD && ret == 1)
		m->buf[0] = byte_value;
	return ret;
}
static int i2c_master_send(const struct i2c_client *c, const char *data, int count)
{
	assert(c->addr == 0x15 && normal_calls < 2);
	if (!normal_calls)
		assert(count == 1 && data[0] == 0x00 && !sleeps);
	else
		assert(count == 2 && (u8)data[0] == 0xff && !data[1] && sleeps == 1);
	return results[normal_calls++];
}
static void usleep_range(unsigned long low, unsigned long high) { assert(low == 1000 && high == 2000); sleeps++; }
static void input_report_abs(struct input_dev *d, unsigned int code, int value)
{
	if (code == ABS_X) {
		assert(value >= 1 && value <= 200);
		position_events++;
	} else {
		assert(code == ABS_PRESSURE);
		pressure = value;
	}
}
static void input_report_key(struct input_dev *d, unsigned int code, int value) { assert(code == BTN_TOUCH); touch = value; }
static void input_sync(struct input_dev *d) { syncs++; }

/* PRODUCTION SOURCE */

static void setup(unsigned reg, u8 value, const int *returns, int count)
{
	locked = lock_calls = unlock_calls = calls = normal_calls = sleeps = warnings = position_events = syncs = 0;
	pressure = touch = 1;
	expected_register = reg;
	byte_value = value;
	result_count = count;
	memcpy(results, returns, count * sizeof(int));
}

int main(void)
{
	struct i2c_adapter adapter = {0};
	struct i2c_client client = {.addr = 0x15, .adapter = &adapter};
	struct input_dev input = {0};
	struct q2_wheel wheel = {.client = &client, .input = &input};
	const int good[] = {1, 1};
	/* Position, chip ID and project ID: one write, one read, one segment. */
	for (unsigned reg = 0; reg < 3; reg++) {
		unsigned address = reg == 0 ? 0x21 : reg == 1 ? 0xa7 : 0xa8;
		setup(address, reg ? 0 : 200, good, 2);
		assert(q2_wheel_read_reg(&wheel, address) == (reg ? 0 : 200));
		assert(calls == 2 && trace[0] == 0 && trace[1] == I2C_M_RD);
		assert(lock_calls == 1 && unlock_calls == 1 && !locked);
	}
	/* One complete retry after a short write or a failed read. */
	const int short_write_then_good[] = {0, 1, 1};
	setup(0x21, 42, short_write_then_good, 3);
	assert(q2_wheel_read_reg(&wheel, 0x21) == 42 && calls == 3 && trace[0] == 0 && trace[1] == 0 && trace[2] == 1);
	const int read_error_then_good[] = {1, -EIO, 1, 1};
	setup(0x21, 43, read_error_then_good, 4);
	assert(q2_wheel_read_reg(&wheel, 0x21) == 43 && calls == 4 && trace[0] == 0 && trace[1] == 1 && trace[2] == 0 && trace[3] == 1);
	/* Two failed attempts: the error, or -EIO for a short transfer. */
	const int reads_fail[] = {1, -EIO, 1, 0};
	setup(0x21, 99, reads_fail, 4);
	assert(q2_wheel_read_reg(&wheel, 0x21) == -EIO && calls == 4 && !locked);
	const int writes_fail[] = {-ENXIO, -ETIMEDOUT};
	setup(0x21, 99, writes_fail, 2);
	assert(q2_wheel_read_reg(&wheel, 0x21) == -ETIMEDOUT && calls == 2 && !locked && lock_calls == 1);
	/* Interrupt: report a position, a release at 0, cancel on 201 or an error. */
	setup(0x21, 200, good, 2);
	assert(q2_wheel_irq(0, &wheel) == IRQ_HANDLED);
	assert(position_events == 1 && pressure == 1 && touch == 1 && syncs == 1);
	setup(0x21, 0, good, 2);
	assert(q2_wheel_irq(0, &wheel) == IRQ_HANDLED);
	assert(!position_events && !pressure && !touch && syncs == 1 && !warnings);
	setup(0x21, 201, good, 2);
	assert(q2_wheel_irq(0, &wheel) == IRQ_HANDLED);
	assert(!position_events && !pressure && !touch && syncs == 1 && warnings == 1);
	setup(0x21, 99, writes_fail, 2);
	assert(q2_wheel_irq(0, &wheel) == IRQ_HANDLED);
	assert(!position_events && !pressure && !touch && syncs == 1 && warnings == 1);
	/* Normal mode: exact bytes and delay; a failed first send stops it. */
	const int normal_good[] = {1, 2};
	setup(0, 0, normal_good, 2);
	assert(!q2_wheel_normal_mode(&wheel) && normal_calls == 2 && sleeps == 1);
	const int normal_short[] = {0};
	setup(0, 0, normal_short, 1);
	assert(q2_wheel_normal_mode(&wheel) == -EIO && normal_calls == 1 && !sleeps);
	const int normal_error[] = {1, -ENXIO};
	setup(0, 0, normal_error, 2);
	assert(q2_wheel_normal_mode(&wheel) == -ENXIO && normal_calls == 2);
	puts("PASS: STOP-separated reads under one segment lock, one complete retry, error and range cancellation, normal-mode bytes and delay");
	return 0;
}
