/* Freestanding o32 support for the kernel fault probes.
 *
 * The probes run from the fault kernel's generated initramfs and are built
 * with -nostdlib against the kernel's own UAPI headers, so they depend on
 * nothing but system calls. Each probe defines
 *
 *   static int run(int argc, char **argv)
 *
 * after defining PROBE as its name, and reports one line: "Q2_PROBE_PASS
 * NAME MODE" on success, or "Q2_PROBE_FAIL NAME MODE code=N value=V" and
 * exit status N on failure. The harness (os/kernel_faults.py) requires the
 * pass line and the exit status.
 */
#ifndef Q2_KERNEL_FAULT_PROBE_H
#define Q2_KERNEL_FAULT_PROBE_H

#include <asm/unistd.h>

static const char *probe_mode = "";

/* GCC may emit calls to these for structure initialisers and copies. */
void *memset(void *dest, int value, unsigned int len)
{
	volatile unsigned char *p = dest;

	while (len--)
		*p++ = value;
	return dest;
}

void *memcpy(void *dest, const void *src, unsigned int len)
{
	volatile unsigned char *p = dest;
	const unsigned char *q = src;

	while (len--)
		*p++ = *q++;
	return dest;
}

static long call(long nr, long x, long y, long z)
{
	register long v0 __asm__("$2") = nr;
	register long a0 __asm__("$4") = x;
	register long a1 __asm__("$5") = y;
	register long a2 __asm__("$6") = z;
	register long a3 __asm__("$7");

	/* MIPS o32 system calls may clobber every caller-saved register. */
	__asm__ volatile("syscall"
			 : "+r"(v0), "+r"(a0), "+r"(a1), "+r"(a2), "=r"(a3)
			 :
			 : "$1", "$3", "$8", "$9", "$10", "$11", "$12", "$13",
			   "$14", "$15", "$24", "$25", "hi", "lo", "memory");
	return a3 ? -v0 : v0;
}

static inline int same(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static void say(const char *s)
{
	unsigned int n = 0;

	while (s[n])
		n++;
	call(__NR_write, 1, (long)s, n);
}

static void number(long value)
{
	char buf[12];
	unsigned int n = 0;
	unsigned long v = value;

	if (value < 0) {
		say("-");
		v = -(unsigned long)value;
	}
	do
		buf[n++] = '0' + v % 10;
	while (v /= 10);
	while (n)
		call(__NR_write, 1, (long)&buf[--n], 1);
}

static void __attribute__((noreturn)) leave(int code)
{
	call(__NR_exit, code, 0, 0);
	for (;;)
		;
}

static void __attribute__((noreturn)) fail(int code, long value)
{
	say("Q2_PROBE_FAIL " PROBE " ");
	say(probe_mode);
	say(" code=");
	number(code);
	say(" value=");
	number(value);
	say("\n");
	leave(code);
}

static void __attribute__((noreturn)) pass(void)
{
	say("Q2_PROBE_PASS " PROBE " ");
	say(probe_mode);
	say("\n");
	leave(0);
}

static inline void seek(long fd, long offset, int code)
{
	long ret = call(__NR_lseek, fd, offset, 0);

	if (ret != offset)
		fail(code, ret);
}

static int run(int argc, char **argv);

void start_c(long *sp)
{
	int argc = sp[0];
	char **argv = (char **)&sp[1];

	if (argc > 1)
		probe_mode = argv[1];
	if (run(argc, argv))
		fail(90, argc);
	pass();
}

__asm__(".text\n.globl _start\n.set noreorder\n_start:\n"
	"move $4, $29\naddiu $29, $29, -32\n"
	"jal start_c\nnop\n.set reorder\n");

#endif
