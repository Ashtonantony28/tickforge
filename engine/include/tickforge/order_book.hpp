// L3 order book for many instruments (ITCH stock locate codes).
//
// Layout (ADR 0005):
//  * Orders live in one pool (vector + free list); a FlatMap64 maps order ref -> pool index.
//  * Each instrument has two BookSides. A side is a vector of price levels sorted worst -> best,
//    so the touch is at the back: activity near the touch touches the vector's tail, and adding
//    or removing a level near the touch moves few elements.
//  * Each level keeps a FIFO (intrusive doubly linked list through the pool) of its orders, so
//    queue position is available to the backtester (M2) without a second structure.
#pragma once
#include <algorithm>
#include <cstdint>
#include <vector>

#include "tickforge/flat_map.hpp"

namespace tf {

enum class Side : uint8_t { Buy = 0, Sell = 1 };
inline constexpr uint32_t kNil = ~uint32_t{0};

struct Level {
  uint32_t price;
  uint32_t count;  // live orders at this level
  uint64_t qty;    // total shares at this level
  uint32_t head;   // oldest order (front of queue)
  uint32_t tail;   // newest order
};

struct Order {
  uint64_t ref;
  uint32_t price;
  uint32_t shares;
  uint32_t prev;
  uint32_t next;
  uint64_t seq;  // arrival sequence (global add counter): exact queue position for the backtester
  uint16_t locate;
  Side side;
};

class BookSide {
 public:
  explicit BookSide(bool is_bid) : is_bid_(is_bid) {}

  bool is_bid() const { return is_bid_; }
  bool empty() const { return levels_.empty(); }
  std::size_t size() const { return levels_.size(); }
  const Level& best() const { return levels_.back(); }
  // i-th level from the touch (0 = best). Precondition: i < size().
  const Level& level(std::size_t i) const { return levels_[levels_.size() - 1 - i]; }

  // Strictly-better comparison in this side's direction.
  bool better(uint32_t a, uint32_t b) const { return is_bid_ ? a > b : a < b; }

  // Index of the level with `price`, inserting an empty one if absent.
  std::size_t find_or_insert(uint32_t price) {
    const std::size_t i = lower(price);
    if (i < levels_.size() && levels_[i].price == price) return i;
    levels_.insert(levels_.begin() + static_cast<std::ptrdiff_t>(i), Level{price, 0, 0, kNil, kNil});
    return i;
  }
  // Index of the level with `price`, or size() if absent.
  std::size_t find(uint32_t price) const {
    const std::size_t i = lower(price);
    return (i < levels_.size() && levels_[i].price == price) ? i : levels_.size();
  }
  Level& at(std::size_t i) { return levels_[i]; }
  const Level& at(std::size_t i) const { return levels_[i]; }
  void erase(std::size_t i) { levels_.erase(levels_.begin() + static_cast<std::ptrdiff_t>(i)); }

 private:
  // First index whose level is not strictly worse than `price` (levels are worst -> best).
  std::size_t lower(uint32_t price) const {
    // Fast path: most activity is at or near the touch.
    const std::size_t n = levels_.size();
    if (n == 0 || better(price, levels_[n - 1].price)) return n;
    if (levels_[n - 1].price == price) return n - 1;
    auto it = std::partition_point(levels_.begin(), levels_.end(),
                                   [&](const Level& l) { return better(price, l.price); });
    return static_cast<std::size_t>(it - levels_.begin());
  }

  std::vector<Level> levels_;
  bool is_bid_;
};

struct Book {
  BookSide bids{true};
  BookSide asks{false};
  BookSide& side(Side s) { return s == Side::Buy ? bids : asks; }
  const BookSide& side(Side s) const { return s == Side::Buy ? bids : asks; }
};

struct BookStats {
  uint64_t adds = 0;
  uint64_t executions = 0;
  uint64_t cancels = 0;
  uint64_t deletes = 0;
  uint64_t replaces = 0;
  uint64_t unknown_ref = 0;  // modify/delete for an order we do not hold
  uint64_t duplicate_ref = 0;
  uint64_t overfill = 0;     // execute/cancel larger than remaining shares (clamped)
  uint64_t zero_add = 0;     // add with 0 shares (ignored)
};

class MarketBooks {
 public:
  static constexpr int32_t kUnknown = -1;

  explicit MarketBooks(std::size_t expected_orders = 1 << 16) : index_(expected_orders * 2) {
    pool_.reserve(expected_orders);
  }

  // All mutators return the affected locate, or kUnknown if the message was rejected.
  int32_t add(uint64_t ref, uint16_t locate, Side side, uint32_t shares, uint32_t price) {
    if (shares == 0) {
      ++stats_.zero_add;
      return kUnknown;
    }
    const uint32_t idx = alloc();
    if (!index_.insert(ref, idx)) {
      free_slot(idx);
      ++stats_.duplicate_ref;
      return kUnknown;
    }
    Order& o = pool_[idx];
    o = Order{ref, price, shares, kNil, kNil, next_seq_++, locate, side};
    if (locate >= books_.size()) books_.resize(std::size_t{locate} + 1);
    BookSide& bs = books_[locate].side(side);
    Level& lv = bs.at(bs.find_or_insert(price));
    o.prev = lv.tail;
    if (lv.tail != kNil) pool_[lv.tail].next = idx; else lv.head = idx;
    lv.tail = idx;
    ++lv.count;
    lv.qty += shares;
    ++stats_.adds;
    return locate;
  }

  // Execution or cancel of `shares` from an order; removes it when exhausted.
  int32_t reduce(uint64_t ref, uint32_t shares, bool is_execution) {
    const uint32_t* p = index_.find(ref);
    if (!p) {
      ++stats_.unknown_ref;
      return kUnknown;
    }
    const uint32_t idx = *p;
    Order& o = pool_[idx];
    if (shares > o.shares) {
      ++stats_.overfill;
      shares = o.shares;
    }
    if (is_execution) ++stats_.executions; else ++stats_.cancels;
    const uint16_t locate = o.locate;
    if (shares == o.shares) {
      unlink_and_free(idx);
    } else {
      BookSide& bs = books_[locate].side(o.side);
      bs.at(bs.find(o.price)).qty -= shares;
      o.shares -= shares;
    }
    return locate;
  }

  // Delete when shares == UINT32_MAX, otherwise reduce (used by the backtester's single entry point).
  int32_t reduce_or_remove(uint64_t ref, uint32_t shares, bool is_execution) {
    return shares == ~uint32_t{0} ? remove(ref) : reduce(ref, shares, is_execution);
  }

  int32_t remove(uint64_t ref) {
    const uint32_t* p = index_.find(ref);
    if (!p) {
      ++stats_.unknown_ref;
      return kUnknown;
    }
    const uint16_t locate = pool_[*p].locate;
    unlink_and_free(*p);
    ++stats_.deletes;
    return locate;
  }

  // ITCH 'U': the old order is removed and the new one joins the back of the queue (loses priority).
  int32_t replace(uint64_t old_ref, uint64_t new_ref, uint32_t shares, uint32_t price) {
    const uint32_t* p = index_.find(old_ref);
    if (!p) {
      ++stats_.unknown_ref;
      return kUnknown;
    }
    const Order o = pool_[*p];
    unlink_and_free(*p);
    ++stats_.replaces;
    --stats_.adds;  // add() below counts one; a replace is not an add in the stats
    const int32_t r = add(new_ref, o.locate, o.side, shares, price);
    if (r == kUnknown) ++stats_.adds;  // add() rejected, undo the compensation
    return o.locate;
  }

  const Book* book(uint16_t locate) const { return locate < books_.size() ? &books_[locate] : nullptr; }
  std::size_t num_books() const { return books_.size(); }
  const Order* order(uint64_t ref) const {
    const uint32_t* p = index_.find(ref);
    return p ? &pool_[*p] : nullptr;
  }
  const Order& order_at(uint32_t idx) const { return pool_[idx]; }
  std::size_t live_orders() const { return index_.size(); }
  const BookStats& stats() const { return stats_; }
  // Sequence number the next added order will receive; anything already resting has a smaller one.
  uint64_t next_seq() const { return next_seq_; }

 private:
  uint32_t alloc() {
    if (free_head_ != kNil) {
      const uint32_t i = free_head_;
      free_head_ = pool_[i].next;
      return i;
    }
    pool_.push_back(Order{});
    return static_cast<uint32_t>(pool_.size() - 1);
  }
  void free_slot(uint32_t i) {
    pool_[i].next = free_head_;
    free_head_ = i;
  }

  void unlink_and_free(uint32_t idx) {
    Order& o = pool_[idx];
    BookSide& bs = books_[o.locate].side(o.side);
    const std::size_t li = bs.find(o.price);
    Level& lv = bs.at(li);
    if (o.prev != kNil) pool_[o.prev].next = o.next; else lv.head = o.next;
    if (o.next != kNil) pool_[o.next].prev = o.prev; else lv.tail = o.prev;
    lv.qty -= o.shares;
    if (--lv.count == 0) bs.erase(li);
    index_.erase(o.ref);
    free_slot(idx);
  }

  std::vector<Book> books_;
  std::vector<Order> pool_;
  uint32_t free_head_ = kNil;
  uint64_t next_seq_ = 0;
  FlatMap64 index_;
  BookStats stats_;
};

}  // namespace tf
