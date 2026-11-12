#include <map>
#include <random>

#include "tf_test.hpp"
#include "tickforge/l2_book.hpp"

TF_TEST(l2_side_order_and_delete) {
  tf::L2Side bids(true), asks(false);
  bids.set(100, 5);
  bids.set(102, 7);
  bids.set(101, 1);
  TF_CHECK_EQ(bids.level(0).price, int64_t{102});
  TF_CHECK_EQ(bids.level(2).price, int64_t{100});
  TF_CHECK_EQ(bids.set(102, 0), int64_t{7});
  TF_CHECK_EQ(bids.level(0).price, int64_t{101});
  TF_CHECK_EQ(bids.set(999, 0), int64_t{0});  // deleting an absent level is a no-op
  TF_CHECK_EQ(bids.size(), std::size_t{2});
  asks.set(105, 2);
  asks.set(103, 4);
  TF_CHECK_EQ(asks.level(0).price, int64_t{103});
  TF_CHECK_EQ(asks.qty_at(105), int64_t{2});
}

// Differential test against std::map for both sides.
TF_TEST(l2_side_vs_map) {
  std::mt19937_64 rng(99);
  for (int s = 0; s < 2; ++s) {
    const bool is_bid = s == 0;
    tf::L2Side side(is_bid);
    std::map<int64_t, int64_t> ref;
    for (int i = 0; i < 200000; ++i) {
      const int64_t px = static_cast<int64_t>(rng() % 300);
      const int64_t q = (rng() % 3 == 0) ? 0 : static_cast<int64_t>(rng() % 1000 + 1);
      const int64_t prev = ref.count(px) ? ref[px] : 0;
      TF_CHECK_EQ(side.set(px, q), prev);
      if (q == 0) ref.erase(px); else ref[px] = q;
      if (i % 997 == 0) {
        TF_CHECK_EQ(side.size(), ref.size());
        std::size_t k = 0;
        if (is_bid) {
          for (auto it = ref.rbegin(); it != ref.rend(); ++it, ++k) TF_CHECK_EQ(side.level(k).price, it->first);
        } else {
          for (auto it = ref.begin(); it != ref.end(); ++it, ++k) TF_CHECK_EQ(side.level(k).price, it->first);
        }
      }
    }
  }
}

TF_TEST(l2_replayer_counts_crossed_and_bad) {
  tf::L2Replayer r;
  auto ev = [](uint8_t k, int64_t p, int64_t q, uint64_t s) {
    tf::L2Event e{};
    e.kind = k; e.price = p; e.qty = q; e.seq = s;
    return e;
  };
  r.apply(ev(1, 100, 1, 0));
  r.apply(ev(2, 101, 1, 0));
  r.apply(ev(3, 0, 0, 1));
  r.apply(ev(1, 102, 1, 0));  // bid through the ask
  r.apply(ev(3, 0, 0, 2));
  r.apply(ev(9, 0, 0, 0));
  TF_CHECK_EQ(r.batches(), uint64_t{2});
  TF_CHECK_EQ(r.crossed(), uint64_t{1});
  TF_CHECK_EQ(r.bad(), uint64_t{1});
}
