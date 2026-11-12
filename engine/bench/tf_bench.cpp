// tf_bench: in-memory replay throughput. Loads the whole file, replays it R times (fresh books each
// run) and reports the best and median messages/second. Not a substitute for perf counters, but
// stable enough to catch regressions.
//   tf_bench FILE [--runs R] [--no-digest]
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "tickforge/replay.hpp"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s FILE [--runs R] [--no-digest]\n", argv[0]);
    return 2;
  }
  int runs = 5;
  bool with_digest = true;
  for (int i = 2; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--runs") && i + 1 < argc) runs = std::atoi(argv[++i]);
    else if (!std::strcmp(argv[i], "--no-digest")) with_digest = false;
  }
  std::FILE* f = std::fopen(argv[1], "rb");
  if (!f) {
    std::perror(argv[1]);
    return 1;
  }
  std::vector<uint8_t> data;
  uint8_t chunk[1 << 16];
  std::size_t got;
  while ((got = std::fread(chunk, 1, sizeof chunk, f)) > 0) data.insert(data.end(), chunk, chunk + got);
  std::fclose(f);

  std::vector<double> rates;
  uint64_t msgs = 0, digest = 0;
  for (int r = 0; r < runs; ++r) {
    tf::Replayer rep(with_digest);
    const auto t0 = std::chrono::steady_clock::now();
    rep.feed(data.data(), data.size());
    const auto t1 = std::chrono::steady_clock::now();
    msgs = rep.messages();
    digest = rep.digest();
    rates.push_back(static_cast<double>(msgs) / std::chrono::duration<double>(t1 - t0).count());
  }
  std::sort(rates.begin(), rates.end());
  std::printf("{\"messages\": %llu, \"bytes\": %zu, \"runs\": %d, \"digest\": \"%016llx\", "
              "\"best_msgs_per_s\": %.0f, \"median_msgs_per_s\": %.0f}\n",
              (unsigned long long)msgs, data.size(), runs, (unsigned long long)digest, rates.back(),
              rates[rates.size() / 2]);
  return 0;
}
