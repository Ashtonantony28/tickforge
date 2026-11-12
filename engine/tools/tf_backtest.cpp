// tf_backtest: run the TouchQuoter baseline over an ITCH file for one instrument and print a JSON
// summary with a P&L decomposition. Example:
//   tf_backtest FILE --locate 1 --latency-ns 20000 --size 100
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "tickforge/sim.hpp"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s FILE [--locate N] [--latency-ns N] [--jitter-ns N] [--size N] [--max-pos N]\n", argv[0]);
    return 2;
  }
  tf::SimConfig cfg;
  uint32_t size = 100;
  for (int i = 2; i + 1 < argc; i += 2) {
    const char* k = argv[i];
    const long long v = std::atoll(argv[i + 1]);
    if (!std::strcmp(k, "--locate")) cfg.locate = static_cast<uint16_t>(v);
    else if (!std::strcmp(k, "--latency-ns")) cfg.latency_ns = v;
    else if (!std::strcmp(k, "--jitter-ns")) cfg.jitter_ns = v;
    else if (!std::strcmp(k, "--size")) size = static_cast<uint32_t>(v);
    else if (!std::strcmp(k, "--max-pos")) cfg.max_position = v;
    else { std::fprintf(stderr, "unknown option %s\n", k); return 2; }
  }
  std::FILE* f = std::fopen(argv[1], "rb");
  if (!f) { std::perror(argv[1]); return 1; }
  tf::TouchQuoter q(size);
  tf::Sim sim(cfg, &q);
  std::vector<uint8_t> buf(1 << 24);
  std::size_t have = 0;
  for (;;) {
    const std::size_t got = std::fread(buf.data() + have, 1, buf.size() - have, f);
    have += got;
    const std::size_t used = sim.feed(buf.data(), have);
    std::memmove(buf.data(), buf.data() + used, have - used);
    have -= used;
    if (got == 0) break;
  }
  std::fclose(f);
  sim.finish();

  // P&L split (marked at the final mid): gross trading P&L before fees, fee/rebate total
  // (positive = net rebate received), and net. Spread-capture vs adverse-selection decomposition
  // arrives with the M5 market makers.
  const auto& s = sim.stats();
  const int64_t equity = sim.equity();
  const int64_t gross = equity + sim.fees_paid();
  std::printf("{\"messages\": %llu, \"fills\": %llu, \"maker_fills\": %llu, \"taker_fills\": %llu, "
              "\"shares_bought\": %llu, \"shares_sold\": %llu, \"final_position\": %lld, "
              "\"gross_pnl_usd\": %.4f, \"fee_rebate_usd\": %.4f, \"net_pnl_usd\": %.4f, "
              "\"submitted\": %llu, \"rejected\": %llu, \"post_only_rejects\": %llu, \"cancel_rejects\": %llu, "
              "\"fill_digest\": \"%016llx\"}\n",
              (unsigned long long)sim.messages(), (unsigned long long)s.fills, (unsigned long long)s.maker_fills,
              (unsigned long long)s.taker_fills, (unsigned long long)s.shares_bought,
              (unsigned long long)s.shares_sold, (long long)sim.position(), static_cast<double>(gross) / 1e4,
              static_cast<double>(-sim.fees_paid()) / 1e4, static_cast<double>(equity) / 1e4, (unsigned long long)s.submitted, (unsigned long long)s.rejected,
              (unsigned long long)s.post_only_rejects, (unsigned long long)s.cancel_rejects,
              (unsigned long long)sim.fill_digest());
  return 0;
}
