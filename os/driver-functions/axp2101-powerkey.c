/* Q2 power-key timing (drivers/mfd/axp20x.c, axp2101_q2_powerkey_init).
 *
 * On a Q2 with an AXP2101 the PMIC's IRQ_LEVEL_OFF_ON register gets the stock
 * timing 0x1d in bits 5:0 (power-on 512 ms, IRQ 1.5 s, power-off 10 s); bits
 * 7:6 and every other register keep their values. The readback that the log
 * reports comes from the PMIC, not the register cache.
 */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define GENMASK(h, l) (((1U << ((h) - (l) + 1)) - 1) << (l))
struct device;
struct regmap;
struct regmap_irq_chip_data;
struct regmap_config;
struct regmap_irq_chip;
struct mfd_cell;
enum axp20x_variants { AXP2101_ID = 8, AXP717_ID = 9 };
static unsigned registers[256], cache[256], updates, bypassed, cached, warnings, infos;
static bool q2 = true, wrong_read;
static int update_error, read_error;
static bool of_machine_is_compatible(const char *compat) { assert(!strcmp(compat, "shanling,q2")); return q2; }
#define dev_warn(...) (warnings++)
#define dev_info(...) (infos++)
static int regmap_update_bits(struct regmap *map, unsigned reg, unsigned mask, unsigned value)
{
	updates++;
	assert(reg < 256);
	if (update_error)
		return update_error;
	registers[reg] = (registers[reg] & ~mask) | (value & mask);
	cache[reg] = registers[reg];
	return 0;
}
static int regmap_read(struct regmap *map, unsigned reg, unsigned *value)
{
	cached++;
	*value = cache[reg];
	return 0;
}
static int regmap_read_bypassed(struct regmap *map, unsigned reg, unsigned *value)
{
	bypassed++;
	assert(reg < 256);
	if (read_error)
		return read_error;
	*value = wrong_read ? registers[reg] ^ 0x01 : registers[reg];
	return 0;
}

/* PRODUCTION SOURCE */

static void reset(void) { updates = bypassed = cached = warnings = infos = 0; }

int main(void)
{
	struct axp20x_dev axp = {.variant = AXP2101_ID};
	assert(AXP2101_IRQ_LEVEL_OFF_ON == 0x27);
	for (unsigned initial = 0; initial < 256; initial++) {
		for (unsigned reg = 0; reg < 256; reg++)
			registers[reg] = cache[reg] = (reg * 19 + 7) & 255;
		registers[0x27] = cache[0x27] = initial;
		unsigned before[256];
		memcpy(before, registers, sizeof(before));
		reset();
		axp2101_q2_powerkey_init(&axp);
		assert(updates == 1 && bypassed == 1 && !cached && !warnings && infos == 1);
		assert(registers[0x27] == ((initial & 0xc0) | 0x1d));
		for (unsigned reg = 0; reg < 256; reg++)
			if (reg != 0x27)
				assert(registers[reg] == before[reg]);
	}
	/* Only a Q2 with an AXP2101. */
	reset();
	q2 = false;
	axp2101_q2_powerkey_init(&axp);
	assert(!updates && !bypassed && !cached);
	q2 = true;
	axp.variant = AXP717_ID;
	axp2101_q2_powerkey_init(&axp);
	assert(!updates && !bypassed && !cached);
	/* A failed update warns and skips the readback. */
	axp.variant = AXP2101_ID;
	registers[0x27] = 0xc7;
	update_error = -5;
	axp2101_q2_powerkey_init(&axp);
	assert(updates == 1 && !bypassed && warnings == 1 && !infos && registers[0x27] == 0xc7);
	/* A failed or mismatched readback warns and logs no timing. */
	reset();
	update_error = 0;
	read_error = -5;
	axp2101_q2_powerkey_init(&axp);
	assert(updates == 1 && bypassed == 1 && warnings == 1 && !infos);
	reset();
	read_error = 0;
	wrong_read = true;
	axp2101_q2_powerkey_init(&axp);
	assert(updates == 1 && bypassed == 1 && warnings == 1 && !infos);
	puts("PASS: 256 register states, reserved-bit and other-register preservation, board and variant scope, update and readback faults");
	return 0;
}
