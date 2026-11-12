// pybind11 bindings: expose the replayer so Python tests and research can drive the C++ engine.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "tickforge/l2_book.hpp"
#include "tickforge/replay.hpp"
#include "tickforge/sim.hpp"

namespace py = pybind11;

namespace {

// Lets Python subclasses implement strategies (slow; for research on small samples).
class PyStrategy : public tf::Strategy {
 public:
  using tf::Strategy::Strategy;
  // Pass the Sim by reference: the default override macros would hand Python a copy.
  void on_market(tf::Sim& sim) override {
    py::gil_scoped_acquire gil;
    py::function fn = py::get_override(static_cast<const tf::Strategy*>(this), "on_market");
    if (!fn) py::pybind11_fail("Strategy.on_market not implemented");
    fn(py::cast(&sim, py::return_value_policy::reference));
  }
  void on_fill(tf::Sim& sim, const tf::Fill& f) override {
    py::gil_scoped_acquire gil;
    py::function fn = py::get_override(static_cast<const tf::Strategy*>(this), "on_fill");
    if (fn) fn(py::cast(&sim, py::return_value_policy::reference), f);
  }
};

// Owns the strategy reference so Python keeps it alive for the Sim's lifetime.
struct PySim {
  py::object strategy;
  std::unique_ptr<tf::Sim> sim;
};

tf::Side parse_side(const std::string& s) { return s == "B" ? tf::Side::Buy : tf::Side::Sell; }
const char* side_str(tf::Side s) { return s == tf::Side::Buy ? "B" : "S"; }

}  // namespace

PYBIND11_MODULE(_tickforge, m) {
  m.doc() = "Tickforge C++ engine";
  py::class_<tf::L2Replayer>(m, "L2Replayer")
      .def(py::init<std::size_t>(), py::arg("reserve_levels") = 8192)
      .def("feed",
           [](tf::L2Replayer& r, py::bytes b) {
             const std::string_view sv = b;
             return r.feed(reinterpret_cast<const uint8_t*>(sv.data()), sv.size());
           },
           "Feed 32-byte L2 events; returns bytes consumed.")
      .def("top",
           [](const tf::L2Replayer& r, std::size_t n, bool bids) {
             const tf::L2Side& s = bids ? r.book().bids : r.book().asks;
             std::vector<std::pair<int64_t, int64_t>> out;
             for (std::size_t i = 0; i < s.size() && i < n; ++i) out.emplace_back(s.level(i).price, s.level(i).qty);
             return out;
           })
      .def_property_readonly("digest", &tf::L2Replayer::digest)
      .def_property_readonly("batches", &tf::L2Replayer::batches)
      .def_property_readonly("crossed", &tf::L2Replayer::crossed);
  py::class_<tf::Replayer>(m, "Replayer")
      .def(py::init<bool, std::size_t>(), py::arg("with_digest") = true, py::arg("expected_orders") = 1 << 20)
      .def("feed",
           [](tf::Replayer& r, py::bytes b) {
             const std::string_view sv = b;
             return r.feed(reinterpret_cast<const uint8_t*>(sv.data()), sv.size());
           },
           "Feed length-prefixed ITCH bytes; returns bytes consumed.")
      .def("set_trace_locate", &tf::Replayer::set_trace_locate)
      .def_property_readonly("trace",
                             [](const tf::Replayer& r) {
                               std::vector<std::tuple<uint64_t, uint32_t, uint64_t, uint32_t, uint64_t>> out;
                               for (const auto& e : r.trace()) out.emplace_back(e.index, e.bid_px, e.bid_qty, e.ask_px, e.ask_qty);
                               return out;
                             }, "List of (msg_index, bid_px, bid_qty, ask_px, ask_qty) for the traced locate.")
      .def_property_readonly("digest", &tf::Replayer::digest)
      .def_property_readonly("messages", &tf::Replayer::messages)
      .def_property_readonly("trades", &tf::Replayer::trades)
      .def_property_readonly("bad_messages", &tf::Replayer::bad_messages)
      .def_property_readonly("live_orders", [](const tf::Replayer& r) { return r.books().live_orders(); })
      .def_property_readonly("symbols",
                             [](const tf::Replayer& r) {
                               py::dict d;
                               const auto& s = r.symbols();
                               for (std::size_t i = 0; i < s.size(); ++i)
                                 if (!s[i].empty()) d[py::int_(i)] = s[i];
                               return d;
                             })
      .def_property_readonly("stats",
                             [](const tf::Replayer& r) {
                               const auto& s = r.books().stats();
                               py::dict d;
                               d["adds"] = s.adds; d["executions"] = s.executions; d["cancels"] = s.cancels;
                               d["deletes"] = s.deletes; d["replaces"] = s.replaces;
                               d["unknown_ref"] = s.unknown_ref; d["duplicate_ref"] = s.duplicate_ref;
                               d["overfill"] = s.overfill; d["zero_add"] = s.zero_add;
                               return d;
                             })
      .def("depth",
           [](const tf::Replayer& r, uint16_t locate, const std::string& side, std::size_t n) {
             std::vector<std::tuple<uint32_t, uint64_t, uint32_t>> out;
             const tf::Book* b = r.books().book(locate);
             if (!b) return out;
             const tf::BookSide& bs = side == "B" ? b->bids : b->asks;
             for (std::size_t i = 0; i < bs.size() && i < n; ++i) {
               const auto& l = bs.level(i);
               out.emplace_back(l.price, l.qty, l.count);
             }
             return out;
           },
           py::arg("locate"), py::arg("side"), py::arg("n") = 5, "Top-n levels (price, qty, count), best first.")
      .def("queue",
           [](const tf::Replayer& r, uint16_t locate, const std::string& side, uint32_t price) {
             std::vector<uint64_t> refs;
             const tf::Book* b = r.books().book(locate);
             if (!b) return refs;
             const tf::BookSide& bs = side == "B" ? b->bids : b->asks;
             for (std::size_t i = 0; i < bs.size(); ++i) {
               if (bs.level(i).price != price) continue;
               for (uint32_t o = bs.level(i).head; o != tf::kNil; o = r.books().order_at(o).next)
                 refs.push_back(r.books().order_at(o).ref);
             }
             return refs;
           },
           py::arg("locate"), py::arg("side"), py::arg("price"), "Order refs at a level, front of queue first.");

  py::class_<tf::SimConfig>(m, "SimConfig")
      .def(py::init<>())
      .def_readwrite("locate", &tf::SimConfig::locate)
      .def_readwrite("latency_ns", &tf::SimConfig::latency_ns)
      .def_readwrite("jitter_ns", &tf::SimConfig::jitter_ns)
      .def_readwrite("maker_fee", &tf::SimConfig::maker_fee)
      .def_readwrite("taker_fee", &tf::SimConfig::taker_fee)
      .def_readwrite("max_position", &tf::SimConfig::max_position)
      .def_readwrite("max_order_qty", &tf::SimConfig::max_order_qty)
      .def_readwrite("sample_ns", &tf::SimConfig::sample_ns)
      .def_readwrite("seed", &tf::SimConfig::seed);

  py::class_<tf::Fill>(m, "Fill")
      .def_readonly("ts", &tf::Fill::ts)
      .def_readonly("order_id", &tf::Fill::order_id)
      .def_property_readonly("side", [](const tf::Fill& f) { return side_str(f.side); })
      .def_readonly("price", &tf::Fill::price)
      .def_readonly("qty", &tf::Fill::qty)
      .def_readonly("maker", &tf::Fill::maker)
      .def_readonly("fee", &tf::Fill::fee)
      .def("__repr__", [](const tf::Fill& f) {
        return "Fill(ts=" + std::to_string(f.ts) + ", id=" + std::to_string(f.order_id) + ", " + side_str(f.side) +
               " " + std::to_string(f.qty) + "@" + std::to_string(f.price) + (f.maker ? ", maker)" : ", taker)");
      });

  py::class_<tf::SimOrder>(m, "SimOrder")
      .def_readonly("id", &tf::SimOrder::id)
      .def_property_readonly("side", [](const tf::SimOrder& o) { return side_str(o.side); })
      .def_readonly("price", &tf::SimOrder::price)
      .def_readonly("qty", &tf::SimOrder::qty)
      .def_readonly("ahead", &tf::SimOrder::ahead);

  py::class_<tf::Strategy, PyStrategy>(m, "Strategy")
      .def(py::init<>())
      .def("on_market", &tf::Strategy::on_market)
      .def("on_fill", &tf::Strategy::on_fill);
  py::class_<tf::TouchQuoter, tf::Strategy>(m, "TouchQuoter").def(py::init<uint32_t>(), py::arg("size") = 100);

  // Strategy-facing view (passed to on_market/on_fill).
  py::class_<tf::Sim>(m, "SimView")
      .def_property_readonly("now", &tf::Sim::now)
      .def_property_readonly("best_bid", &tf::Sim::best_bid)
      .def_property_readonly("best_ask", &tf::Sim::best_ask)
      .def_property_readonly("position", &tf::Sim::position)
      .def_property_readonly("cash", &tf::Sim::cash)
      .def_property_readonly("open_orders", &tf::Sim::open_orders)
      .def("submit", [](tf::Sim& s, const std::string& side, uint32_t price, uint32_t qty, bool ioc, bool post_only) {
             return s.submit(parse_side(side), price, qty, ioc, post_only);
           }, py::arg("side"), py::arg("price"), py::arg("qty"), py::arg("ioc") = false, py::arg("post_only") = false)
      .def("can_submit", [](const tf::Sim& s, const std::string& side, uint32_t qty) { return s.can_submit(parse_side(side), qty); })
      .def("cancel", &tf::Sim::cancel)
      .def("cancel_all", &tf::Sim::cancel_all)
      .def("in_flight", &tf::Sim::in_flight)
      .def("depth", [](const tf::Sim& s, const std::string& side, std::size_t n) {
             std::vector<std::tuple<uint32_t, uint64_t, uint32_t>> out;
             const tf::Book* b = s.book();
             if (!b) return out;
             const tf::BookSide& bs = side == "B" ? b->bids : b->asks;
             for (std::size_t i = 0; i < bs.size() && i < n; ++i) out.emplace_back(bs.level(i).price, bs.level(i).qty, bs.level(i).count);
             return out;
           }, py::arg("side"), py::arg("n") = 5);

  py::class_<PySim>(m, "Backtest")
      .def(py::init([](const tf::SimConfig& cfg, py::object strategy) {
             auto p = std::make_unique<PySim>();
             p->strategy = strategy;
             tf::Strategy* s = strategy.is_none() ? nullptr : strategy.cast<tf::Strategy*>();
             p->sim = std::make_unique<tf::Sim>(cfg, s);
             return p;
           }), py::arg("config"), py::arg("strategy") = py::none())
      .def("feed", [](PySim& p, py::bytes b) {
             const std::string_view sv = b;
             return p.sim->feed(reinterpret_cast<const uint8_t*>(sv.data()), sv.size());
           })
      .def("finish", [](PySim& p) { p.sim->finish(); })
      .def_property_readonly("view", [](PySim& p) -> tf::Sim& { return *p.sim; }, py::return_value_policy::reference_internal)
      .def_property_readonly("fills", [](PySim& p) { return p.sim->fills(); })
      .def_property_readonly("fill_digest", [](PySim& p) { return p.sim->fill_digest(); })
      .def_property_readonly("position", [](PySim& p) { return p.sim->position(); })
      .def_property_readonly("cash", [](PySim& p) { return p.sim->cash(); })
      .def_property_readonly("fees", [](PySim& p) { return p.sim->fees_paid(); })
      .def_property_readonly("equity", [](PySim& p) { return p.sim->equity(); })
      .def_property_readonly("messages", [](PySim& p) { return p.sim->messages(); })
      .def_property_readonly("open_orders", [](PySim& p) { return p.sim->open_orders(); })
      .def_property_readonly("samples", [](PySim& p) {
             std::vector<std::tuple<uint64_t, int64_t, int64_t, uint32_t, int64_t>> out;
             for (const auto& s : p.sim->samples()) out.emplace_back(s.ts, s.position, s.cash, s.mid2, s.equity);
             return out;
           }, "List of (ts, position, cash, bid+ask, equity).")
      .def_property_readonly("stats", [](PySim& p) {
             const auto& s = p.sim->stats();
             py::dict d;
             d["submitted"] = s.submitted; d["rejected"] = s.rejected; d["cancelled"] = s.cancelled;
             d["cancel_rejects"] = s.cancel_rejects; d["post_only_rejects"] = s.post_only_rejects; d["fills"] = s.fills; d["maker_fills"] = s.maker_fills;
             d["taker_fills"] = s.taker_fills; d["shares_bought"] = s.shares_bought; d["shares_sold"] = s.shares_sold;
             return d;
           });
}
