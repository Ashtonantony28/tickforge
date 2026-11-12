// Nasdaq TotalView-ITCH 5.0 parser (header-only, zero-copy, no allocation).
// Input framing: each message is preceded by a 2-byte big-endian length, as in Nasdaq's
// published sample files. Messages that affect the book are decoded into small PODs and passed
// to a handler; everything else is counted and skipped by length.
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace tf::itch {

inline uint16_t be16(const uint8_t* p) { return static_cast<uint16_t>((p[0] << 8) | p[1]); }
inline uint32_t be32(const uint8_t* p) {
  return (uint32_t{p[0]} << 24) | (uint32_t{p[1]} << 16) | (uint32_t{p[2]} << 8) | uint32_t{p[3]};
}
inline uint64_t be48(const uint8_t* p) {
  return (uint64_t{be16(p)} << 32) | uint64_t{be32(p + 2)};
}
inline uint64_t be64(const uint8_t* p) { return (uint64_t{be32(p)} << 32) | uint64_t{be32(p + 4)}; }

// Message lengths (payload, excluding the 2-byte length prefix) from the ITCH 5.0 spec.
inline constexpr std::array<uint8_t, 128> kLength = [] {
  std::array<uint8_t, 128> t{};
  t['S'] = 12; t['R'] = 39; t['H'] = 25; t['Y'] = 20; t['L'] = 26; t['V'] = 35; t['W'] = 12;
  t['K'] = 28; t['J'] = 35; t['h'] = 21; t['A'] = 36; t['F'] = 40; t['E'] = 31; t['C'] = 36;
  t['X'] = 23; t['D'] = 19; t['U'] = 35; t['P'] = 44; t['Q'] = 40; t['B'] = 19; t['I'] = 50;
  t['N'] = 20; t['O'] = 48;
  return t;
}();

struct Header {
  char type;
  uint16_t locate;
  uint16_t tracking;
  uint64_t timestamp;  // nanoseconds since midnight
};
struct SystemEvent { Header h; char event; };
struct StockDirectory { Header h; char stock[8]; uint32_t round_lot; };
struct AddOrder { Header h; uint64_t ref; char side; uint32_t shares; char stock[8]; uint32_t price; };
struct OrderExecuted { Header h; uint64_t ref; uint32_t shares; uint64_t match; };
struct OrderExecutedPrice { Header h; uint64_t ref; uint32_t shares; uint64_t match; char printable; uint32_t price; };
struct OrderCancel { Header h; uint64_t ref; uint32_t shares; };
struct OrderDelete { Header h; uint64_t ref; };
struct OrderReplace { Header h; uint64_t old_ref; uint64_t new_ref; uint32_t shares; uint32_t price; };
struct Trade { Header h; uint64_t ref; char side; uint32_t shares; char stock[8]; uint32_t price; uint64_t match; };

// Default no-op handler; derive and hide the callbacks you need.
struct NullHandler {
  void on_system_event(const SystemEvent&) {}
  void on_stock_directory(const StockDirectory&) {}
  void on_add(const AddOrder&) {}
  void on_executed(const OrderExecuted&) {}
  void on_executed_price(const OrderExecutedPrice&) {}
  void on_cancel(const OrderCancel&) {}
  void on_delete(const OrderDelete&) {}
  void on_replace(const OrderReplace&) {}
  void on_trade(const Trade&) {}
  void on_other(char /*type*/) {}
  void on_bad_message(char /*type*/, uint16_t /*len*/) {}
  void on_message_end() {}
};

struct ParseResult {
  uint64_t messages = 0;
  std::size_t bytes_consumed = 0;
  bool truncated = false;  // trailing partial message left unconsumed
};

inline Header header(const uint8_t* m) {
  return Header{static_cast<char>(m[0]), be16(m + 1), be16(m + 3), be48(m + 5)};
}
inline void copy8(char* dst, const uint8_t* src) {
  for (int i = 0; i < 8; ++i) dst[i] = static_cast<char>(src[i]);
}

// Decode one message payload `m` of length `len` and dispatch it.
template <class H>
inline void dispatch(const uint8_t* m, uint16_t len, H& h) {
  const auto type = static_cast<uint8_t>(m[0]);
  if (type >= 128 || kLength[type] == 0 || kLength[type] != len) {
    h.on_bad_message(static_cast<char>(type), len);
    h.on_message_end();
    return;
  }
  switch (type) {
    case 'A':
    case 'F': {
      AddOrder a;
      a.h = header(m);
      a.ref = be64(m + 11);
      a.side = static_cast<char>(m[19]);
      a.shares = be32(m + 20);
      copy8(a.stock, m + 24);
      a.price = be32(m + 32);
      h.on_add(a);
      break;
    }
    case 'E':
      h.on_executed(OrderExecuted{header(m), be64(m + 11), be32(m + 19), be64(m + 23)});
      break;
    case 'C':
      h.on_executed_price(OrderExecutedPrice{header(m), be64(m + 11), be32(m + 19), be64(m + 23),
                                             static_cast<char>(m[31]), be32(m + 32)});
      break;
    case 'X': h.on_cancel(OrderCancel{header(m), be64(m + 11), be32(m + 19)}); break;
    case 'D': h.on_delete(OrderDelete{header(m), be64(m + 11)}); break;
    case 'U':
      h.on_replace(OrderReplace{header(m), be64(m + 11), be64(m + 19), be32(m + 27), be32(m + 31)});
      break;
    case 'P': {
      Trade t;
      t.h = header(m);
      t.ref = be64(m + 11);
      t.side = static_cast<char>(m[19]);
      t.shares = be32(m + 20);
      copy8(t.stock, m + 24);
      t.price = be32(m + 32);
      t.match = be64(m + 36);
      h.on_trade(t);
      break;
    }
    case 'S': h.on_system_event(SystemEvent{header(m), static_cast<char>(m[11])}); break;
    case 'R': {
      StockDirectory s;
      s.h = header(m);
      copy8(s.stock, m + 11);
      s.round_lot = be32(m + 21);
      h.on_stock_directory(s);
      break;
    }
    default: h.on_other(static_cast<char>(type)); break;
  }
  h.on_message_end();
}

// Parse a buffer of length-prefixed messages. Stops before a trailing partial message so a caller
// streaming from disk can carry the remainder into the next chunk.
template <class H>
inline ParseResult parse(const uint8_t* data, std::size_t n, H& h) {
  ParseResult r;
  std::size_t off = 0;
  while (off + 2 <= n) {
    const uint16_t len = be16(data + off);
    if (off + 2 + len > n) {
      r.truncated = true;
      break;
    }
    if (len == 0) {
      h.on_bad_message('\0', 0);
      h.on_message_end();
    } else {
      dispatch(data + off + 2, len, h);
    }
    off += 2 + std::size_t{len};
    ++r.messages;
  }
  if (off < n && !r.truncated) r.truncated = true;
  r.bytes_consumed = off;
  return r;
}

}  // namespace tf::itch
