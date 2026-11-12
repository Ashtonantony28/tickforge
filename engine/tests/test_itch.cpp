#include <vector>

#include "tf_test.hpp"
#include "tickforge/itch.hpp"

namespace {

void put16(std::vector<uint8_t>& v, uint16_t x) { v.push_back(uint8_t(x >> 8)); v.push_back(uint8_t(x)); }
void put32(std::vector<uint8_t>& v, uint32_t x) { put16(v, uint16_t(x >> 16)); put16(v, uint16_t(x)); }
void put48(std::vector<uint8_t>& v, uint64_t x) { put16(v, uint16_t(x >> 32)); put32(v, uint32_t(x)); }
void put64(std::vector<uint8_t>& v, uint64_t x) { put32(v, uint32_t(x >> 32)); put32(v, uint32_t(x)); }

std::vector<uint8_t> add_msg(uint64_t ref, char side, uint32_t shares, uint32_t price) {
  std::vector<uint8_t> p;
  p.push_back('A');
  put16(p, 5);                  // locate
  put16(p, 0);                  // tracking
  put48(p, 0x0123456789abULL);  // timestamp
  put64(p, ref);
  p.push_back(uint8_t(side));
  put32(p, shares);
  for (char c : std::string("AAPL    ")) p.push_back(uint8_t(c));
  put32(p, price);
  std::vector<uint8_t> f;
  put16(f, uint16_t(p.size()));
  f.insert(f.end(), p.begin(), p.end());
  return f;
}

struct Capture : tf::itch::NullHandler {
  std::vector<tf::itch::AddOrder> adds;
  int bad = 0, ends = 0;
  void on_add(const tf::itch::AddOrder& a) { adds.push_back(a); }
  void on_bad_message(char, uint16_t) { ++bad; }
  void on_message_end() { ++ends; }
};

}  // namespace

TF_TEST(itch_lengths_match_spec) {
  using tf::itch::kLength;
  TF_CHECK_EQ(int(kLength['A']), 36);
  TF_CHECK_EQ(int(kLength['F']), 40);
  TF_CHECK_EQ(int(kLength['E']), 31);
  TF_CHECK_EQ(int(kLength['C']), 36);
  TF_CHECK_EQ(int(kLength['X']), 23);
  TF_CHECK_EQ(int(kLength['D']), 19);
  TF_CHECK_EQ(int(kLength['U']), 35);
  TF_CHECK_EQ(int(kLength['P']), 44);
  TF_CHECK_EQ(int(kLength['R']), 39);
}

TF_TEST(itch_decode_add_fields) {
  auto f = add_msg(0x1122334455667788ULL, 'S', 300, 1234500);
  TF_CHECK_EQ(f.size(), std::size_t{38});
  Capture c;
  auto r = tf::itch::parse(f.data(), f.size(), c);
  TF_CHECK_EQ(r.messages, uint64_t{1});
  TF_CHECK(!r.truncated);
  TF_CHECK_EQ(c.adds.size(), std::size_t{1});
  const auto& a = c.adds[0];
  TF_CHECK_EQ(a.h.locate, uint16_t{5});
  TF_CHECK_EQ(a.h.timestamp, uint64_t{0x0123456789abULL});
  TF_CHECK_EQ(a.ref, uint64_t{0x1122334455667788ULL});
  TF_CHECK_EQ(a.side, 'S');
  TF_CHECK_EQ(a.shares, uint32_t{300});
  TF_CHECK_EQ(a.price, uint32_t{1234500});
}

TF_TEST(itch_truncated_tail_not_consumed) {
  auto f = add_msg(1, 'B', 100, 100);
  auto g = add_msg(2, 'B', 100, 100);
  f.insert(f.end(), g.begin(), g.begin() + 10);
  Capture c;
  auto r = tf::itch::parse(f.data(), f.size(), c);
  TF_CHECK_EQ(r.messages, uint64_t{1});
  TF_CHECK(r.truncated);
  TF_CHECK_EQ(r.bytes_consumed, std::size_t{38});
}

TF_TEST(itch_bad_length_reported_and_skipped) {
  std::vector<uint8_t> f = {0, 3, 'A', 0, 0};  // 'A' must be 36 bytes
  auto g = add_msg(9, 'B', 100, 100);
  f.insert(f.end(), g.begin(), g.end());
  Capture c;
  auto r = tf::itch::parse(f.data(), f.size(), c);
  TF_CHECK_EQ(r.messages, uint64_t{2});
  TF_CHECK_EQ(c.bad, 1);
  TF_CHECK_EQ(c.ends, 2);
  TF_CHECK_EQ(c.adds.size(), std::size_t{1});
}
