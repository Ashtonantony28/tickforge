#include <string>

#include "tf_test.hpp"
#include "tickforge/mm.hpp"

TF_TEST(as_quotes_symmetric_when_flat) {
  tf::MMParams p;
  p.gamma = 0.1, p.k = 1.5, p.tau_s = 60;
  // sigma2 = 0 -> delta = ln(1 + gamma/k)/gamma = 0.6454 $: quotes 65 ticks either side of 100.00
  auto [b, a] = tf::AvellanedaStoikov::quotes(p, 100.0, 0, 0.0, 0.0);
  TF_CHECK_EQ(b, 1'000'000u - 6500u);
  TF_CHECK_EQ(a, 1'000'000u + 6500u);
}

TF_TEST(as_quotes_lean_against_inventory_and_toward_alpha) {
  tf::MMParams p;
  p.gamma = 1.0, p.k = 100.0, p.tau_s = 100;
  const double s2 = 1e-6;  // $^2/s -> gamma*s2*tau = 1e-4 $ per share of inventory
  auto [b0, a0] = tf::AvellanedaStoikov::quotes(p, 50.0, 0, s2, 0.0);
  auto [b1, a1] = tf::AvellanedaStoikov::quotes(p, 50.0, 300, s2, 0.0);  // long 300: r down 3 cents
  TF_CHECK(b1 < b0 && a1 < a0);
  TF_CHECK_EQ(b0 - b1, 300u);
  p.alpha_ticks = 2.0;
  auto [b2, a2] = tf::AvellanedaStoikov::quotes(p, 50.0, 0, s2, 1.0);  // full bid imbalance: up 2 ticks
  TF_CHECK_EQ(b2 - b0, 200u);
  TF_CHECK_EQ(a2 - a0, 200u);
}

namespace {
std::vector<uint8_t> load(const std::string& path) {
  std::vector<uint8_t> v;
  std::FILE* f = std::fopen(path.c_str(), "rb");
  if (!f) return v;
  std::fseek(f, 0, SEEK_END);
  v.resize(static_cast<std::size_t>(std::ftell(f)));
  std::fseek(f, 0, SEEK_SET);
  if (std::fread(v.data(), 1, v.size(), f) != v.size()) v.clear();
  std::fclose(f);
  return v;
}
}  // namespace

TF_TEST(as_backtest_deterministic_and_within_limits) {
  const auto data = load(std::string(TF_FIXTURE_DIR) + "/synth_small.itch");
  TF_CHECK(!data.empty());
  uint64_t digest[2] = {};
  for (int run = 0; run < 2; ++run) {
    tf::MMParams p;
    p.gamma = 0.01, p.k = 50, p.size = 100, p.start_ns = 0;
    p.stop_ns = ~0ULL, p.flat_ns = ~0ULL;
    tf::SimConfig cfg;
    cfg.locate = 1, cfg.max_position = 500;
    tf::AvellanedaStoikov mm(p);
    tf::Sim sim(cfg, &mm);
    sim.feed(data.data(), data.size());
    sim.finish();
    TF_CHECK(sim.stats().fills > 0);
    TF_CHECK(std::llabs(sim.position()) <= 500);
    TF_CHECK_EQ(sim.stats().taker_fills, uint64_t{0});  // post-only quoting never takes
    digest[run] = sim.fill_digest();
  }
  TF_CHECK_EQ(digest[0], digest[1]);
}
