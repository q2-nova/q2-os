/* Q2 cell-charge control (drivers/power/supply/axp20x_battery.c) through the
 * kernel's regmap read, write and cache paths and the AXP2101 register
 * tables of drivers/mfd/axp20x.c, over a generated PMIC (POWER-001).
 *
 * charge_behaviour inhibits and restores cell charging through REG18 bit 1
 * and nothing else; `auto` is refused while the PMIC reports a charger or
 * temperature fault; every manual write revokes earlier policy authority;
 * charge_policy_control takes only the current epoch and generation; the
 * constant charge current takes only exact supported values. REG18 is
 * volatile, so a failed write or a PMIC reset is never hidden by the cache.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) (((1U << ((h) - (l) + 1)) - 1) << (l))
#define U64_MAX UINT64_MAX
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define REGCACHE_NONE 0
#define REGCACHE_MAPLE 3
#define BUG_ON(x) assert(!(x))
#define dev_info(...) ((void)0)
#define trace_regmap_reg_read(...) ((void)0)
#define trace_regmap_reg_write(...) ((void)0)
#define trace_regmap_reg_read_cache(...) ((void)0)
struct device;
struct iio_channel;
struct axp_data;
struct mutex { int held, locks; };
static void mutex_init(struct mutex *m) { m->held = 0; }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; m->locks++; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
static u64 get_random_u64(void) { return 0x5eed5eed5eed5eedULL; }
enum power_supply_property {
	POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT,
	POWER_SUPPLY_PROP_HEALTH, POWER_SUPPLY_PROP_STATUS,
};
enum {
	POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO, POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE,
	POWER_SUPPLY_CHARGE_BEHAVIOUR_FORCE_DISCHARGE,
};
enum {
	POWER_SUPPLY_HEALTH_UNKNOWN, POWER_SUPPLY_HEALTH_GOOD, POWER_SUPPLY_HEALTH_OVERVOLTAGE,
	POWER_SUPPLY_HEALTH_UNSPEC_FAILURE, POWER_SUPPLY_HEALTH_SAFETY_TIMER_EXPIRE,
};
union power_supply_propval { int intval; };
struct power_supply { void *data; };
static unsigned events;
static void *power_supply_get_drvdata(struct power_supply *psy) { return psy->data; }
static void power_supply_changed(struct power_supply *psy) { events++; }

struct regmap_access_table;
struct regmap;
struct regcache_ops {
	int (*read)(struct regmap *map, unsigned int reg, unsigned int *value);
	int (*write)(struct regmap *map, unsigned int reg, unsigned int value);
};
struct regmap {
	struct device *dev;
	bool bus, cache_bypass, cache_only, cache_dirty, defer_caching, force_write_field;
	void *read, *bus_context;
	struct { void *format_write; } format;
	bool (*volatile_reg)(struct device *dev, unsigned int reg);
	const struct regmap_access_table *volatile_table, *wr_table;
	int cache_type;
	const struct regcache_ops *cache_ops;
	unsigned int max_register;
	int (*reg_read)(void *context, unsigned int reg, unsigned int *val);
	int (*reg_write)(void *context, unsigned int reg, unsigned int val);
	int (*reg_update_bits)(void *context, unsigned int reg, unsigned int mask, unsigned int val);
};
static bool regmap_readable(struct regmap *map, unsigned int reg) { return reg <= map->max_register; }
static bool regmap_writeable(struct regmap *map, unsigned int reg);
static bool regmap_should_log(struct regmap *map) { return false; }
static unsigned int regmap_reg_addr(struct regmap *map, unsigned int reg) { return reg; }
static int regmap_read(struct regmap *map, unsigned int reg, unsigned int *value);
static int regmap_update_bits(struct regmap *map, unsigned int reg, unsigned int mask, unsigned int value);

/* PRODUCTION SOURCE */

/* The PMIC: register values, injected bus errors, and which register a step may write. */
static unsigned hw[256], pattern[256], cache[256];
static bool cached[256];
static int read_error[256], write_error;
static int allowed_write = -1;
static unsigned bus_writes;
static int bus_read(void *context, unsigned int reg, unsigned int *value)
{
	assert(reg < 256);
	if (read_error[reg])
		return read_error[reg];
	*value = hw[reg];
	return 0;
}
static int bus_write(void *context, unsigned int reg, unsigned int value)
{
	assert((int)reg == allowed_write && value < 256);
	bus_writes++;
	if (write_error)
		return write_error;
	hw[reg] = value;
	return 0;
}
static int cache_read(struct regmap *map, unsigned int reg, unsigned int *value)
{
	if (!cached[reg])
		return -ENOENT;
	*value = cache[reg];
	return 0;
}
static int cache_write(struct regmap *map, unsigned int reg, unsigned int value)
{
	cache[reg] = value;
	cached[reg] = true;
	return 0;
}
static bool regmap_writeable(struct regmap *map, unsigned int reg)
{
	return reg <= map->max_register && regmap_check_range_table(map, reg, map->wr_table);
}
static const struct regcache_ops maple = {cache_read, cache_write};
static struct regmap map;
static struct axp20x_batt_ps batt;
static struct power_supply psy = {&batt};

/* regmap_read and regmap_update_bits take the map lock around these. */
static int regmap_read(struct regmap *m, unsigned int reg, unsigned int *value) { return _regmap_read(m, reg, value); }
static int regmap_update_bits(struct regmap *m, unsigned int reg, unsigned int mask, unsigned int value)
{
	return _regmap_update_bits(m, reg, mask, value, NULL, false);
}

static void setup(unsigned module_en)
{
	for (unsigned r = 0; r < 256; r++)
		hw[r] = pattern[r] = (r * 37 + 19) & 255;
	hw[AXP2101_MODULE_EN] = pattern[AXP2101_MODULE_EN] = module_en;
	hw[AXP2101_IRQ0_STATE] = pattern[AXP2101_IRQ0_STATE] = 0;
	hw[AXP2101_IRQ2_STATE] = pattern[AXP2101_IRQ2_STATE] = 0;
	hw[AXP2101_PMU_STATUS_1] &= ~BIT(1);
	pattern[AXP2101_PMU_STATUS_1] = hw[AXP2101_PMU_STATUS_1];
	memset(cached, 0, sizeof(cached));
	memset(read_error, 0, sizeof(read_error));
	write_error = 0;
	allowed_write = AXP2101_MODULE_EN;
	bus_writes = events = 0;
	memset(&map, 0, sizeof(map));
	map.bus_context = &map;
	map.reg_read = bus_read;
	map.reg_write = bus_write;
	map.cache_type = REGCACHE_MAPLE;
	map.cache_ops = &maple;
	map.volatile_table = &axp2101_volatile_table;
	map.wr_table = &axp2101_writeable_table;
	map.max_register = AXP2101_TTF_L;
	memset(&batt, 0, sizeof(batt));
	batt.regmap = &map;
	batt.max_ccc = AXP2101_CC_CEILING_UA;
	mutex_init(&batt.fault_lock);
	axp2101_policy_init(&batt);
	axp2101_policy_ready(&batt);
	assert(batt.policy_valid);
}

static void others_unchanged(unsigned except)
{
	for (unsigned r = 0; r < 256; r++)
		if (r != except)
			assert(hw[r] == pattern[r]);
}

static int set(int psp, int value)
{
	union power_supply_propval v = {value};
	int ret = axp2101_battery_set_prop(&psy, psp, &v);
	assert(!batt.policy_lock.held && !batt.fault_lock.held);
	return ret;
}

static int behaviour(void)
{
	union power_supply_propval v = {-1};
	assert(!axp2101_get_charge_behaviour(&batt, &v));
	return v.intval;
}

static void check_behaviour(void)
{
	for (unsigned initial = 0; initial < 256; initial++)
		for (int choice = 0; choice < 2; choice++) {
			setup(initial);
			u64 generation = batt.policy_generation;
			unsigned expected = (initial & ~BIT(1)) | (choice == POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO ? BIT(1) : 0);
			assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, choice) == 0);
			assert(hw[AXP2101_MODULE_EN] == expected && events == 1);
			assert(batt.policy_generation == generation + 1 && batt.policy_valid);
			assert(behaviour() == choice);
			others_unchanged(AXP2101_MODULE_EN);
			/* The same value again is a manual write too. */
			assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, choice) == 0);
			assert(hw[AXP2101_MODULE_EN] == expected && events == 2);
			assert(batt.policy_generation == generation + 2);
		}
	for (int value = -4; value < 8; value++) {
		if (value == POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO || value == POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE)
			continue;
		setup(0x5a);
		u64 generation = batt.policy_generation;
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, value) == -EINVAL);
		assert(!bus_writes && !events && hw[AXP2101_MODULE_EN] == 0x5a);
		assert(batt.policy_generation == generation + 1);
	}
	setup(0x5a);
	u64 generation = batt.policy_generation;
	assert(set(POWER_SUPPLY_PROP_STATUS, 0) == -EINVAL && !bus_writes && batt.policy_generation == generation);
	assert(axp2101_battery_prop_writeable(&psy, POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR));
	assert(axp2101_battery_prop_writeable(&psy, POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT));
	assert(!axp2101_battery_prop_writeable(&psy, POWER_SUPPLY_PROP_HEALTH));
	assert(!axp2101_battery_prop_writeable(&psy, POWER_SUPPLY_PROP_STATUS));
}

static void check_faults(void)
{
	struct { unsigned reg, bit; bool sticky; } faults[] = {
		{AXP2101_IRQ0_STATE, 0, true}, {AXP2101_IRQ0_STATE, 1, true}, {AXP2101_IRQ0_STATE, 2, true},
		{AXP2101_IRQ0_STATE, 3, true}, {AXP2101_IRQ2_STATE, 0, true}, {AXP2101_IRQ2_STATE, 1, true},
		{AXP2101_IRQ2_STATE, 2, true}, {AXP2101_PMU_STATUS_1, 1, false},
	};
	for (unsigned f = 0; f < ARRAY_SIZE(faults); f++) {
		setup(0x00);
		hw[faults[f].reg] |= BIT(faults[f].bit);
		u64 generation = batt.policy_generation;
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) == -EPERM);
		assert(hw[AXP2101_MODULE_EN] == 0x00 && !bus_writes && !events);
		assert(batt.policy_generation == generation + 1);
		/* Inhibiting stays possible during a fault. */
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE) == 0);
		/* An event seen once is kept for this driver instance; the status bit is live. */
		hw[faults[f].reg] &= ~BIT(faults[f].bit);
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) ==
		       (faults[f].sticky ? -EPERM : 0));
		assert(!!(hw[AXP2101_MODULE_EN] & BIT(1)) == !faults[f].sticky);
	}
	/* Bits that are not charger or temperature events do not refuse auto. */
	setup(0x00);
	hw[AXP2101_IRQ0_STATE] = 0xf0;
	hw[AXP2101_IRQ2_STATE] = 0xf8;
	hw[AXP2101_PMU_STATUS_1] = 0xfd;
	assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) == 0);
	assert(hw[AXP2101_MODULE_EN] == BIT(1));
	/* An unreadable fault register refuses auto without a write. */
	unsigned fault_registers[] = {AXP2101_IRQ0_STATE, AXP2101_IRQ2_STATE, AXP2101_PMU_STATUS_1};
	for (unsigned i = 0; i < ARRAY_SIZE(fault_registers); i++) {
		setup(0x00);
		read_error[fault_registers[i]] = -EIO;
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) == -EIO);
		assert(!bus_writes && !events && hw[AXP2101_MODULE_EN] == 0x00);
	}
}

static void check_bus_faults(void)
{
	for (int choice = 0; choice < 2; choice++) {
		unsigned initial = choice == POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO ? 0xa5 & ~BIT(1) : 0xa5 | BIT(1);
		int other = !choice;
		setup(initial);
		read_error[AXP2101_MODULE_EN] = -EIO;
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, choice) == -EIO);
		assert(hw[AXP2101_MODULE_EN] == initial && !bus_writes && !events);
		read_error[AXP2101_MODULE_EN] = 0;
		write_error = -ENXIO;
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, choice) == -ENXIO);
		assert(hw[AXP2101_MODULE_EN] == initial && bus_writes == 1 && !events);
		write_error = 0;
		/* The readback is the PMIC's, not the value that failed to reach it. */
		assert(behaviour() == other);
		assert(set(POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, choice) == 0 && behaviour() == choice);
		/* A PMIC reset or another writer is seen at the next read. */
		hw[AXP2101_MODULE_EN] ^= BIT(1);
		assert(behaviour() == other);
	}
}

static int expected_selector(int ua)
{
	if (ua >= 25000 && ua <= 200000 && ua % 25000 == 0)
		return ua / 25000;
	if (ua > 200000 && ua <= 1000000 && (ua - 200000) % 100000 == 0)
		return (ua - 200000) / 100000 + 8;
	return -EINVAL;
}

static void check_current(void)
{
	for (int ua = -100000; ua <= 1100000; ua++)
		assert(axp2101_ccc_selector(ua) == expected_selector(ua));
	assert(axp2101_ccc_selector(INT32_MIN) == -EINVAL && axp2101_ccc_selector(INT32_MAX) == -EINVAL);
	for (int ua = 0; ua <= 1100000; ua += 5000) {
		setup(0x5a);
		allowed_write = AXP2101_ICC_CHG_SET;
		u64 generation = batt.policy_generation;
		int selector = expected_selector(ua);
		int ret = set(POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT, ua);
		assert(batt.policy_generation == generation + 1);
		if (selector < 0) {
			assert(ret == -EINVAL && !bus_writes && !events);
		} else {
			assert(ret == 0 && events == 1);
			assert(hw[AXP2101_ICC_CHG_SET] == ((pattern[AXP2101_ICC_CHG_SET] & ~AXP2101_ICC_MASK) | selector));
			assert(selector <= AXP2101_ICC_MAX_SEL);
		}
		others_unchanged(AXP2101_ICC_CHG_SET);
	}
	/* The probe's ceiling bounds requests the selector would accept. */
	setup(0x5a);
	allowed_write = AXP2101_ICC_CHG_SET;
	batt.max_ccc = 500000;
	assert(set(POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT, 600000) == -EINVAL && !bus_writes);
	assert(set(POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT, 500000) == 0 && bus_writes == 1);
}

static int token(u64 epoch, u64 generation, int psp, int value)
{
	union power_supply_propval v = {value};
	int ret = axp2101_policy_set(&psy, epoch, generation, psp, &v);
	assert(!batt.policy_lock.held);
	return ret;
}

static void check_policy(void)
{
	const int inhibit = POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE, automatic = POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO;
	const int behaviour_psp = POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR;
	setup(BIT(1));
	u64 epoch = batt.policy_epoch, generation = batt.policy_generation;
	assert(epoch && batt.policy_valid);
	assert(token(epoch, generation, behaviour_psp, inhibit) == 0);
	assert(batt.policy_generation == generation + 1 && !(hw[AXP2101_MODULE_EN] & BIT(1)) && events == 1);
	/* A used, older, newer or foreign token changes nothing. */
	u64 stale[][2] = {{epoch, generation}, {epoch, generation + 2}, {epoch + 1, generation + 1}, {0, generation + 1}};
	for (unsigned i = 0; i < ARRAY_SIZE(stale); i++) {
		assert(token(stale[i][0], stale[i][1], behaviour_psp, automatic) == -ESTALE);
		assert(batt.policy_generation == generation + 1 && !(hw[AXP2101_MODULE_EN] & BIT(1)) && events == 1);
	}
	assert(token(epoch, generation + 1, behaviour_psp, automatic) == 0 && (hw[AXP2101_MODULE_EN] & BIT(1)));
	/* A manual write revokes the token its holder had. */
	assert(set(behaviour_psp, inhibit) == 0);
	assert(token(epoch, generation + 2, behaviour_psp, automatic) == -ESTALE && !(hw[AXP2101_MODULE_EN] & BIT(1)));
	/* A refused control still spends the token. */
	hw[AXP2101_IRQ2_STATE] = BIT(0);
	assert(token(epoch, generation + 3, behaviour_psp, automatic) == -EPERM);
	assert(batt.policy_generation == generation + 4 && !(hw[AXP2101_MODULE_EN] & BIT(1)));
	/* No valid policy before probe completes. */
	setup(0);
	batt.policy_valid = false;
	assert(token(batt.policy_epoch, batt.policy_generation, behaviour_psp, automatic) == -EOVERFLOW && !bus_writes);
	/* The generation counter's end: policy stops, the manual control remains. */
	setup(0);
	batt.policy_generation = U64_MAX - 1;
	assert(set(behaviour_psp, automatic) == 0 && batt.policy_generation == U64_MAX && !batt.policy_valid);
	assert(token(batt.policy_epoch, U64_MAX, behaviour_psp, inhibit) == -EOVERFLOW && (hw[AXP2101_MODULE_EN] & BIT(1)));
	assert(set(behaviour_psp, inhibit) == 0 && !(hw[AXP2101_MODULE_EN] & BIT(1)) && batt.policy_generation == U64_MAX);
}

int main(void)
{
	assert(AXP2101_MODULE_EN == 0x18);
	check_behaviour();
	check_faults();
	check_bus_faults();
	check_current();
	check_policy();
	puts("PASS: 512 charge-enable transitions, fault refusals, bus errors and PMIC reset readback, charge current selectors, policy tokens");
	return 0;
}
