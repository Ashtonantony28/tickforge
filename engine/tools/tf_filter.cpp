// Keep only the ITCH messages for a set of symbols (plus system events), preserving framing.
// A full day is ~11 GB; one symbol's sub-stream is tens of MB, so backtests (M5) can sweep
// parameters without re-reading the whole day. Book state for the kept symbols is unchanged
// because every order message carries its instrument's stock locate.
//   tf_filter IN OUT AAPL,MSFT
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <set>
#include <sstream>
#include <string>
#include <vector>

int main(int argc, char** argv) {
  if (argc < 4) {
    std::fprintf(stderr, "usage: %s IN OUT SYM1,SYM2\n", argv[0]);
    return 2;
  }
  std::set<std::string> want;
  std::stringstream ss(argv[3]);
  for (std::string s; std::getline(ss, s, ',');) want.insert(s);
  std::FILE* in = std::fopen(argv[1], "rb");
  std::FILE* out = std::fopen(argv[2], "wb");
  if (!in || !out) {
    std::perror("open");
    return 1;
  }
  std::vector<bool> keep(65536, false);
  std::vector<uint8_t> buf(1 << 24);
  std::size_t have = 0, pos = 0;
  uint64_t n_in = 0, n_out = 0;
  for (;;) {
    std::memmove(buf.data(), buf.data() + pos, have - pos);
    have -= pos;
    pos = 0;
    const std::size_t got = std::fread(buf.data() + have, 1, buf.size() - have, in);
    have += got;
    while (have - pos >= 2) {
      const std::size_t len = (std::size_t{buf[pos]} << 8) | buf[pos + 1];
      if (have - pos < 2 + len) break;
      const uint8_t* m = buf.data() + pos + 2;
      ++n_in;
      bool k = false;
      if (len >= 3) {
        const uint16_t locate = static_cast<uint16_t>((m[1] << 8) | m[2]);
        if (m[0] == 'S') {
          k = true;
        } else if (m[0] == 'R' && len >= 19) {
          std::string sym(reinterpret_cast<const char*>(m + 11), 8);
          sym.resize(sym.find_last_not_of(' ') + 1);
          if (want.count(sym)) keep[locate] = true;
          k = keep[locate];
        } else {
          k = keep[locate];
        }
      }
      if (k) {
        std::fwrite(buf.data() + pos, 1, 2 + len, out);
        ++n_out;
      }
      pos += 2 + len;
    }
    if (got == 0) break;
  }
  std::fclose(in);
  std::fclose(out);
  std::fprintf(stderr, "kept %llu of %llu messages\n", (unsigned long long)n_out, (unsigned long long)n_in);
  return 0;
}
