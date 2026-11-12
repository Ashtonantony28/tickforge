// Sample causal L3-book state on a fixed time grid for a set of symbols (M4 feature extraction).
//
// For each grid time t (default: every second of regular hours), and each selected symbol, writes
// the book state after every message with timestamp <= t. Only raw, cumulative counters are
// written; all windowed features and labels are derived in Python (tickforge.features) from these
// columns, so the C++ side has no modelling choices in it.
//
// Output: little-endian int64 matrix [n_grid][n_symbols][kFields] preceded by a JSON header line.
//   tf_features ITCH_FILE OUT.bin --symbols AAPL,MSFT [--start-ns N] [--end-ns N] [--step-ns N]
// Use /dev/stdin with `gzip -dc day.gz |` to avoid storing the 11 GB uncompressed day.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
#include <vector>

#include "tickforge/itch.hpp"
#include "tickforge/order_book.hpp"

namespace {

enum Field : int {
  kBidPx, kBidQty, kAskPx, kAskQty,  // L1 (ITCH price units 1e-4 $, shares)
  kBidDepth5, kAskDepth5,            // shares in the best 5 levels
  kBidCount1, kAskCount1,            // orders at L1
  kOfi,                              // cumulative L1 order-flow imbalance (Cont, Kukanov, Stoikov 2014)
  kBuyVol, kSellVol,                 // cumulative aggressor volume (visible executions + hidden trades)
  kTrades,                           // cumulative executions
  kAddBid, kAddAsk,                  // cumulative added shares
  kCxlBid, kCxlAsk,                  // cumulative cancelled/deleted shares
  kLastPx,                           // last execution price
  kFields
};
const char* kFieldNames[kFields] = {"bid_px", "bid_qty", "ask_px", "ask_qty", "bid_depth5", "ask_depth5",
                                    "bid_count1", "ask_count1", "ofi", "buy_vol", "sell_vol", "trades",
                                    "add_bid", "add_ask", "cxl_bid", "cxl_ask", "last_px"};

struct SymState {
  int64_t f[kFields] = {};
  int64_t prev_bid_px = 0, prev_bid_qty = 0, prev_ask_px = 0, prev_ask_qty = 0;
};

class Extractor : public tf::itch::NullHandler {
 public:
  Extractor(std::vector<std::string> syms, uint64_t start, uint64_t end, uint64_t step, std::FILE* out)
      : syms_(std::move(syms)), next_(start + step), end_(end), step_(step), out_(out), books_(1 << 22) {
    state_.resize(syms_.size());
  }

  std::size_t feed(const uint8_t* p, std::size_t n) { return tf::itch::parse(p, n, *this).bytes_consumed; }

  void on_stock_directory(const tf::itch::StockDirectory& s) {
    std::string name(s.stock, 8);
    name.resize(name.find_last_not_of(' ') + 1);
    for (std::size_t i = 0; i < syms_.size(); ++i)
      if (syms_[i] == name) {
        if (s.h.locate >= slot_.size()) slot_.resize(std::size_t{s.h.locate} + 1, -1);
        slot_[s.h.locate] = static_cast<int>(i);
      }
  }
  void on_add(const tf::itch::AddOrder& a) {
    tick(a.h);
    const int k = slot(a.h.locate);
    if (k >= 0 && a.shares) state_[k].f[a.side == 'B' ? kAddBid : kAddAsk] += a.shares;
    touched_ = books_.add(a.ref, a.h.locate, a.side == 'B' ? tf::Side::Buy : tf::Side::Sell, a.shares, a.price);
  }
  void on_executed(const tf::itch::OrderExecuted& e) {
    tick(e.h);
    exec(e.ref, e.shares, 0);
    touched_ = books_.reduce(e.ref, e.shares, true);
  }
  void on_executed_price(const tf::itch::OrderExecutedPrice& e) {
    tick(e.h);
    exec(e.ref, e.shares, e.price);
    touched_ = books_.reduce(e.ref, e.shares, true);
  }
  void on_cancel(const tf::itch::OrderCancel& x) {
    tick(x.h);
    cancel(x.ref, x.shares);
    touched_ = books_.reduce(x.ref, x.shares, false);
  }
  void on_delete(const tf::itch::OrderDelete& d) {
    tick(d.h);
    cancel(d.ref, 0);
    touched_ = books_.remove(d.ref);
  }
  void on_replace(const tf::itch::OrderReplace& u) {
    tick(u.h);
    cancel(u.old_ref, 0);
    if (const tf::Order* o = books_.order(u.old_ref)) {
      const int k = slot(o->locate);
      if (k >= 0) state_[k].f[o->side == tf::Side::Buy ? kAddBid : kAddAsk] += u.shares;
    }
    touched_ = books_.replace(u.old_ref, u.new_ref, u.shares, u.price);
  }
  void on_trade(const tf::itch::Trade& t) {  // hidden-order execution; side = resting order's side
    tick(t.h);
    const int k = slot(t.h.locate);
    if (k < 0) return;
    state_[k].f[t.side == 'B' ? kSellVol : kBuyVol] += t.shares;
    state_[k].f[kTrades] += 1;
    state_[k].f[kLastPx] = t.price;
  }
  void on_message_end() {
    if (touched_ >= 0) update_l1(static_cast<uint16_t>(touched_));
    touched_ = tf::MarketBooks::kUnknown;
  }

  void finish() {
    while (next_ <= end_) emit();
  }
  uint64_t rows() const { return rows_; }

 private:
  int slot(uint16_t locate) const { return locate < slot_.size() ? slot_[locate] : -1; }

  void tick(const tf::itch::Header& h) {
    while (next_ <= end_ && h.timestamp > next_) emit();
  }

  void exec(uint64_t ref, uint32_t shares, uint32_t px) {
    const tf::Order* o = books_.order(ref);
    if (!o) return;
    const int k = slot(o->locate);
    if (k < 0) return;
    const uint32_t q = std::min(shares, o->shares);
    state_[k].f[o->side == tf::Side::Buy ? kSellVol : kBuyVol] += q;  // aggressor is the other side
    state_[k].f[kTrades] += 1;
    state_[k].f[kLastPx] = px ? px : o->price;
  }

  void cancel(uint64_t ref, uint32_t shares) {
    const tf::Order* o = books_.order(ref);
    if (!o) return;
    const int k = slot(o->locate);
    if (k < 0) return;
    const uint32_t q = shares ? std::min(shares, o->shares) : o->shares;
    state_[k].f[o->side == tf::Side::Buy ? kCxlBid : kCxlAsk] += q;
  }

  void update_l1(uint16_t locate) {
    const int k = slot(locate);
    if (k < 0) return;
    const tf::Book& b = *books_.book(locate);
    SymState& s = state_[k];
    const int64_t bp = b.bids.empty() ? 0 : b.bids.best().price, bq = b.bids.empty() ? 0 : b.bids.best().qty;
    const int64_t ap = b.asks.empty() ? 0 : b.asks.best().price, aq = b.asks.empty() ? 0 : b.asks.best().qty;
    if (s.prev_bid_px && s.prev_ask_px && bp && ap) {
      int64_t e = 0;
      if (bp >= s.prev_bid_px) e += bq;
      if (bp <= s.prev_bid_px) e -= s.prev_bid_qty;
      if (ap <= s.prev_ask_px) e -= aq;
      if (ap >= s.prev_ask_px) e += s.prev_ask_qty;
      s.f[kOfi] += e;
    }
    s.prev_bid_px = bp, s.prev_bid_qty = bq, s.prev_ask_px = ap, s.prev_ask_qty = aq;
    s.f[kBidPx] = bp, s.f[kBidQty] = bq, s.f[kAskPx] = ap, s.f[kAskQty] = aq;
    s.f[kBidCount1] = b.bids.empty() ? 0 : b.bids.best().count;
    s.f[kAskCount1] = b.asks.empty() ? 0 : b.asks.best().count;
    int64_t d = 0;
    for (std::size_t i = 0; i < b.bids.size() && i < 5; ++i) d += b.bids.level(i).qty;
    s.f[kBidDepth5] = d;
    d = 0;
    for (std::size_t i = 0; i < b.asks.size() && i < 5; ++i) d += b.asks.level(i).qty;
    s.f[kAskDepth5] = d;
  }

  void emit() {
    for (const SymState& s : state_) std::fwrite(s.f, sizeof(int64_t), kFields, out_);
    next_ += step_;
    ++rows_;
  }

  std::vector<std::string> syms_;
  std::vector<int> slot_;
  std::vector<SymState> state_;
  uint64_t next_, end_, step_;
  std::FILE* out_;
  tf::MarketBooks books_;
  int32_t touched_ = tf::MarketBooks::kUnknown;
  uint64_t rows_ = 0;
};

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3) {
    std::fprintf(stderr, "usage: %s ITCH OUT --symbols A,B [--start-ns N] [--end-ns N] [--step-ns N]\n", argv[0]);
    return 2;
  }
  std::vector<std::string> syms;
  uint64_t start = 34'200ULL * 1'000'000'000ULL;  // 09:30:00
  uint64_t end = 57'600ULL * 1'000'000'000ULL;    // 16:00:00
  uint64_t step = 1'000'000'000ULL;
  for (int i = 3; i + 1 < argc; i += 2) {
    const std::string k = argv[i];
    if (k == "--symbols") {
      std::stringstream ss(argv[i + 1]);
      for (std::string s; std::getline(ss, s, ',');) syms.push_back(s);
    } else if (k == "--start-ns") {
      start = std::strtoull(argv[i + 1], nullptr, 10);
    } else if (k == "--end-ns") {
      end = std::strtoull(argv[i + 1], nullptr, 10);
    } else if (k == "--step-ns") {
      step = std::strtoull(argv[i + 1], nullptr, 10);
    }
  }
  if (syms.empty() || step == 0) return 2;
  std::FILE* in = std::fopen(argv[1], "rb");
  std::FILE* out = std::fopen(argv[2], "wb");
  if (!in || !out) {
    std::perror("open");
    return 1;
  }
  std::string hdr = "{\"symbols\": [";
  for (std::size_t i = 0; i < syms.size(); ++i) hdr += (i ? ", \"" : "\"") + syms[i] + "\"";
  hdr += "], \"fields\": [";
  for (int i = 0; i < kFields; ++i) hdr += std::string(i ? ", \"" : "\"") + kFieldNames[i] + "\"";
  hdr += "], \"start_ns\": " + std::to_string(start) + ", \"step_ns\": " + std::to_string(step) + "}\n";
  std::fwrite(hdr.data(), 1, hdr.size(), out);

  Extractor x(syms, start, end, step, out);
  std::vector<uint8_t> buf(1 << 24);
  std::size_t have = 0;
  for (;;) {
    const std::size_t got = std::fread(buf.data() + have, 1, buf.size() - have, in);
    have += got;
    const std::size_t used = x.feed(buf.data(), have);
    std::memmove(buf.data(), buf.data() + used, have - used);
    have -= used;
    if (got == 0) break;
  }
  x.finish();
  std::fclose(in);
  std::fclose(out);
  std::fprintf(stderr, "rows %llu x %zu symbols\n", (unsigned long long)x.rows(), syms.size());
  return 0;
}
