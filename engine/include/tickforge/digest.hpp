// Book digest shared bit-for-bit with python/tickforge/refbook.py and, later, the RTL bench.
// FNV-1a over 64-bit words: h = (h ^ w) * P mod 2^64.
#pragma once
#include <cstdint>

namespace tf {

inline constexpr uint64_t kDigestInit = 0xcbf29ce484222325ULL;
inline constexpr uint64_t kDigestPrime = 0x100000001b3ULL;

struct Digest {
  uint64_t h = kDigestInit;
  void word(uint64_t w) { h = (h ^ w) * kDigestPrime; }
};

}  // namespace tf
