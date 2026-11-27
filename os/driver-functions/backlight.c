/* Q2 backlight: the production DT's levels, pwm-backlight's duty calculation
 * (drivers/video/backlight/pwm_bl.c) and the X2000 PWM provider
 * (drivers/pwm/pwm-ingenic-x2000.c) over a generated register bank.
 *
 * Every app percentage has its own PWM level and requested duty. At full
 * duty the provider stays in PWM mode with one low clock per period: the
 * X2000 PWM gives no constant high from a zero low phase, so a full request
 * programmed literally turns the backlight dark (DISPLAY-003).
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef unsigned int spinlock_t;
#define __iomem
#define BIT(n) (1U << (n))
#define NSEC_PER_SEC 1000000000ULL
#define PWM_POLARITY_NORMAL 0
#define PWM_POLARITY_INVERSED 1
#define DIV_ROUND_UP_ULL(n, d) (((n) + (d) - 1) / (d))
#define do_div(n, d) ((n) /= (d))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define READ_ONCE(x) (x)
#define time_after(a, b) ((long)((b) - (a)) < 0)
#define spin_lock_irqsave(lock, flags) do { assert(!*(lock)); *(lock) = 1; (flags) = 0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { assert(*(lock)); *(lock) = 0; (void)(flags); } while (0)
#define dev_err(...) ((void)0)
struct clk;
struct device;
struct delayed_work { int scheduled; };
struct pwm_state { u64 period, duty_cycle; int polarity; bool enabled; };
struct pwm_chip { void *data; };
struct pwm_device { unsigned int hwpwm; struct pwm_state state; };
struct pwm_bl_data { unsigned int lth_brightness; unsigned int *levels; unsigned int scale; };
static unsigned long jiffies = 1000;
static unsigned writes, clk_enables, scheduled;
static int clk_error;
static u32 registers[0x400 / 4];
static unsigned long nsecs_to_jiffies(u64 ns) { return ns / 4000000; }
static int clk_enable(struct clk *clk) { clk_enables++; return clk_error; }
static bool schedule_delayed_work(struct delayed_work *work, unsigned long delay) { scheduled++; return true; }
static void *pwmchip_get_drvdata(struct pwm_chip *chip) { return chip->data; }
static u64 mul_u64_u32_div(u64 value, u32 mul, u32 div) { return (u64)((unsigned __int128)value * mul / div); }
static u32 readl(const volatile void __iomem *address) { return *(const volatile u32 *)address; }
static void writel(u32 value, volatile void __iomem *address);
#define readl_poll_timeout(addr, val, cond, delay_us, timeout_us) ((val) = readl(addr), (cond) ? 0 : -ETIMEDOUT)

/* PRODUCTION SOURCE */

static void writel(u32 value, volatile void __iomem *address)
{
	unsigned reg = (const volatile unsigned char *)address - (unsigned char *)registers;
	assert(reg < sizeof(registers) && !(reg & 3));
	writes++;
	if (reg == PWM_ENS)
		registers[PWM_EN / 4] |= value;
	else if (reg == PWM_ENC)
		registers[PWM_EN / 4] &= ~value;
	else if (reg == PWM_DEC)
		registers[PWM_DE / 4] &= ~value;
	registers[reg / 4] = value;
}

static void reset(void)
{
	for (unsigned i = 0; i < sizeof(registers) / sizeof(registers[0]); i++)
		registers[i] = 0xa5a5a5a5;
	registers[PWM_BUSY / 4] = 0;
	registers[PWM_EN / 4] = 0;
	registers[PWM_DFIFO_EMPTY / 4] = 0;
	writes = 0;
}

static bool two_phases(unsigned n)
{
	u32 wave = registers[PWM_WCFG(n) / 4];
	return (wave & 0xffff) && (wave >> 16);
}

static u32 nibble_cleared(u32 value, unsigned n) { return value & ~(0xfU << ((n % 8) * 4)); }

int main(void)
{
	/* The DT: one distinct level per percentage, from the lit minimum to 255. */
	enum { LEVELS = sizeof(dt_levels) / sizeof(dt_levels[0]) };
	assert(LEVELS == 101 && dt_levels[0] == 0 && dt_levels[1] == 16 && dt_levels[100] == 255);
	for (unsigned i = 1; i < LEVELS; i++)
		assert(dt_levels[i] > dt_levels[i - 1] && (i == 1 || dt_levels[i] - dt_levels[i - 1] <= 3));
	assert(dt_default == 0 && dt_channel == 0 && dt_period == 100000);

	/* pwm-backlight: every percentage requests its own duty, full at 100. */
	unsigned int levels[LEVELS];
	memcpy(levels, dt_levels, sizeof(levels));
	struct pwm_bl_data pb = {.levels = levels, .scale = levels[LEVELS - 1]};
	struct pwm_state request = {.period = dt_period};
	u64 duty[LEVELS];
	for (int i = 0; i < LEVELS; i++) {
		duty[i] = compute_duty_cycle(&pb, i, &request);
		assert(!i || duty[i] > duty[i - 1]);
	}
	assert(duty[0] == 0 && duty[100] == dt_period);

	/* The provider, for each requested duty, channel and two clock rates. */
	struct x2000_pwm provider;
	memset(&provider, 0, sizeof(provider));
	provider.base = registers;
	struct pwm_chip chip = {&provider};
	struct pwm_device pwm = {0};
	const unsigned long rates[] = {50000000, 93750000};
	unsigned checks = 0;
	for (unsigned r = 0; r < 2; r++) {
		provider.rate = rates[r];
		u64 ticks = dt_period * provider.rate / NSEC_PER_SEC;
		for (unsigned n = 0; n < 16; n++) {
			pwm.hwpwm = n;
			for (int i = 0; i < LEVELS; i++) {
				struct pwm_state state = {.period = dt_period, .duty_cycle = duty[i], .enabled = true};
				reset();
				u32 ccfg = registers[PWM_CCFG(n) / 4], dcr0 = registers[PWM_DCR0 / 4];
				u32 initr = registers[PWM_INITR / 4], oen = registers[PWM_OEN / 4], loop = registers[PWM_DCR1 / 4];
				assert(x2000_pwm_apply(&chip, &pwm, &state) == 0);
				assert(!provider.users && provider.clk_held && !provider.lock);
				if (i > 0)
					assert(two_phases(n));
				u32 wave = registers[PWM_WCFG(n) / 4];
				u64 high = duty[i] * provider.rate / NSEC_PER_SEC;
				if (high >= ticks)
					high = ticks - 1;
				assert((wave >> 16) == high && (wave >> 16) + (wave & 0xffff) == ticks);
				assert(registers[PWM_EN / 4] == BIT(n) && (registers[PWM_OEN / 4] & BIT(n)));
				/* Registers shared with the other channels keep their bits. */
				assert(registers[PWM_CCFG(n) / 4] == nibble_cleared(ccfg, n));
				assert(registers[PWM_DCR0 / 4] == (dcr0 & ~BIT(n)));
				assert(registers[PWM_INITR / 4] == (initr & ~(BIT(n) | BIT(n + 16))));
				assert(registers[PWM_OEN / 4] == (oen | BIT(n)) && registers[PWM_DCR1 / 4] == loop);
				assert(!!(provider.running & BIT(n)) == (i > 0));
				if (i == 100) {
					struct pwm_state actual = {0};
					assert((wave & 0xffff) == 1);
					assert(x2000_pwm_get_state(&chip, &pwm, &actual) == 0 && !provider.users);
					assert(actual.enabled && actual.period == dt_period);
					assert(actual.duty_cycle < actual.period && actual.duty_cycle > actual.period - 21);
				}
				checks++;
			}
			/* Disabled goes low and releases the clock's need. */
			struct pwm_state off = {.period = dt_period, .duty_cycle = dt_period, .enabled = false};
			assert(x2000_pwm_apply(&chip, &pwm, &off) == 0);
			assert(!(registers[PWM_EN / 4] & BIT(n)) && !(registers[PWM_INITR / 4] & (BIT(n) | BIT(n + 16))));
			assert(!(provider.running & BIT(n)) && !provider.users);
		}
	}

	/* Prescalers 0 to 14 at a 1 GHz clock keep one low clock at full duty. */
	pwm.hwpwm = 3;
	provider.rate = 1000000000;
	for (unsigned scale = 0; scale < 15; scale++) {
		u64 period = (40000ULL << scale) * NSEC_PER_SEC / provider.rate;
		struct pwm_state state = {.period = period, .duty_cycle = period, .enabled = true};
		reset();
		assert(x2000_pwm_apply(&chip, &pwm, &state) == 0 && two_phases(3));
		assert((registers[PWM_WCFG(3) / 4] & 0xffff) == 1);
		assert(((registers[PWM_CCFG(3) / 4] >> 12) & 0xf) == scale);
	}

	/* Refusals change no register and leave no clock user. */
	pwm.hwpwm = 0;
	provider.rate = 93750000;
	struct pwm_state bad = {.period = NSEC_PER_SEC + 1, .duty_cycle = 1, .enabled = true};
	reset();
	assert(x2000_pwm_apply(&chip, &pwm, &bad) == -ERANGE && !writes && !provider.users);
	bad = (struct pwm_state){.period = 10, .duty_cycle = 10, .enabled = true};
	reset();
	assert(x2000_pwm_apply(&chip, &pwm, &bad) == -ERANGE && !writes);
	bad.period = bad.duty_cycle = 11;   /* one clock: no room for a low phase */
	reset();
	assert(x2000_pwm_apply(&chip, &pwm, &bad) == -ERANGE && !writes);
	bad.period = bad.duty_cycle = 22;
	reset();
	assert(x2000_pwm_apply(&chip, &pwm, &bad) == 0 && two_phases(0));
	struct pwm_state full = {.period = dt_period, .duty_cycle = dt_period, .enabled = true};
	reset();
	registers[PWM_BUSY / 4] = 1;
	assert(x2000_pwm_apply(&chip, &pwm, &full) == -ETIMEDOUT && !writes && !provider.users);
	reset();
	provider.dma_started = 1;
	assert(x2000_pwm_apply(&chip, &pwm, &full) == -ETIMEDOUT && !writes && provider.dma_started == 1);
	registers[PWM_DFIFO_EMPTY / 4] = 1;
	assert(x2000_pwm_apply(&chip, &pwm, &full) == 0 && !provider.dma_started && two_phases(0));
	full.polarity = PWM_POLARITY_INVERSED;
	reset();
	unsigned enables = clk_enables;
	assert(x2000_pwm_apply(&chip, &pwm, &full) == -EINVAL && !writes && clk_enables == enables);
	full.polarity = PWM_POLARITY_NORMAL;
	provider.clk_held = false;
	clk_error = -EIO;
	reset();
	assert(x2000_pwm_apply(&chip, &pwm, &full) == -EIO && !writes && !provider.users);
	printf("PASS: %d DT levels, %u provider requests over 16 channels and two clocks, prescalers, disable and refusals\n",
	       LEVELS, checks);
	return 0;
}
