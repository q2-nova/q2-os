/* os/x2000-c-build.py: libxcrypt hashes with fixed salts, one per method
 * the image's configuration enables, and crypt_gensalt_rn with fixed
 * random bytes. */
#include <crypt.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
	static const char *settings[] = {
		"$y$j9T$F5Jx5fExrKuPp53xLKQ..1$",       /* yescrypt */
		"$gy$j9T$F5Jx5fExrKuPp53xLKQ..1$",      /* gost-yescrypt */
		"$7$CU..../....F5Jx5fExrKuPp53xLKQ..1$", /* scrypt */
		"$2b$05$abcdefghijklmnopqrstuu",         /* bcrypt */
		"$6$rounds=5000$saltsaltsaltsalt$",      /* sha512crypt */
		"$5$rounds=5000$saltsaltsaltsalt$",      /* sha256crypt */
		"$sha1$4096$saltsalt$",                  /* sha1crypt */
		"$md5,rounds=1000$saltsalt$",            /* SunMD5 */
		"$1$saltsalt$",                          /* md5crypt */
		"_J9..salt",                             /* bsdicrypt */
		"ab",                                    /* descrypt */
	};
	static const char *phrases[] = {"", "q2test", "correct horse battery staple",
		"a phrase of more than seventy-two bytes, which bcrypt truncates and the others read in full"};
	struct crypt_data data;
	for (unsigned i = 0; i < sizeof settings / sizeof *settings; i++)
		for (unsigned j = 0; j < sizeof phrases / sizeof *phrases; j++) {
			memset(&data, 0, sizeof data);
			const char *h = crypt_rn(phrases[j], settings[i], &data, sizeof data);
			printf("%u %u %s\n", i, j, h ? h : "(null)");
		}
	static const char *prefixes[] = {"$y$", "$2b$", "$6$", "$5$", "$1$"};
	const char rbytes[] = "0123456789abcdef0123456789abcdef";
	char salt[CRYPT_GENSALT_OUTPUT_SIZE];
	for (unsigned i = 0; i < sizeof prefixes / sizeof *prefixes; i++)
		printf("gensalt %s %s\n", prefixes[i],
		       crypt_gensalt_rn(prefixes[i], 0, rbytes, 16, salt, sizeof salt) ? salt : "(null)");
	return 0;
}
