// Market-making strategies for the L3 backtester (M5, ADR 0010).
//
// AvellanedaStoikov: quotes around a reservation price that leans against inventory,
//   r     = mid + alpha_ticks * imb1 * tick - q * gamma * sigma^2 * tau
//   delta = gamma * sigma^2 * tau / 2 + ln(1 + gamma / k) / gamma
//   bid   = floor_tick(r - delta), ask = ceil_tick(r + delta), never crossing the opposite touch.
// sigma is an EWMA of squared 1-second mid changes (in $), tau is a fixed horizon in seconds (the
// "infinite horizon" variant; a closing-time horizon would make quotes explode near 16:00).
// alpha_ticks > 0 adds the M4 signal: L1 queue imbalance imb1 = (bq - aq) / (bq + aq) predicts
// the next mid move, so the strategy shades both quotes toward it (alpha_ticks = 0 is plain A-S).
//
// Risk layer: hard position limit (Sim::can_submit), quotes only during [start_ns, stop_ns), and a
// flatten window at the end that crosses the spread to go flat.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <utility>

#include "tickforge/sim.hpp"

namespace tf {

struct MMParams {
  double gamma = 0.1;        // risk aversion (1 / $ per share-squared units; tuned in M5)
  double k = 1.5;            // order-arrival decay per $ of distance (fills ~ exp(-k * delta))
  double tau_s = 60.0;       // horizon in seconds for the inventory term
  double alpha_ticks = 0.0;  // imbalance skew in ticks at |imb1| = 1
  uint32_t size = 100;
  uint32_t tick = 100;                       // ITCH price units ($0.01)
  uint64_t start_ns = 34'500'000'000'000ULL; // 09:35
  uint64_t stop_ns = 57'000'000'000'000ULL;  // 15:50 stop quoting
  uint64_t flat_ns = 57'300'000'000'000ULL;  // 15:55 flatten by
  double sigma_halflife_s = 60.0;
  uint64_t min_requote_ns = 1'000'000;       // 1 ms between requotes per side
  bool join_only = true;                     // quote at or behind the touch, never inside it
};

class AvellanedaStoikov : public Strategy {
 public:
  explicit AvellanedaStoikov(MMParams p) : p_(p) {}

  void on_market(Sim& s) override {
    const uint32_t bb = s.best_bid(), ba = s.best_ask();
    if (!bb || !ba || ba <= bb) return;
    const uint64_t now = s.now();
    update_sigma(now, 0.5 * (double(bb) + double(ba)) / 1e4);
    if (now < p_.start_ns) return;
    if (now >= p_.stop_ns) {
      wind_down(s, bb, ba, now);
      return;
    }
    const Book* b = s.book();
    const double bq = double(b->bids.best().qty), aq = double(b->asks.best().qty);
    const double imb = (bq - aq) / (bq + aq);
    auto [bid, ask] = quotes(p_, 0.5 * (double(bb) + double(ba)) / 1e4, double(s.position()), sigma2_, imb);
    if (p_.join_only) {  // never improve the touch: our quote would sit alone in front, unseen by
      bid = std::min(bid, bb);  // the real flow we are replaying (no-impact model, ADR 0006)
      ask = std::max(ask, ba);
    }
    if (bid >= ba) bid = ba - p_.tick;  // post-only safety: never cross on purpose
    if (ask <= bb) ask = bb + p_.tick;
    requote(s, bid_id_, last_bid_t_, Side::Buy, bid, now);
    requote(s, ask_id_, last_ask_t_, Side::Sell, ask, now);
  }

  double sigma_per_s() const { return std::sqrt(sigma2_); }

  // Pure quote rule (unit-tested): mid in $, q in shares, sigma2 in $^2/s; returns ITCH prices.
  static std::pair<uint32_t, uint32_t> quotes(const MMParams& p, double mid, double q, double sigma2, double imb) {
    const double var_tau = sigma2 * p.tau_s;
    const double tick = double(p.tick) / 1e4;
    const double r = mid + p.alpha_ticks * imb * tick - q * p.gamma * var_tau;
    const double delta = 0.5 * p.gamma * var_tau + std::log1p(p.gamma / p.k) / p.gamma;
    // Small epsilon keeps prices that sit exactly on a tick from flipping on float noise.
    return {to_px(std::floor((r - delta) / tick + 1e-9) * tick), to_px(std::ceil((r + delta) / tick - 1e-9) * tick)};
  }

 private:
  static uint32_t to_px(double dollars) {
    const double v = std::round(dollars * 1e4);
    return v > 0 ? static_cast<uint32_t>(v) : 0u;
  }

  void update_sigma(uint64_t now, double mid) {
    if (last_sec_ == 0) {
      last_sec_ = now / 1'000'000'000ULL;
      last_mid_ = mid;
      return;
    }
    const uint64_t sec = now / 1'000'000'000ULL;
    if (sec == last_sec_) return;
    const double dt = double(sec - last_sec_);
    const double d = mid - last_mid_;
    const double a = 1.0 - std::exp2(-dt / p_.sigma_halflife_s);
    const double obs = d * d / dt;
    sigma2_ = warm_ ? (1 - a) * sigma2_ + a * obs : obs;
    warm_ = true;
    last_sec_ = sec;
    last_mid_ = mid;
  }

  void requote(Sim& s, uint64_t& id, uint64_t& last_t, Side side, uint32_t px, uint64_t now) {
    if (id) {
      const SimOrder* live = nullptr;
      for (const auto& o : s.open_orders()) if (o.id == id) live = &o;
      if (s.in_flight(id) || s.pending_cancel(id)) return;
      if (live && live->price == px) return;
      if (!live) {  // filled or rejected
        id = 0;
      } else {
        if (now - last_t < p_.min_requote_ns) return;
        s.cancel(id);
        id = 0;
        return;  // place the replacement once the cancel is acknowledged (no double exposure)
      }
    }
    if (px == 0 || !s.can_submit(side, p_.size)) return;
    id = s.submit(side, px, p_.size, /*ioc=*/false, /*post_only=*/true);
    last_t = now;
  }

  void wind_down(Sim& s, uint32_t bb, uint32_t ba, uint64_t now) {
    if (!wound_) {
      s.cancel_all();
      bid_id_ = ask_id_ = 0;
      wound_ = true;
    }
    if (now < p_.flat_ns || s.position() == 0 || now - last_flat_t_ < 100'000'000ULL) return;
    for (const auto& o : s.open_orders()) if (o.qty) return;
    const int64_t q = s.position();
    const uint32_t qty = static_cast<uint32_t>(std::min<int64_t>(std::llabs(q), s.config().max_order_qty));
    // Aggressive IOC at the far touch; risk check is on worst-case exposure, which shrinks here.
    if (q > 0) s.submit(Side::Sell, bb, qty, /*ioc=*/true);
    else s.submit(Side::Buy, ba, qty, /*ioc=*/true);
    last_flat_t_ = now;
  }

  MMParams p_;
  uint64_t bid_id_ = 0, ask_id_ = 0, last_bid_t_ = 0, last_ask_t_ = 0, last_flat_t_ = 0;
  uint64_t last_sec_ = 0;
  double last_mid_ = 0, sigma2_ = 0;
  bool warm_ = false, wound_ = false;
};

}  // namespace tf
