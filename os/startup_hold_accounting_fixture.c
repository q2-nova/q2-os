/* Generated service-test adapter, not an alternate supervisor daemon.
 * The including test substitutes only owned filesystem paths in the actual
 * production source. Watchdog and health qualification remain VM gates. */
#define main production_supervisor_main
#include "supervisor.c"
#undef main

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int failures = number("state/app/boot-failures");
    if (!strcmp(argv[1], "start")) {
        int counted = access("run/boot-counted", F_OK) == 0;
        if (failed_boot_limit(failures, counted)) {
            record("run/rescue", 1);
            return 3;
        }
        if (!counted) {
            if (persist_failures(++failures)) return 4;
            record("run/boot-counted", 1);
        }
        return 0;
    }
    if (!strcmp(argv[1], "stop")) {
        /* Early cancellation has no first frame/health confirmation. This
         * adapter never invents one. A separate explicit policy control may
         * create confirmed; the real daemon determines it from health pulses. */
        int confirmed = access("run/confirmed", F_OK) == 0;
        credit_clean_disarm(failures, confirmed, 1);
        return 0;
    }
    return 2;
}
