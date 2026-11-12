// Golden-fixture check (ADR 0004): replay the committed synthetic ITCH fixture and compare the
// digest and stats against the values the Python reference book produced when it was generated.
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "tf_test.hpp"
#include "tickforge/replay.hpp"

namespace {

std::vector<uint8_t> slurp(const std::string& path) {
  std::ifstream f(path, std::ios::binary);
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

// Tiny extractor for `"key": value` from the flat expected JSON (no JSON dependency).
std::string field(const std::string& json, const std::string& key) {
  const auto k = json.find("\"" + key + "\"");
  if (k == std::string::npos) return {};
  auto p = json.find(':', k) + 1;
  while (json[p] == ' ' || json[p] == '"') ++p;
  auto e = p;
  while (e < json.size() && json[e] != ',' && json[e] != '"' && json[e] != '\n' && json[e] != '}') ++e;
  return json.substr(p, e - p);
}

void check_fixture(const std::string& stem) {
  const std::string dir = TF_FIXTURE_DIR;
  const auto data = slurp(dir + "/" + stem + ".itch");
  std::ifstream ej(dir + "/" + stem + ".expected.json");
  std::stringstream ss;
  ss << ej.rdbuf();
  const std::string exp = ss.str();
  TF_CHECK(!data.empty());
  TF_CHECK(!exp.empty());

  tf::Replayer r;
  const std::size_t used = r.feed(data.data(), data.size());
  TF_CHECK_EQ(used, data.size());
  char hex[17];
  std::snprintf(hex, sizeof hex, "%016llx", (unsigned long long)r.digest());
  if (field(exp, "digest") != hex)
    std::fprintf(stderr, "  digest mismatch for %s: expected %s got %s\n", stem.c_str(),
                 field(exp, "digest").c_str(), hex);
  TF_CHECK(field(exp, "digest") == hex);
  TF_CHECK_EQ(std::to_string(r.messages()), field(exp, "messages"));
  TF_CHECK_EQ(std::to_string(r.books().live_orders()), field(exp, "live_orders"));
  const auto& s = r.books().stats();
  TF_CHECK_EQ(std::to_string(s.adds), field(exp, "adds"));
  TF_CHECK_EQ(std::to_string(s.executions), field(exp, "executions"));
  TF_CHECK_EQ(std::to_string(s.unknown_ref), field(exp, "unknown_ref"));
  TF_CHECK_EQ(std::to_string(s.overfill), field(exp, "overfill"));
  TF_CHECK_EQ(std::to_string(s.duplicate_ref), field(exp, "duplicate_ref"));
}

}  // namespace

TF_TEST(fixture_synth_small) { check_fixture("synth_small"); }
TF_TEST(fixture_synth_faults) { check_fixture("synth_faults"); }
