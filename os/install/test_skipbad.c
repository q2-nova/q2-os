/* Tests the driver's exact mapping helpers against generated nandwrite layout. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "mtdblock_skipbad.h"
#define BLOCKS 12
#define ERASE 2048
static int bad[BLOCKS];
static int query(void *unused, unsigned int block) { (void)unused; return bad[block]; }
int main(void)
{
    unsigned int map[BLOCKS], i, j;
    unsigned char physical[BLOCKS * ERASE], image[BLOCKS * ERASE];
    unsigned long long offset;
    int count;
    for (i = 0; i < sizeof(image); i++) image[i] = (i / 512 * 13 + i % 251) & 255;
    for (unsigned int mask = 0; mask < (1U << BLOCKS); mask++) {
        for (i = 0; i < BLOCKS; i++) bad[i] = !!(mask & (1U << i));
        count = skipbad_build_map(map, BLOCKS, query, NULL);
        if (mask == (1U << BLOCKS) - 1) { assert(count == -ENOSPC); continue; }
        assert(count > 0);
        memset(physical, 0xee, sizeof(physical));
        /* Independently model nandwrite: advance logical data only on good blocks. */
        for (i = 0, j = 0; i < BLOCKS; i++) if (!bad[i]) {
            memcpy(physical + i * ERASE, image + j * ERASE, ERASE); j++;
        }
        assert(j == (unsigned int)count);
        for (i = 0; i < j * ERASE / 512; i++) {
            assert(!skipbad_offset(map, count, ERASE, i, &offset));
            assert(!memcmp(physical + offset, image + i * 512, 512));
        }
        assert(skipbad_offset(map, count, ERASE, j * ERASE / 512, &offset) == -EIO);
        assert(skipbad_offset(map, count, ERASE, UINT64_MAX, &offset) == -EIO);
    }
    memset(bad, 0, sizeof(bad)); bad[4] = -EBADMSG;
    assert(skipbad_build_map(map, BLOCKS, query, NULL) == -EBADMSG);
    assert(skipbad_offset(map, 1, 513, 0, &offset) == -EIO);
    puts("4096 bad-block layouts match nandwrite model; bounds and scan errors rejected");
    return 0;
}
