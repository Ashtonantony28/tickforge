#include <cstring>

#include "tf_test.hpp"

int main(int argc, char** argv) {
  const char* filter = argc > 1 ? argv[1] : nullptr;
  int run = 0;
  for (const auto& c : tf::test::registry()) {
    if (filter && std::strstr(c.name, filter) == nullptr) continue;
    const int before = tf::test::failures();
    c.fn();
    ++run;
    std::printf("%s %s\n", tf::test::failures() == before ? "ok  " : "FAIL", c.name);
  }
  std::printf("%d tests, %d failed checks\n", run, tf::test::failures());
  return tf::test::failures() == 0 ? 0 : 1;
}
