#include <stdint.h>
struct result { double value; uint64_t bits; uint32_t size; };
void q2_probe_mix(double a, float b, uint64_t n, struct result *out)
{
    out->value = a * b;
    out->bits = n ^ UINT64_C(0x123456789abcdef0);
    out->size = sizeof(*out);
}
double q2_probe_callback(double (*callback)(double, float), double a, float b)
{
    return callback(a, b);
}
