#include <random>
#include <unordered_map>

#include "tf_test.hpp"
#include "tickforge/flat_map.hpp"

TF_TEST(flat_map_basic) {
  tf::FlatMap64 m(4);
  TF_CHECK(m.insert(7, 70));
  TF_CHECK(!m.insert(7, 71));
  TF_CHECK(m.find(7) && *m.find(7) == 70);
  TF_CHECK(m.find(8) == nullptr);
  TF_CHECK(m.erase(7));
  TF_CHECK(!m.erase(7));
  TF_CHECK_EQ(m.size(), std::size_t{0});
}

// Randomized differential test against std::unordered_map, with a small key space so probe runs,
// wrap-around and backward-shift deletion are all exercised heavily.
TF_TEST(flat_map_vs_unordered_map) {
  std::mt19937_64 rng(12345);
  tf::FlatMap64 m(16);
  std::unordered_map<uint64_t, uint32_t> ref;
  for (int i = 0; i < 400000; ++i) {
    const uint64_t k = rng() % 5000;
    const int op = static_cast<int>(rng() % 3);
    if (op == 0) {
      const uint32_t v = static_cast<uint32_t>(rng());
      TF_CHECK_EQ(m.insert(k, v), ref.emplace(k, v).second);
    } else if (op == 1) {
      TF_CHECK_EQ(m.erase(k), ref.erase(k) == 1);
    } else {
      const uint32_t* p = m.find(k);
      auto it = ref.find(k);
      TF_CHECK_EQ(p != nullptr, it != ref.end());
      if (p && it != ref.end()) TF_CHECK_EQ(*p, it->second);
    }
  }
  TF_CHECK_EQ(m.size(), ref.size());
  for (const auto& [k, v] : ref) TF_CHECK(m.find(k) && *m.find(k) == v);
}
