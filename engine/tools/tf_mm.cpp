// tf_mm: run the Avellaneda-Stoikov market maker (optionally alpha-skewed) on one symbol of an
// ITCH stream and print a JSON summary with a P&L decomposition (M5, ADR 0010):
//   spread capture  = sum over fills of signed (mid at fill - fill price) * qty
//   adverse select. = sum over fills of signed (mid H seconds later - mid at fill) * qty
//   inventory/other = gross - spread - adverse (holding P&L beyond H, flattening cost)
// Example: tf_mm AAPL.itch --symbol AAPL --gamma 0.1 --k 1.5 --alpha-ticks 0.5 --latency-ns 20000
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "tickforge/mm.hpp"

namespace {

struct Locator : tf::itch::NullHandler {
  std::string want;
  int locate = -1;
  void on_stock_directory(const tf::itch::StockDirectory& s) {
    std::string n(s.stock, 8);
    n.resize(n.find_last_not_of(' ') + 1);
    if (n == want) locate = s.h.locate;
  }
};

// Wraps the market maker to record per-second mids and each fill's mid.
struct Recorder : tf::Strategy {
  tf::Strategy* inner;
  std::vector<double> mid_by_sec = std::vector<double>(86400, 0.0);
  struct F { uint64_t ts; int sign; double px, mid; uint32_t qty; bool maker; };
  std::vector<F> fills;
  double mid = 0;
  explicit Recorder(tf::Strategy* s) : inner(s) {}
  void on_market(tf::Sim& s) override {
    if (s.best_bid() && s.best_ask()) {
      mid = 0.5 * (double(s.best_bid()) + double(s.best_ask())) / 1e4;
      double& slot = mid_by_sec[std::min<uint64_t>(s.now() / 1'000'000'000ULL, 86399)];
      if (slot == 0) slot = mid;  // first mid of each second, so "H seconds later" is not H + 1
    }
    inner->on_market(s);
  }
  void on_fill(tf::Sim& s, const tf::Fill& f) override {
    fills.push_back({f.ts, f.side == tf::Side::Buy ? 1 : -1, double(f.price) / 1e4, mid, f.qty, f.maker});
    inner->on_fill(s, f);
  }
  double mid_at(uint64_t sec) const {  // last known mid at or before sec
    for (uint64_t t = std::min<uint64_t>(sec, 86399);; --t) {
      if (mid_by_sec[t] > 0) return mid_by_sec[t];
      if (t == 0) return 0;
    }
  }
};

std::vector<uint8_t> read_all(const char* path) {
  std::vector<uint8_t> v;
  std::FILE* f = std::fopen(path, "rb");
  if (!f) return v;
  std::fseek(f, 0, SEEK_END);
  v.resize(static_cast<std::size_t>(std::ftell(f)));
  std::fseek(f, 0, SEEK_SET);
  if (std::fread(v.data(), 1, v.size(), f) != v.size()) v.clear();
  std::fclose(f);
  return v;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 4) {
    std::fprintf(stderr, "usage: %s ITCH --symbol SYM [--gamma G] [--k K] [--tau S] [--alpha-ticks A] "
                         "[--size N] [--max-pos N] [--latency-ns N] [--adverse-s H]\n", argv[0]);
    return 2;
  }
  tf::MMParams p;
  tf::SimConfig cfg;
  cfg.sample_ns = 60'000'000'000LL;
  std::string sym;
  int adverse_s = 10;
  for (int i = 2; i + 1 < argc; i += 2) {
    const std::string k = argv[i];
    const char* v = argv[i + 1];
    if (k == "--symbol") sym = v;
    else if (k == "--gamma") p.gamma = std::atof(v);
    else if (k == "--k") p.k = std::atof(v);
    else if (k == "--tau") p.tau_s = std::atof(v);
    else if (k == "--alpha-ticks") p.alpha_ticks = std::atof(v);
    else if (k == "--size") p.size = static_cast<uint32_t>(std::atoi(v));
    else if (k == "--max-pos") cfg.max_position = std::atoll(v);
    else if (k == "--latency-ns") cfg.latency_ns = std::atoll(v);
    else if (k == "--adverse-s") adverse_s = std::atoi(v);
    else if (k == "--join-only") p.join_only = std::atoi(v) != 0;
    else { std::fprintf(stderr, "unknown option %s\n", k.c_str()); return 2; }
  }
  const std::vector<uint8_t> data = read_all(argv[1]);
  if (data.empty()) { std::perror(argv[1]); return 1; }
  Locator loc;
  loc.want = sym;
  tf::itch::parse(data.data(), data.size(), loc);
  if (loc.locate < 0) { std::fprintf(stderr, "symbol %s not found\n", sym.c_str()); return 1; }
  cfg.locate = static_cast<uint16_t>(loc.locate);

  tf::AvellanedaStoikov mm(p);
  Recorder rec(&mm);
  tf::Sim sim(cfg, &rec);
  sim.feed(data.data(), data.size());
  sim.finish();

  double spread = 0, adverse = 0, maker_qty = 0, taker_qty = 0;
  for (const auto& f : rec.fills) {
    spread += f.sign * (f.mid - f.px) * f.qty;
    adverse += f.sign * (rec.mid_at(f.ts / 1'000'000'000ULL + adverse_s) - f.mid) * f.qty;
    (f.maker ? maker_qty : taker_qty) += f.qty;
  }
  const double fees = -double(sim.fees_paid()) / 1e4;  // + = net rebate
  const double net = double(sim.equity()) / 1e4;
  const double gross = net - fees;
  std::printf("{\"symbol\": \"%s\", \"fills\": %zu, \"maker_shares\": %.0f, \"taker_shares\": %.0f, "
              "\"final_position\": %lld, \"gross_usd\": %.2f, \"spread_capture_usd\": %.2f, "
              "\"adverse_selection_usd\": %.2f, \"inventory_other_usd\": %.2f, \"fee_rebate_usd\": %.2f, "
              "\"net_usd\": %.2f, \"post_only_rejects\": %llu, \"cancel_rejects\": %llu, \"fill_digest\": \"%016llx\", "
              "\"equity_by_minute\": [",
              sym.c_str(), rec.fills.size(), maker_qty, taker_qty, (long long)sim.position(), gross, spread, adverse,
              gross - spread - adverse, fees, net, (unsigned long long)sim.stats().post_only_rejects,
              (unsigned long long)sim.stats().cancel_rejects, (unsigned long long)sim.fill_digest());
  bool first = true;
  for (const auto& smp : sim.samples()) {
    std::printf("%s%.2f", first ? "" : ", ", double(smp.equity) / 1e4);
    first = false;
  }
  std::printf("]}\n");
  return 0;
}
