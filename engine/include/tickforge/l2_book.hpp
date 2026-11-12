// L2 (price-level aggregated) book mode for crypto feeds (M3, ADR 0008).
//
// Venues such as Binance publish absolute level quantities ("price p now has qty q", q == 0 deletes),
// not individual orders, so this book stores one quantity per price. Prices and quantities are
// fixed-point int64 at kL2Scale (1e-8), parsed exactly from the venue's decimal strings, so no
// floating point ever touches book state.
//
// Layout: one sorted vector per side with the best price at the back (bids ascending, asks
// descending). Updates near the touch are O(1)-ish memmoves, the whole book is contiguous, and
// after reserve() the steady state does no heap allocation.
//
// The event stream (L2Event, 32 bytes, little-endian) is produced by python/tickforge/l2.py from a
// raw recording after sequence-number sync; the digest is shared bit-for-bit with the Python
// reference (L2Book.digest there).
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <vector>

#include "tickforge/digest.hpp"

namespace tf {

inline constexpr int64_t kL2Scale = 100000000;  // 1e8: covers Binance tick/step sizes exactly

struct L2Level {
  int64_t price;
  int64_t qty;
};

class L2Side {
 public:
  explicit L2Side(bool is_bid) : bid_(is_bid) {}
  void reserve(std::size_t n) { lv_.reserve(n); }
  void clear() { lv_.clear(); }
  std::size_t size() const { return lv_.size(); }
  bool empty() const { return lv_.empty(); }
  // i = 0 is the best level.
  const L2Level& level(std::size_t i) const { return lv_[lv_.size() - 1 - i]; }

  // Returns the previous quantity at price (0 if absent).
  int64_t set(int64_t price, int64_t qty) {
    auto it = std::lower_bound(lv_.begin(), lv_.end(), price, [this](const L2Level& l, int64_t p) {
      return bid_ ? l.price < p : l.price > p;
    });
    const bool found = it != lv_.end() && it->price == price;
    const int64_t prev = found ? it->qty : 0;
    if (qty == 0) {
      if (found) lv_.erase(it);
    } else if (found) {
      it->qty = qty;
    } else {
      lv_.insert(it, L2Level{price, qty});
    }
    return prev;
  }

  int64_t qty_at(int64_t price) const {
    auto it = std::lower_bound(lv_.begin(), lv_.end(), price, [this](const L2Level& l, int64_t p) {
      return bid_ ? l.price < p : l.price > p;
    });
    return (it != lv_.end() && it->price == price) ? it->qty : 0;
  }

 private:
  bool bid_;
  std::vector<L2Level> lv_;
};

struct L2Book {
  L2Side bids{true};
  L2Side asks{false};
  void clear() {
    bids.clear();
    asks.clear();
  }
  bool crossed() const {
    return !bids.empty() && !asks.empty() && bids.level(0).price >= asks.level(0).price;
  }
};

// Wire format of the synced event stream.
enum class L2Kind : uint8_t {
  Clear = 0,  // snapshot start: drop all levels
  Bid = 1,    // set bid level
  Ask = 2,    // set ask level
  Batch = 3,  // end of one venue update (seq = final update id); digest is folded here
};

#pragma pack(push, 1)
struct L2Event {
  uint8_t kind;
  uint8_t pad[7];
  int64_t price;
  int64_t qty;
  uint64_t seq;
};
#pragma pack(pop)
static_assert(sizeof(L2Event) == 32, "L2Event wire size");

class L2Replayer {
 public:
  explicit L2Replayer(std::size_t reserve_levels = 8192) {
    book_.bids.reserve(reserve_levels);
    book_.asks.reserve(reserve_levels);
  }

  // Consumes whole 32-byte events; returns bytes used.
  std::size_t feed(const uint8_t* p, std::size_t n) {
    std::size_t used = 0;
    while (n - used >= sizeof(L2Event)) {
      L2Event e;
      std::memcpy(&e, p + used, sizeof e);
      apply(e);
      used += sizeof e;
    }
    return used;
  }

  void apply(const L2Event& e) {
    ++events_;
    switch (static_cast<L2Kind>(e.kind)) {
      case L2Kind::Clear:
        book_.clear();
        break;
      case L2Kind::Bid:
        book_.bids.set(e.price, e.qty);
        break;
      case L2Kind::Ask:
        book_.asks.set(e.price, e.qty);
        break;
      case L2Kind::Batch:
        ++batches_;
        if (book_.crossed()) ++crossed_;
        fold(e.seq);
        break;
      default:
        ++bad_;
    }
  }

  const L2Book& book() const { return book_; }
  uint64_t digest() const { return dg_.h; }
  uint64_t events() const { return events_; }
  uint64_t batches() const { return batches_; }
  uint64_t crossed() const { return crossed_; }
  uint64_t bad() const { return bad_; }

 private:
  void fold(uint64_t seq) {
    dg_.word(seq);
    for (const L2Side* s : {&book_.bids, &book_.asks}) {
      if (s->empty()) {
        dg_.word(0);
        dg_.word(0);
      } else {
        dg_.word(static_cast<uint64_t>(s->level(0).price));
        dg_.word(static_cast<uint64_t>(s->level(0).qty));
      }
    }
  }

  L2Book book_;
  Digest dg_;
  uint64_t events_ = 0, batches_ = 0, crossed_ = 0, bad_ = 0;
};

}  // namespace tf
