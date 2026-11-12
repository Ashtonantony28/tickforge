// tf_replay: replay an ITCH 5.0 file (length-prefixed, uncompressed) and print a JSON summary:
// message counts, book stats, digest, and optionally top-N depth for every instrument.
//   tf_replay FILE [--depth N] [--no-digest]
// Streams the file in chunks so full-day files (several GB) need constant memory.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "tickforge/replay.hpp"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s FILE [--depth N] [--no-digest]\n", argv[0]);
    return 2;
  }
  int depth = 0;
  bool with_digest = true;
  for (int i = 2; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--depth") && i + 1 < argc) depth = std::atoi(argv[++i]);
    else if (!std::strcmp(argv[i], "--no-digest")) with_digest = false;
  }
  std::FILE* f = std::fopen(argv[1], "rb");
  if (!f) {
    std::perror(argv[1]);
    return 1;
  }
  tf::Replayer r(with_digest);
  std::vector<uint8_t> buf(1 << 24);
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
  const auto& s = r.books().stats();
  std::printf("{\n  \"messages\": %llu,\n  \"digest\": \"%016llx\",\n", (unsigned long long)r.messages(),
              (unsigned long long)r.digest());
  std::printf("  \"trailing_bytes\": %zu,\n  \"bad_messages\": %llu,\n  \"trades\": %llu,\n", have,
              (unsigned long long)r.bad_messages(), (unsigned long long)r.trades());
  std::printf("  \"live_orders\": %zu,\n", r.books().live_orders());
  std::printf(
      "  \"stats\": {\"adds\": %llu, \"executions\": %llu, \"cancels\": %llu, \"deletes\": %llu, "
      "\"replaces\": %llu, \"unknown_ref\": %llu, \"duplicate_ref\": %llu, \"overfill\": %llu, \"zero_add\": %llu}",
      (unsigned long long)s.adds, (unsigned long long)s.executions, (unsigned long long)s.cancels,
      (unsigned long long)s.deletes, (unsigned long long)s.replaces, (unsigned long long)s.unknown_ref,
      (unsigned long long)s.duplicate_ref, (unsigned long long)s.overfill, (unsigned long long)s.zero_add);
  if (depth > 0) {
    std::printf(",\n  \"books\": {");
    bool first = true;
    for (std::size_t loc = 0; loc < r.books().num_books(); ++loc) {
      const tf::Book* b = r.books().book(static_cast<uint16_t>(loc));
      if (b->bids.empty() && b->asks.empty()) continue;
      std::printf("%s\n    \"%zu\": {", first ? "" : ",", loc);
      first = false;
      for (int s2 = 0; s2 < 2; ++s2) {
        const tf::BookSide& bs = s2 == 0 ? b->bids : b->asks;
        std::printf("%s\"%c\": [", s2 ? ", " : "", s2 == 0 ? 'B' : 'S');
        for (std::size_t i = 0; i < bs.size() && i < static_cast<std::size_t>(depth); ++i) {
          const tf::Level& l = bs.level(i);
          std::printf("%s[%u, %llu, %u]", i ? ", " : "", l.price, (unsigned long long)l.qty, l.count);
        }
        std::printf("]");
      }
      std::printf("}");
    }
    std::printf("\n  }");
  }
  std::printf("\n}\n");
  return 0;
}
