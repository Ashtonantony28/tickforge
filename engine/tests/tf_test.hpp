// Minimal in-tree test harness (ADR 0002): TF_TEST registers a case, TF_CHECK records failures.
#pragma once
#include <cstdio>
#include <functional>
#include <string>
#include <type_traits>
#include <vector>

namespace tf::test {

struct Case {
  const char* name;
  std::function<void()> fn;
};

inline std::vector<Case>& registry() {
  static std::vector<Case> r;
  return r;
}
template <class T>
std::string show(const T& v) {
  if constexpr (std::is_convertible_v<T, std::string>) return std::string(v);
  else if constexpr (std::is_same_v<T, char>) return std::string(1, v);
  else if constexpr (std::is_same_v<T, bool>) return v ? "true" : "false";
  else return std::to_string(v);
}
inline int& failures() {
  static int f = 0;
  return f;
}
struct Registrar {
  Registrar(const char* name, std::function<void()> fn) { registry().push_back({name, std::move(fn)}); }
};

}  // namespace tf::test

#define TF_CAT2(a, b) a##b
#define TF_CAT(a, b) TF_CAT2(a, b)
#define TF_TEST(name)                                                          \
  static void name();                                                          \
  static ::tf::test::Registrar TF_CAT(tf_reg_, name){#name, &name};            \
  static void name()

#define TF_CHECK(cond)                                                         \
  do {                                                                         \
    if (!(cond)) {                                                             \
      std::fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);   \
      ++::tf::test::failures();                                                \
    }                                                                          \
  } while (0)

#define TF_CHECK_EQ(a, b)                                                      \
  do {                                                                         \
    const auto tf_a_ = (a);                                                    \
    const auto tf_b_ = (b);                                                    \
    if (!(tf_a_ == tf_b_)) {                                                   \
      std::fprintf(stderr, "  FAIL %s:%d: %s == %s (%s vs %s)\n", __FILE__,    \
                   __LINE__, #a, #b, ::tf::test::show(tf_a_).c_str(),          \
                   ::tf::test::show(tf_b_).c_str());                             \
      ++::tf::test::failures();                                                \
    }                                                                          \
  } while (0)
