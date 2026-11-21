#include <metal_stdlib>
using namespace metal;

struct Parameters {
    uint low;
    uint high;
    uint control;
    float c0, s0, c1, s1;
    float2 phases[4];
};

uint insert_zero(uint index, uint mask) {
    return (index & (mask - 1)) | ((index & ~(mask - 1)) << 1);
}

float2 phase(float2 value, float2 coefficient) {
    return float2(fma(-value.y, coefficient.y, value.x * coefficient.x),
                  fma(value.x, coefficient.y, value.y * coefficient.x));
}

kernel void hadamard(device float2 *state [[buffer(0)]],
                     constant Parameters &p [[buffer(1)]], uint i [[thread_position_in_grid]]) {
    uint a = insert_zero(i, p.low), b = a | p.low;
    float2 x = state[a], y = state[b];
    state[a] = (x + y) * 0.7071067811865475f;
    state[b] = (x - y) * 0.7071067811865475f;
}

kernel void controlled_x(device float2 *state [[buffer(0)]],
                         constant Parameters &p [[buffer(1)]], uint i [[thread_position_in_grid]]) {
    uint control = p.control;
    uint target = control == p.low ? p.high : p.low;
    uint a = insert_zero(insert_zero(i, p.low), p.high) | control;
    float2 x = state[a];
    state[a] = state[a | target];
    state[a | target] = x;
}

kernel void rotate_pair(device float2 *state [[buffer(0)]],
                        constant Parameters &p [[buffer(1)]], uint i [[thread_position_in_grid]]) {
    uint a = insert_zero(i, p.low), b = a | p.low;
    float2 x = state[a], y = state[b];
    state[a] = phase(fma(p.c0, x, -p.s0 * y), p.phases[0]);
    state[b] = phase(fma(p.s0, x, p.c0 * y), p.phases[1]);
}

kernel void rotate_quad(device float2 *state [[buffer(0)]],
                        constant Parameters &p [[buffer(1)]], uint i [[thread_position_in_grid]]) {
    uint a = insert_zero(insert_zero(i, p.low), p.high);
    uint b = a | p.low, c = a | p.high, d = c | p.low;
    float2 x = state[a], y = state[b], z = state[c], w = state[d];
    float2 u = fma(p.c0, x, -p.s0 * y), v = fma(p.s0, x, p.c0 * y);
    float2 s = fma(p.c0, z, -p.s0 * w), t = fma(p.s0, z, p.c0 * w);
    state[a] = phase(fma(p.c1, u, -p.s1 * s), p.phases[0]);
    state[b] = phase(fma(p.c1, v, -p.s1 * t), p.phases[1]);
    state[c] = phase(fma(p.s1, u, p.c1 * s), p.phases[2]);
    state[d] = phase(fma(p.s1, v, p.c1 * t), p.phases[3]);
}
