// Replay a synced L2 event stream (python -m tickforge.l2 check --events) through the C++ L2 book.
// Prints counts, crossed batches and the digest shared with python/tickforge/l2.py (ADR 0008).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string_view>
#include <vector>

#include "tickforge/l2_book.hpp"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s EVENTS [--depth N]\n", argv[0]);
    return 2;
  }
  int depth = 0;
  for (int i = 2; i < argc; ++i)
    if (std::string_view(argv[i]) == "--depth" && i + 1 < argc) depth = std::atoi(argv[++i]);
  std::FILE* f = std::fopen(argv[1], "rb");
  if (!f) {
    std::perror(argv[1]);
    return 1;
  }
  tf::L2Replayer r;
  std::vector<uint8_t> buf(1 << 22);
  std::size_t have = 0;
  for (;;) {
    const std::size_t got = std::fread(buf.data() + have, 1, buf.size() - have, f);
    have += got;
    const std::size_t used = r.feed(buf.data(), have);
    std::memmove(buf.data(), buf.data() + used, have - used);
    have -= used;
    if (got == 0) break;
  }
  std::fclose(f);
  std::printf("{\n  \"events\": %llu,\n  \"batches\": %llu,\n  \"crossed\": %llu,\n  \"bad\": %llu,\n",
              (unsigned long long)r.events(), (unsigned long long)r.batches(), (unsigned long long)r.crossed(),
              (unsigned long long)r.bad());
  std::printf("  \"trailing_bytes\": %zu,\n  \"bid_levels\": %zu,\n  \"ask_levels\": %zu,\n", have,
              r.book().bids.size(), r.book().asks.size());
  std::printf("  \"digest\": \"%016llx\"", (unsigned long long)r.digest());
  for (int s = 0; s < 2 && depth > 0; ++s) {
    const tf::L2Side& side = s == 0 ? r.book().bids : r.book().asks;
    std::printf(",\n  \"%s\": [", s == 0 ? "bids" : "asks");
    for (std::size_t i = 0; i < side.size() && i < static_cast<std::size_t>(depth); ++i)
      std::printf("%s[%lld, %lld]", i ? ", " : "", (long long)side.level(i).price, (long long)side.level(i).qty);
    std::printf("]");
  }
  std::printf("\n}\n");
  return 0;
}
