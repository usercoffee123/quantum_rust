#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <simd/simd.h>
#include <algorithm>
#include <cmath>
#include <iostream>
#include <vector>

enum class Kernel { Hadamard, ControlledX, RotatePair, RotateQuad };
struct Parameters {
    uint32_t low, high, control;
    float c0, s0, c1, s1;
    vector_float2 phases[4];
};
struct Operation {
    Kernel kernel;
    Parameters parameters;
};

static NSInteger index_field(NSDictionary *object, NSString *name, NSInteger limit) {
    id value = object[name];
    if (![value isKindOfClass:NSNumber.class] || [value doubleValue] < 0 ||
        [value doubleValue] >= limit || [value doubleValue] != [value integerValue]) {
        @throw [NSException exceptionWithName:@"Invalid request" reason:name userInfo:nil];
    }
    return [value integerValue];
}

static bool matches(NSArray *gates, NSUInteger i, NSString *name, NSInteger target) {
    if (i >= gates.count) return false;
    NSDictionary *gate = gates[i];
    return [gate[@"gate"] isEqual:name] && [gate[@"target"] integerValue] == target;
}

static vector_float2 phase(double angle) {
    return {(float)std::cos(angle), (float)std::sin(angle)};
}

static std::vector<Operation> compile_gates(NSArray *gates, NSInteger n, bool fuse) {
    for (NSDictionary *gate in gates) {
        index_field(gate, @"target", n);
        NSString *name = gate[@"gate"];
        if ([name isEqual:@"ry"] || [name isEqual:@"rz"]) {
            id theta = gate[@"theta"];
            if (![theta isKindOfClass:NSNumber.class] || !std::isfinite([theta doubleValue])) {
                @throw [NSException exceptionWithName:@"Invalid request" reason:@"angle" userInfo:nil];
            }
        } else if ([name isEqual:@"cx"]) {
            if (index_field(gate, @"control", n) == [gate[@"target"] integerValue]) {
                @throw [NSException exceptionWithName:@"Invalid request" reason:@"control equals target" userInfo:nil];
            }
        } else if (![name isEqual:@"h"]) {
            @throw [NSException exceptionWithName:@"Invalid request" reason:@"expected H, CX, Ry, or Rz" userInfo:nil];
        }
    }
    std::vector<Operation> operations;
    for (NSUInteger i = 0; i < gates.count; ++i) {
        NSDictionary *gate = gates[i];
        NSString *name = gate[@"gate"];
        NSInteger target = [gate[@"target"] integerValue];
        uint32_t mask = 1u << target;
        Parameters p{};
        p.low = mask;
        if ([name isEqual:@"h"]) {
            operations.push_back({Kernel::Hadamard, p});
        } else if ([name isEqual:@"cx"]) {
            uint32_t control = 1u << [gate[@"control"] integerValue];
            p.low = std::min(control, mask);
            p.high = std::max(control, mask);
            p.control = control;
            operations.push_back({Kernel::ControlledX, p});
        } else {
            bool ry = [name isEqual:@"ry"];
            double angle = [gate[@"theta"] doubleValue] / 2;
            double y = ry ? angle : 0, z = ry ? 0 : angle;
            if (fuse && ry && matches(gates, i + 1, @"rz", target)) {
                z = [gates[++i][@"theta"] doubleValue] / 2;
                if (i + 2 < gates.count && [gates[i + 1][@"gate"] isEqual:@"ry"]) {
                    NSInteger second = [gates[i + 1][@"target"] integerValue];
                    if (target != second && matches(gates, i + 2, @"rz", second)) {
                        double other_y = [gates[i + 1][@"theta"] doubleValue] / 2;
                        double other_z = [gates[i + 2][@"theta"] doubleValue] / 2;
                        uint32_t other_mask = 1u << second;
                        if (other_mask < mask) {
                            std::swap(y, other_y);
                            std::swap(z, other_z);
                        }
                        p.low = std::min(mask, other_mask);
                        p.high = std::max(mask, other_mask);
                        p.c0 = std::cos(y);
                        p.s0 = std::sin(y);
                        p.c1 = std::cos(other_y);
                        p.s1 = std::sin(other_y);
                        p.phases[0] = phase(-z - other_z);
                        p.phases[1] = phase(z - other_z);
                        p.phases[2] = phase(-z + other_z);
                        p.phases[3] = phase(z + other_z);
                        operations.push_back({Kernel::RotateQuad, p});
                        i += 2;
                        continue;
                    }
                }
            }
            p.c0 = std::cos(y);
            p.s0 = std::sin(y);
            p.phases[0] = phase(-z);
            p.phases[1] = phase(z);
            operations.push_back({Kernel::RotatePair, p});
        }
    }
    return operations;
}

static NSDictionary *simulate(NSDictionary *request, id<MTLDevice> gpu,
                              id<MTLCommandQueue> queue, NSArray *pipelines) {
    if (![request[@"precision"] isEqual:@"f32"] ||
        ![request[@"gates"] isKindOfClass:NSArray.class] ||
        [request[@"include_state"] boolValue] || [request[@"include_norm"] boolValue] ||
        [request[@"optimize"] boolValue] || request[@"benchmark"] || request[@"amplitudes_output"]) {
        return @{@"error": @"Metal prototype requires FP32, probe-only output, and CX compression off"};
    }
    NSInteger n = index_field(request, @"num_qubits", 30);
    NSUInteger count = NSUInteger(1) << n;
    NSUInteger bytes = count * sizeof(vector_float2);
    if (bytes > gpu.maxBufferLength || bytes > [request[@"memory_limit_bytes"] unsignedLongLongValue]) {
        return @{@"error": @"state exceeds memory limit"};
    }
    auto operations = compile_gates(request[@"gates"], n, [request[@"fusion"] boolValue]);
    NSArray *probes = request[@"probe_indices"] ?: @[];
    for (id probe in probes) index_field(@{@"probe": probe}, @"probe", count);
    id<MTLBuffer> buffer = [gpu newBufferWithLength:bytes options:MTLResourceStorageModeShared];
    if (!buffer) return @{@"error": @"Metal buffer allocation failed"};
    id<MTLCommandBuffer> commands = [queue commandBuffer];
    if (!commands) return @{@"error": @"Metal command buffer allocation failed"};
    id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
    [blit fillBuffer:buffer range:NSMakeRange(0, bytes) value:0];
    const vector_float2 one = {1, 0};
    id<MTLBuffer> initial = [gpu newBufferWithBytes:&one length:sizeof(one) options:MTLResourceStorageModeShared];
    if (!initial) return @{@"error": @"Metal initialization buffer allocation failed"};
    [blit copyFromBuffer:initial sourceOffset:0 toBuffer:buffer destinationOffset:0 size:sizeof(one)];
    [blit endEncoding];

    id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
    [encoder setBuffer:buffer offset:0 atIndex:0];
    NSUInteger quads = 0;
    for (const auto &operation : operations) {
        id<MTLComputePipelineState> pipeline = pipelines[(NSUInteger)operation.kernel];
        bool quad = operation.kernel == Kernel::RotateQuad;
        quads += quad;
        NSUInteger work = count >> (quad || operation.kernel == Kernel::ControlledX ? 2 : 1);
        NSUInteger group = std::min<NSUInteger>(256, pipeline.maxTotalThreadsPerThreadgroup);
        [encoder setComputePipelineState:pipeline];
        [encoder setBytes:&operation.parameters length:sizeof(Parameters) atIndex:1];
        [encoder dispatchThreads:MTLSizeMake(work, 1, 1) threadsPerThreadgroup:MTLSizeMake(group, 1, 1)];
    }
    [encoder endEncoding];
    [commands commit];
    [commands waitUntilCompleted];
    if (commands.status != MTLCommandBufferStatusCompleted) {
        return @{@"error": commands.error.localizedDescription ?: @"Metal execution failed"};
    }
    const vector_float2 *state = static_cast<const vector_float2 *>(buffer.contents);
    NSMutableArray *values = [NSMutableArray arrayWithCapacity:probes.count];
    for (NSNumber *index in probes) {
        vector_float2 value = state[index.unsignedIntegerValue];
        if (!std::isfinite(value.x) || !std::isfinite(value.y)) return @{@"error": @"nonfinite probe"};
        [values addObject:@[@(value.x), @(value.y)]];
    }
    return @{@"probes": values, @"state_bytes": @(bytes), @"operations": @(operations.size()),
             @"structured_blocks": @(quads), @"gpu_seconds": @(commands.GPUEndTime - commands.GPUStartTime),
             @"device": gpu.name};
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            std::cerr << "Usage: metal_statevector tools/metal_statevector.metal\n";
            return 2;
        }
        id<MTLDevice> gpu = MTLCreateSystemDefaultDevice();
        if (!gpu) {
            std::cerr << "No Metal device available\n";
            return 1;
        }
        NSError *error = nil;
        NSString *source = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
        if (!source) {
            std::cerr << error.localizedDescription.UTF8String << '\n';
            return 1;
        }
        MTLCompileOptions *options = [MTLCompileOptions new];
        options.mathMode = MTLMathModeSafe;
        id<MTLLibrary> library = [gpu newLibraryWithSource:source options:options error:&error];
        if (!library) {
            std::cerr << error.localizedDescription.UTF8String << '\n';
            return 1;
        }
        NSMutableArray *pipelines = [NSMutableArray array];
        for (NSString *name in @[@"hadamard", @"controlled_x", @"rotate_pair", @"rotate_quad"]) {
            id<MTLComputePipelineState> pipeline =
                [gpu newComputePipelineStateWithFunction:[library newFunctionWithName:name] error:&error];
            if (!pipeline) {
                std::cerr << error.localizedDescription.UTF8String << '\n';
                return 1;
            }
            [pipelines addObject:pipeline];
        }
        id<MTLCommandQueue> queue = [gpu newCommandQueue];
        if (!queue) return 1;
        std::string line;
        while (std::getline(std::cin, line)) {
            @autoreleasepool {
                NSData *data = [NSData dataWithBytes:line.data() length:line.size()];
                id request = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
                NSDictionary *result = nil;
                @try {
                    result = [request isKindOfClass:NSDictionary.class]
                        ? simulate(request, gpu, queue, pipelines) : @{@"error": @"invalid JSON object"};
                } @catch (NSException *exception) {
                    result = @{@"error": exception.reason ?: @"invalid request"};
                }
                NSData *encoded = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
                std::cout.write(static_cast<const char *>(encoded.bytes), encoded.length);
                std::cout << std::endl;
            }
        }
    }
}
