// ITCH -> MarketBooks replayer. After every book-changing message (A F E C X D U) it folds
// (message index, locate, best bid px, best bid qty, best ask px, best ask qty) into a digest, so
// two implementations agree only if their top of book matches after every single update.
#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "tickforge/digest.hpp"
#include "tickforge/itch.hpp"
#include "tickforge/order_book.hpp"

namespace tf {

class Replayer : public itch::NullHandler {
 public:
  explicit Replayer(bool with_digest = true, std::size_t expected_orders = 1 << 20)
      : books_(expected_orders), with_digest_(with_digest) {}

  // Feed raw length-prefixed ITCH bytes; returns bytes consumed (a trailing partial message is
  // left for the caller to resend with the next chunk).
  std::size_t feed(const uint8_t* data, std::size_t n) { return itch::parse(data, n, *this).bytes_consumed; }

  // --- handler callbacks -------------------------------------------------------------------
  void on_add(const itch::AddOrder& a) {
    touched_ = books_.add(a.ref, a.h.locate, a.side == 'B' ? Side::Buy : Side::Sell, a.shares, a.price);
  }
  void on_executed(const itch::OrderExecuted& e) { touched_ = books_.reduce(e.ref, e.shares, true); }
  void on_executed_price(const itch::OrderExecutedPrice& e) { touched_ = books_.reduce(e.ref, e.shares, true); }
  void on_cancel(const itch::OrderCancel& x) { touched_ = books_.reduce(x.ref, x.shares, false); }
  void on_delete(const itch::OrderDelete& d) { touched_ = books_.remove(d.ref); }
  void on_replace(const itch::OrderReplace& u) { touched_ = books_.replace(u.old_ref, u.new_ref, u.shares, u.price); }
  void on_trade(const itch::Trade&) { ++trades_; }
  void on_system_event(const itch::SystemEvent&) { ++system_events_; }
  void on_stock_directory(const itch::StockDirectory& s) {
    if (s.h.locate >= symbols_.size()) symbols_.resize(std::size_t{s.h.locate} + 1);
    symbols_[s.h.locate].assign(s.stock, 8);
    const auto end = symbols_[s.h.locate].find_last_not_of(' ');
    symbols_[s.h.locate].resize(end == std::string::npos ? 0 : end + 1);
  }
  void on_other(char) { ++other_; }
  void on_bad_message(char, uint16_t) { ++bad_; }
  void on_message_end() {
    if (touched_ != MarketBooks::kUnknown && with_digest_) fold(static_cast<uint16_t>(touched_));
    touched_ = MarketBooks::kUnknown;
    ++msg_index_;
  }

  // --- queries -------------------------------------------------------------------------------
  const MarketBooks& books() const { return books_; }
  uint64_t digest() const { return digest_.h; }
  uint64_t messages() const { return msg_index_; }
  uint64_t trades() const { return trades_; }
  uint64_t system_events() const { return system_events_; }
  uint64_t other_messages() const { return other_; }
  uint64_t bad_messages() const { return bad_; }
  const std::vector<std::string>& symbols() const { return symbols_; }

 private:
  void fold(uint16_t locate) {
    const Book& b = *books_.book(locate);
    digest_.word(msg_index_);
    digest_.word(locate);
    digest_.word(b.bids.empty() ? 0 : b.bids.best().price);
    digest_.word(b.bids.empty() ? 0 : b.bids.best().qty);
    digest_.word(b.asks.empty() ? 0 : b.asks.best().price);
    digest_.word(b.asks.empty() ? 0 : b.asks.best().qty);
  }

  MarketBooks books_;
  Digest digest_;
  bool with_digest_;
  int32_t touched_ = MarketBooks::kUnknown;
  uint64_t msg_index_ = 0;
  uint64_t trades_ = 0;
  uint64_t system_events_ = 0;
  uint64_t other_ = 0;
  uint64_t bad_ = 0;
  std::vector<std::string> symbols_;
};

}  // namespace tf
