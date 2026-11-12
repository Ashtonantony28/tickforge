// Event-driven backtester on top of the L3 book (M2, ADR 0006).
//
// The exchange book is rebuilt from ITCH exactly as in replay.hpp. A strategy sees the book after
// every message for its instrument and may submit/cancel *simulated* orders. Simulated orders never
// enter the exchange book (no market impact; documented in ADR 0006). Fills are modelled as:
//
//  * Aggressive (marketable on arrival): walk the opposite side's visible levels up to the limit
//    price, filling against displayed size; taker fee per share.
//  * Passive: the order joins the back of its price level. Because the book is L3, queue position is
//    exact: `ahead` = shares resting at that price when we joined. Executions/cancels/deletes of
//    orders that arrived before us reduce `ahead`. An execution of an order that arrived *after* us
//    at our price means the queue reached us: we fill first, up to the executed size. An execution
//    at a worse price on our side, or the opposite side moving to our price, fills us fully
//    (trade-through). Passive fills earn the maker rebate.
//  * Latency: every submit/cancel reaches the simulated exchange `latency_ns` after the decision
//    (plus seeded uniform jitter); market events in between are applied first.
//
// All money is integer: prices in ITCH units (1e-4 $), fees in 1e-4 $ per share, cash in 1e-4 $.
// No wall clock, seeded RNG only, so a run is bit-reproducible; `fill_digest()` proves it.
#pragma once
#include <algorithm>
#include <cstdint>
#include <deque>
#include <limits>
#include <random>
#include <vector>

#include "tickforge/digest.hpp"
#include "tickforge/itch.hpp"
#include "tickforge/order_book.hpp"

namespace tf {

struct SimConfig {
  uint16_t locate = 1;          // instrument to trade
  int64_t latency_ns = 20'000;  // decision -> exchange arrival
  int64_t jitter_ns = 0;        // uniform extra latency in [0, jitter_ns]
  int64_t maker_fee = -20;      // per share, 1e-4 $ (negative = rebate): -$0.0020
  int64_t taker_fee = 30;       // per share, 1e-4 $: $0.0030
  int64_t max_position = 1000;  // absolute shares; orders that could breach it are rejected
  uint32_t max_order_qty = 1000;
  int64_t sample_ns = 1'000'000'000;  // P&L sample interval
  uint64_t seed = 1;
};

struct Fill {
  uint64_t ts;
  uint64_t order_id;
  Side side;
  uint32_t price;
  uint32_t qty;
  bool maker;
  int64_t fee;  // total fee for this fill, 1e-4 $
};

struct PnlSample {
  uint64_t ts;
  int64_t position;
  int64_t cash;
  uint32_t mid2;   // bid + ask (twice the mid, avoids halves); 0 if one side empty
  int64_t equity;  // cash + position * mid, 1e-4 $ (mid rounded toward zero)
};

struct SimOrder {
  uint64_t id;
  Side side;
  uint32_t price;
  uint32_t qty;   // remaining
  uint64_t ahead; // shares ahead of us in the queue
  uint64_t join_seq;
  bool resting;
};

struct SimStats {
  uint64_t submitted = 0, rejected = 0, cancelled = 0, cancel_rejects = 0;
  uint64_t post_only_rejects = 0;  // post-only order would have crossed on arrival
  uint64_t fills = 0, maker_fills = 0, taker_fills = 0;
  uint64_t shares_bought = 0, shares_sold = 0;
};

class Sim;

class Strategy {
 public:
  virtual ~Strategy() = default;
  virtual void on_market(Sim& sim) = 0;            // after every message for the instrument
  virtual void on_fill(Sim& /*sim*/, const Fill& /*f*/) {}
};

class Sim : public itch::NullHandler {
 public:
  Sim(SimConfig cfg, Strategy* strategy, std::size_t expected_orders = 1 << 20)
      : cfg_(cfg), strat_(strategy), books_(expected_orders), rng_(cfg.seed) {}
  Sim(const Sim&) = delete;
  Sim& operator=(const Sim&) = delete;

  // ---- driving ------------------------------------------------------------------------------
  std::size_t feed(const uint8_t* data, std::size_t n) { return itch::parse(data, n, *this).bytes_consumed; }
  // Apply any actions still in flight and take a final sample.
  void finish() {
    apply_pending(std::numeric_limits<uint64_t>::max());
    sample(now_);
  }

  // ---- strategy API -------------------------------------------------------------------------
  uint64_t now() const { return now_; }
  const Book* book() const { return books_.book(cfg_.locate); }
  uint32_t best_bid() const { auto b = book(); return b && !b->bids.empty() ? b->bids.best().price : 0; }
  uint32_t best_ask() const { auto b = book(); return b && !b->asks.empty() ? b->asks.best().price : 0; }
  int64_t position() const { return position_; }
  const SimConfig& config() const { return cfg_; }
  // True if a new order of `qty` on side `s` would pass the risk checks right now.
  bool can_submit(Side s, uint32_t qty) const {
    return qty > 0 && qty <= cfg_.max_order_qty && exposure(s) + int64_t{qty} <= cfg_.max_position;
  }
  int64_t cash() const { return cash_; }
  const std::vector<SimOrder>& open_orders() const { return open_; }
  // Worst-case position on side `s` if every resting and in-flight order on that side filled
  // (orders on the other side are not netted: they may never fill).
  int64_t exposure(Side s) const {
    int64_t e = s == Side::Buy ? position_ : -position_;
    for (const auto& o : open_)
      if (o.side == s) e += o.qty;
    for (const auto& a : pending_)
      if (a.kind == Action::Submit && a.side == s) e += a.qty;
    return e;
  }

  // Submit a limit order. ioc: cancel any unfilled remainder on arrival. post_only: reject on
  // arrival instead of taking liquidity (stale quotes after latency). Returns the order id, or 0
  // if rejected by risk checks.
  uint64_t submit(Side side, uint32_t price, uint32_t qty, bool ioc = false, bool post_only = false) {
    if (price == 0 || !can_submit(side, qty)) {
      ++stats_.rejected;
      return 0;
    }
    ++stats_.submitted;
    const uint64_t id = next_id_++;
    pending_.push_back(Action{arrival(), Action::Submit, id, side, price, qty, ioc, post_only});
    return id;
  }
  void cancel(uint64_t id) { pending_.push_back(Action{arrival(), Action::Cancel, id, Side::Buy, 0, 0, false, false}); }
  void cancel_all() {
    for (const auto& o : open_) cancel(o.id);
  }
  bool pending_cancel(uint64_t id) const {
    for (const auto& a : pending_) if (a.kind == Action::Cancel && a.id == id) return true;
    return false;
  }
  bool in_flight(uint64_t id) const {
    for (const auto& a : pending_) if (a.kind == Action::Submit && a.id == id) return true;
    return false;
  }

  // ---- results ------------------------------------------------------------------------------
  const std::vector<Fill>& fills() const { return fills_; }
  const std::vector<PnlSample>& samples() const { return samples_; }
  const SimStats& stats() const { return stats_; }
  uint64_t fill_digest() const { return digest_.h; }
  int64_t fees_paid() const { return fees_; }
  int64_t equity() const { return equity_at(mid2()); }
  const MarketBooks& books() const { return books_; }
  uint64_t messages() const { return messages_; }

  // ---- ITCH handler -------------------------------------------------------------------------
  void on_add(const itch::AddOrder& a) {
    begin(a.h);
    touched_ = books_.add(a.ref, a.h.locate, a.side == 'B' ? Side::Buy : Side::Sell, a.shares, a.price);
  }
  void on_executed(const itch::OrderExecuted& e) { begin(e.h); removal(e.ref, e.shares, true); }
  void on_executed_price(const itch::OrderExecutedPrice& e) { begin(e.h); removal(e.ref, e.shares, true); }
  void on_cancel(const itch::OrderCancel& x) { begin(x.h); removal(x.ref, x.shares, false); }
  void on_delete(const itch::OrderDelete& d) {
    begin(d.h);
    removal(d.ref, std::numeric_limits<uint32_t>::max(), false);
  }
  void on_replace(const itch::OrderReplace& u) {
    begin(u.h);
    // The old order leaves its queue entirely; the new one joins behind us.
    removal(u.old_ref, std::numeric_limits<uint32_t>::max(), false, /*apply=*/false);
    touched_ = books_.replace(u.old_ref, u.new_ref, u.shares, u.price);
  }
  void on_message_end() {
    ++messages_;
    if (touched_ == cfg_.locate) {
      check_crossed();
      if (strat_) strat_->on_market(*this);
    }
    touched_ = MarketBooks::kUnknown;
  }

 private:
  struct Action {
    uint64_t at;
    enum Kind : uint8_t { Submit, Cancel } kind;
    uint64_t id;
    Side side;
    uint32_t price;
    uint32_t qty;
    bool ioc;
    bool post_only;
  };

  uint64_t arrival() {
    int64_t lat = cfg_.latency_ns;
    if (cfg_.jitter_ns > 0) lat += static_cast<int64_t>(rng_() % static_cast<uint64_t>(cfg_.jitter_ns + 1));
    return now_ + static_cast<uint64_t>(lat);
  }

  void begin(const itch::Header& h) {
    if (h.timestamp > now_) {
      apply_pending(h.timestamp);
      while (next_sample_ != 0 && h.timestamp >= next_sample_) {
        sample(next_sample_);
        next_sample_ += static_cast<uint64_t>(cfg_.sample_ns);
      }
      if (next_sample_ == 0) next_sample_ = h.timestamp + static_cast<uint64_t>(cfg_.sample_ns);
      now_ = h.timestamp;
    }
  }

  // Actions are queued in decision order; arrival times are non-decreasing without jitter. With
  // jitter we still apply in arrival order (stable for ties).
  void apply_pending(uint64_t upto) {
    while (!pending_.empty()) {
      auto it = std::min_element(pending_.begin(), pending_.end(),
                                 [](const Action& a, const Action& b) { return a.at < b.at; });
      if (it->at > upto) break;
      const Action a = *it;
      pending_.erase(it);
      const uint64_t saved = now_;
      now_ = std::max(now_, a.at);
      if (a.kind == Action::Submit) arrive(a); else do_cancel(a.id);
      now_ = std::max(saved, now_);
    }
  }

  void arrive(const Action& a) {
    SimOrder o{a.id, a.side, a.price, a.qty, 0, 0, false};
    const Book* b = book();
    if (b && a.post_only) {
      const uint32_t opp = a.side == Side::Buy ? best_ask() : best_bid();
      if (opp && (a.side == Side::Buy ? opp <= a.price : opp >= a.price)) {
        ++stats_.post_only_rejects;
        return;
      }
    }
    if (b) {  // aggressive part: walk visible opposite levels
      const BookSide& opp = a.side == Side::Buy ? b->asks : b->bids;
      for (std::size_t i = 0; i < opp.size() && o.qty > 0; ++i) {
        const Level& lv = opp.level(i);
        const bool marketable = a.side == Side::Buy ? lv.price <= a.price : lv.price >= a.price;
        if (!marketable) break;
        const uint32_t q = static_cast<uint32_t>(std::min<uint64_t>(o.qty, lv.qty));
        fill(o, lv.price, q, /*maker=*/false);
      }
    }
    if (o.qty == 0 || a.ioc) return;
    o.resting = true;
    o.join_seq = books_.next_seq();
    if (b) {
      const BookSide& own = a.side == Side::Buy ? b->bids : b->asks;
      const std::size_t i = own.find(a.price);
      o.ahead = i < own.size() ? own.at(i).qty : 0;
    }
    open_.push_back(o);
  }

  void do_cancel(uint64_t id) {
    for (std::size_t i = 0; i < open_.size(); ++i)
      if (open_[i].id == id) {
        open_.erase(open_.begin() + static_cast<std::ptrdiff_t>(i));
        ++stats_.cancelled;
        return;
      }
    ++stats_.cancel_rejects;  // already filled (or never rested)
  }

  // An exchange order loses `shares` (UINT32_MAX = all) by execution or cancel.
  void removal(uint64_t ref, uint32_t shares, bool is_exec, bool apply = true) {
    const Order* ex = books_.order(ref);
    if (ex && ex->locate == cfg_.locate && !open_.empty()) {
      const uint32_t k = std::min(shares, ex->shares);
      for (std::size_t i = 0; i < open_.size();) {
        SimOrder& o = open_[i];
        bool done = false;
        if (o.side == ex->side) {
          const bool same = o.price == ex->price;
          const bool worse = o.side == Side::Buy ? ex->price < o.price : ex->price > o.price;
          if (same && ex->seq < o.join_seq) {
            o.ahead = o.ahead > k ? o.ahead - k : 0;
          } else if (same && is_exec) {  // queue reached an order behind us
            o.ahead = 0;
            const uint32_t q = std::min(o.qty, k);
            done = fill(o, o.price, q, true);
          } else if (worse && is_exec) {  // traded through our price
            done = fill(o, o.price, o.qty, true);
          }
        }
        if (done) open_.erase(open_.begin() + static_cast<std::ptrdiff_t>(i)); else ++i;
      }
    }
    if (apply) touched_ = books_.reduce_or_remove(ref, shares, is_exec);
  }

  // The opposite side arriving at our resting price means a counterparty would have hit us.
  void check_crossed() {
    const uint32_t bid = best_bid(), ask = best_ask();
    for (std::size_t i = 0; i < open_.size();) {
      SimOrder& o = open_[i];
      const bool crossed = o.side == Side::Buy ? (ask != 0 && ask <= o.price) : (bid != 0 && bid >= o.price);
      if (crossed && fill(o, o.price, o.qty, true)) open_.erase(open_.begin() + static_cast<std::ptrdiff_t>(i));
      else ++i;
    }
  }

  // Returns true when the order is fully filled.
  bool fill(SimOrder& o, uint32_t price, uint32_t qty, bool maker) {
    if (qty == 0) return o.qty == 0;
    const int64_t fee = (maker ? cfg_.maker_fee : cfg_.taker_fee) * int64_t{qty};
    const int64_t notional = int64_t{price} * int64_t{qty};
    if (o.side == Side::Buy) {
      position_ += qty;
      cash_ -= notional;
      stats_.shares_bought += qty;
    } else {
      position_ -= qty;
      cash_ += notional;
      stats_.shares_sold += qty;
    }
    cash_ -= fee;
    fees_ += fee;
    o.qty -= qty;
    ++stats_.fills;
    ++(maker ? stats_.maker_fills : stats_.taker_fills);
    const Fill f{now_, o.id, o.side, price, qty, maker, fee};
    fills_.push_back(f);
    digest_.word(f.ts);
    digest_.word(f.order_id);
    digest_.word(static_cast<uint64_t>(f.side));
    digest_.word(f.price);
    digest_.word(f.qty);
    digest_.word(f.maker);
    if (strat_) strat_->on_fill(*this, f);
    return o.qty == 0;
  }

  uint32_t mid2() const {
    const uint32_t b = best_bid(), a = best_ask();
    return (b && a) ? b + a : 0;
  }
  int64_t equity_at(uint32_t m2) const { return cash_ + position_ * int64_t{m2} / 2; }
  void sample(uint64_t ts) {
    const uint32_t m2 = mid2();
    samples_.push_back(PnlSample{ts, position_, cash_, m2, m2 ? equity_at(m2) : cash_});
  }

  SimConfig cfg_;
  Strategy* strat_;
  MarketBooks books_;
  std::mt19937_64 rng_;
  std::vector<SimOrder> open_;
  std::deque<Action> pending_;
  std::vector<Fill> fills_;
  std::vector<PnlSample> samples_;
  SimStats stats_;
  Digest digest_;
  uint64_t now_ = 0;
  uint64_t next_sample_ = 0;
  uint64_t next_id_ = 1;
  uint64_t messages_ = 0;
  int64_t position_ = 0;
  int64_t cash_ = 0;
  int64_t fees_ = 0;
  int32_t touched_ = MarketBooks::kUnknown;
};

// Reference strategy: join the touch on both sides with fixed size, requote when the touch moves,
// stop quoting the side that would breach the inventory limit. Used in tests and as the baseline
// the M5 market makers are compared against.
class TouchQuoter : public Strategy {
 public:
  explicit TouchQuoter(uint32_t size = 100) : size_(size) {}
  void on_market(Sim& s) override {
    const uint32_t bid = s.best_bid(), ask = s.best_ask();
    if (!bid || !ask) return;
    requote(s, bid_id_, Side::Buy, bid);
    requote(s, ask_id_, Side::Sell, ask);
  }

 private:
  void requote(Sim& s, uint64_t& id, Side side, uint32_t px) {
    if (id) {
      const SimOrder* live = nullptr;
      for (const auto& o : s.open_orders()) if (o.id == id) live = &o;
      if (s.in_flight(id) || (live && live->price == px) || s.pending_cancel(id)) return;
      if (live) s.cancel(id);
      id = 0;
    }
    if (!s.can_submit(side, size_)) return;  // inventory limit: stop quoting this side
    id = s.submit(side, px, size_, /*ioc=*/false, /*post_only=*/true);
  }
  uint32_t size_;
  uint64_t bid_id_ = 0, ask_id_ = 0;
};

}  // namespace tf
