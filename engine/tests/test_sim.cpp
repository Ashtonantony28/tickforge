// Backtester smoke tests in C++ (so ASan/UBSan cover sim.hpp); detailed fill-model scenarios with
// hand-computed expectations live in tests/test_backtester.py.
#include <fstream>
#include <string>
#include <vector>

#include "tf_test.hpp"
#include "tickforge/sim.hpp"

namespace {
std::vector<uint8_t> fixture() {
  std::ifstream f(std::string(TF_FIXTURE_DIR) + "/synth_small.itch", std::ios::binary);
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

struct Result {
  uint64_t digest;
  int64_t cash, position;
  std::size_t fills;
};

Result run(const std::vector<uint8_t>& data, uint64_t seed, int64_t jitter) {
  tf::SimConfig cfg;
  cfg.locate = 1;
  cfg.seed = seed;
  cfg.jitter_ns = jitter;
  tf::TouchQuoter q(100);
  tf::Sim sim(cfg, &q);
  TF_CHECK_EQ(sim.feed(data.data(), data.size()), data.size());
  sim.finish();
  int64_t cash = 0, pos = 0;
  for (const auto& f : sim.fills()) {
    const int64_t sgn = f.side == tf::Side::Buy ? 1 : -1;
    cash -= sgn * int64_t{f.price} * int64_t{f.qty} + f.fee;
    pos += sgn * int64_t{f.qty};
  }
  TF_CHECK_EQ(cash, sim.cash());
  TF_CHECK_EQ(pos, sim.position());
  TF_CHECK(pos <= cfg.max_position && -pos <= cfg.max_position);
  return {sim.fill_digest(), sim.cash(), sim.position(), sim.fills().size()};
}
}  // namespace

TF_TEST(sim_touch_quoter_deterministic_and_consistent) {
  const auto data = fixture();
  const Result a = run(data, 7, 3000), b = run(data, 7, 3000);
  TF_CHECK(a.fills > 0);
  TF_CHECK_EQ(a.digest, b.digest);
  TF_CHECK_EQ(a.cash, b.cash);
  TF_CHECK_EQ(a.position, b.position);
}
