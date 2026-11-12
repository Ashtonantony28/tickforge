// pybind11 bindings: expose the replayer so Python tests and research can drive the C++ engine.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "tickforge/replay.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_tickforge, m) {
  m.doc() = "Tickforge C++ engine";
  py::class_<tf::Replayer>(m, "Replayer")
      .def(py::init<bool, std::size_t>(), py::arg("with_digest") = true, py::arg("expected_orders") = 1 << 20)
      .def("feed",
           [](tf::Replayer& r, py::bytes b) {
             const std::string_view sv = b;
             return r.feed(reinterpret_cast<const uint8_t*>(sv.data()), sv.size());
           },
           "Feed length-prefixed ITCH bytes; returns bytes consumed.")
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
}
