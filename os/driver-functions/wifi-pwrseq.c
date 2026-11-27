/* Q2 Wi-Fi and Bluetooth combo-chip prerequisites (drivers/mmc/core/pwrseq_q2.c).
 *
 * The whole driver runs against generated regulator providers. The MMC power
 * sequence is published only after aldo1, bldo1 and bldo2 are acquired and
 * enabled, so MSC1 defers until every supply has arrived, in any order.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define EPROBE_DEFER 517
#define GFP_KERNEL 0
#define THIS_MODULE NULL
#define MODULE_DEVICE_TABLE(type, name)
#define MODULE_DESCRIPTION(text)
#define MODULE_LICENSE(text)
#define module_platform_driver(driver)
struct device { unsigned present, ready; };
struct platform_device { struct device dev; void *data; };
struct mmc_pwrseq_ops { int unused; };
struct mmc_pwrseq { struct device *dev; const struct mmc_pwrseq_ops *ops; void *owner; };
struct of_device_id { const char *compatible; };
struct platform_driver {
	int (*probe)(struct platform_device *);
	void (*remove)(struct platform_device *);
	struct { const char *name; const struct of_device_id *of_match_table; } driver;
};
static bool fail_alloc;
static int supply_error, register_error, published, acquired;
static struct mmc_pwrseq allocation;
static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{
	assert(size == sizeof(allocation));
	memset(&allocation, 0x5a, sizeof(allocation));
	if (fail_alloc)
		return NULL;
	memset(&allocation, 0, sizeof(allocation));
	return &allocation;
}
static bool device_property_present(const struct device *dev, const char *name)
{
	if (!strcmp(name, "aldo1-supply")) return dev->present & 1;
	if (!strcmp(name, "bldo1-supply")) return dev->present & 2;
	if (!strcmp(name, "bldo2-supply")) return dev->present & 4;
	assert(!"unexpected property");
	return false;
}
static int devm_regulator_bulk_get_enable(struct device *dev, int count, const char *const *ids)
{
	assert(count == 3);
	assert(!strcmp(ids[0], "aldo1") && !strcmp(ids[1], "bldo1") && !strcmp(ids[2], "bldo2"));
	if (supply_error)
		return supply_error;
	if (dev->ready != 7)
		return -EPROBE_DEFER;
	acquired = 1;
	return 0;
}
static int dev_err_probe(const struct device *dev, int error, const char *fmt, ...) { return error; }
static void platform_set_drvdata(struct platform_device *dev, void *data) { dev->data = data; }
static void *platform_get_drvdata(const struct platform_device *dev) { return dev->data; }
static int mmc_pwrseq_register(struct mmc_pwrseq *p)
{
	assert(acquired && p->dev->ready == 7 && p->ops);
	if (register_error)
		return register_error;
	assert(!published);
	published = 1;
	return 0;
}
static void mmc_pwrseq_unregister(struct mmc_pwrseq *p) { assert(p && published); published = 0; }

/* PRODUCTION SOURCE */

int main(void)
{
	struct platform_device dev = {0};
	unsigned seed = 1, traces = 0;
	/* Supplies arrive in a generated order, some at the same time. */
	for (int trial = 0; trial < 4096; trial++) {
		unsigned due[3];
		dev.dev.present = 7;
		dev.dev.ready = 0;
		acquired = 0;
		for (int i = 0; i < 3; i++) {
			seed = seed * 1664525u + 1013904223u;
			due[i] = seed % 31;
		}
		for (unsigned now = 0; now <= 30; now++) {
			for (int i = 0; i < 3; i++)
				if (now >= due[i])
					dev.dev.ready |= 1u << i;
			int result = q2_pwrseq_driver.probe(&dev);
			if (dev.dev.ready != 7) {
				assert(result == -EPROBE_DEFER && !published);
			} else {
				assert(!result && published);
				q2_pwrseq_driver.remove(&dev);
				assert(!published);
				traces++;
				break;
			}
		}
	}
	assert(traces == 4096);
	/* A DT without one of the supply properties is refused before any supply is taken. */
	for (unsigned present = 0; present < 7; present++) {
		dev.dev.present = present;
		dev.dev.ready = 7;
		acquired = 0;
		assert(q2_pwrseq_probe(&dev) == -EINVAL && !published && !acquired);
	}
	dev.dev.present = dev.dev.ready = 7;
	fail_alloc = true;
	assert(q2_pwrseq_probe(&dev) == -ENOMEM && !published);
	fail_alloc = false;
	for (int failure = 0; failure < 3; failure++) {
		supply_error = (int[]){-EPROBE_DEFER, -EIO, -ENODEV}[failure];
		acquired = 0;
		assert(q2_pwrseq_probe(&dev) == supply_error && !published && !acquired);
	}
	supply_error = 0;
	register_error = -EINVAL;
	assert(q2_pwrseq_probe(&dev) == -EINVAL && !published);
	register_error = 0;
	assert(!strcmp(q2_pwrseq_match[0].compatible, "shanling,q2-wifi-pwrseq") && !q2_pwrseq_match[1].compatible);
	puts("PASS: 4096 supply arrival orders, missing properties, allocation, supply and registration failures, removal");
	return 0;
}
