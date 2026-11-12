#include "tf_test.hpp"
#include "tickforge/order_book.hpp"

using tf::MarketBooks;
using tf::Side;

TF_TEST(book_add_levels_sorted_best_first) {
  MarketBooks m;
  m.add(1, 3, Side::Buy, 100, 1000);
  m.add(2, 3, Side::Buy, 200, 1010);
  m.add(3, 3, Side::Buy, 50, 990);
  m.add(4, 3, Side::Buy, 25, 1010);
  m.add(5, 3, Side::Sell, 70, 1030);
  m.add(6, 3, Side::Sell, 80, 1020);
  const auto& b = *m.book(3);
  TF_CHECK_EQ(b.bids.size(), std::size_t{3});
  TF_CHECK_EQ(b.bids.level(0).price, uint32_t{1010});
  TF_CHECK_EQ(b.bids.level(0).qty, uint64_t{225});
  TF_CHECK_EQ(b.bids.level(0).count, uint32_t{2});
  TF_CHECK_EQ(b.bids.level(1).price, uint32_t{1000});
  TF_CHECK_EQ(b.bids.level(2).price, uint32_t{990});
  TF_CHECK_EQ(b.asks.level(0).price, uint32_t{1020});
  TF_CHECK_EQ(b.asks.level(1).price, uint32_t{1030});
}

TF_TEST(book_fifo_queue_order) {
  MarketBooks m;
  m.add(10, 1, Side::Sell, 100, 500);
  m.add(11, 1, Side::Sell, 100, 500);
  m.add(12, 1, Side::Sell, 100, 500);
  m.remove(11);
  const auto& lv = m.book(1)->asks.level(0);
  TF_CHECK_EQ(m.order_at(lv.head).ref, uint64_t{10});
  TF_CHECK_EQ(m.order_at(lv.tail).ref, uint64_t{12});
  TF_CHECK_EQ(m.order_at(m.order_at(lv.head).next).ref, uint64_t{12});
}

TF_TEST(book_reduce_partial_full_and_overfill) {
  MarketBooks m;
  m.add(1, 1, Side::Buy, 300, 100);
  TF_CHECK_EQ(m.reduce(1, 100, true), 1);
  TF_CHECK_EQ(m.book(1)->bids.best().qty, uint64_t{200});
  TF_CHECK_EQ(m.reduce(1, 500, false), 1);  // over-cancel clamps and removes
  TF_CHECK(m.book(1)->bids.empty());
  TF_CHECK_EQ(m.stats().overfill, uint64_t{1});
  TF_CHECK_EQ(m.reduce(1, 1, true), MarketBooks::kUnknown);
  TF_CHECK_EQ(m.stats().unknown_ref, uint64_t{1});
  TF_CHECK_EQ(m.live_orders(), std::size_t{0});
}

TF_TEST(book_replace_loses_priority) {
  MarketBooks m;
  m.add(1, 1, Side::Buy, 100, 100);
  m.add(2, 1, Side::Buy, 100, 100);
  m.replace(1, 3, 150, 100);
  const auto& lv = m.book(1)->bids.best();
  TF_CHECK_EQ(lv.qty, uint64_t{250});
  TF_CHECK_EQ(m.order_at(lv.head).ref, uint64_t{2});
  TF_CHECK_EQ(m.order_at(lv.tail).ref, uint64_t{3});
  TF_CHECK(m.order(1) == nullptr);
  TF_CHECK_EQ(m.stats().adds, uint64_t{2});
  TF_CHECK_EQ(m.stats().replaces, uint64_t{1});
}

TF_TEST(book_duplicate_and_zero_adds_rejected) {
  MarketBooks m;
  TF_CHECK_EQ(m.add(1, 1, Side::Buy, 100, 100), 1);
  TF_CHECK_EQ(m.add(1, 1, Side::Buy, 100, 101), MarketBooks::kUnknown);
  TF_CHECK_EQ(m.add(2, 1, Side::Buy, 0, 101), MarketBooks::kUnknown);
  TF_CHECK_EQ(m.stats().duplicate_ref, uint64_t{1});
  TF_CHECK_EQ(m.stats().zero_add, uint64_t{1});
  TF_CHECK_EQ(m.book(1)->bids.size(), std::size_t{1});
}

TF_TEST(book_pool_slots_reused) {
  MarketBooks m(4);
  for (uint64_t r = 1; r <= 1000; ++r) {
    m.add(r, 1, Side::Sell, 10, uint32_t(100 + r % 7));
    if (r > 3) m.remove(r - 3);
  }
  TF_CHECK_EQ(m.live_orders(), std::size_t{3});
}
