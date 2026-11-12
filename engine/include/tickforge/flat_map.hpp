// Open-addressing hash map uint64 -> uint32 for order-reference lookup.
// Linear probing with backward-shift deletion (no tombstones), power-of-two capacity,
// max load 1/2. Key ~0 is reserved as the empty marker; ITCH order refs never use it.
#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace tf {

class FlatMap64 {
 public:
  static constexpr uint64_t kEmpty = ~uint64_t{0};

  explicit FlatMap64(std::size_t initial_capacity = 1024) { rehash(round_up(initial_capacity)); }

  std::size_t size() const { return size_; }
  std::size_t capacity() const { return slots_.size(); }

  uint32_t* find(uint64_t key) {
    std::size_t i = home(key);
    for (;;) {
      Slot& s = slots_[i];
      if (s.key == key) return &s.val;
      if (s.key == kEmpty) return nullptr;
      i = (i + 1) & mask_;
    }
  }
  const uint32_t* find(uint64_t key) const { return const_cast<FlatMap64*>(this)->find(key); }

  // Returns false (and leaves the map unchanged) if the key already exists.
  bool insert(uint64_t key, uint32_t val) {
    assert(key != kEmpty);
    if ((size_ + 1) * 2 > slots_.size()) rehash(slots_.size() * 2);
    std::size_t i = home(key);
    for (;;) {
      Slot& s = slots_[i];
      if (s.key == kEmpty) {
        s.key = key;
        s.val = val;
        ++size_;
        return true;
      }
      if (s.key == key) return false;
      i = (i + 1) & mask_;
    }
  }

  bool erase(uint64_t key) {
    std::size_t i = home(key);
    for (;;) {
      if (slots_[i].key == kEmpty) return false;
      if (slots_[i].key == key) break;
      i = (i + 1) & mask_;
    }
    // Backward-shift: pull later entries of the probe run into the hole when allowed.
    std::size_t j = i;
    for (;;) {
      j = (j + 1) & mask_;
      if (slots_[j].key == kEmpty) break;
      const std::size_t k = home(slots_[j].key);
      // Entry at j may move to i only if its home k is not cyclically within (i, j].
      const bool stays = (i <= j) ? (i < k && k <= j) : (i < k || k <= j);
      if (stays) continue;
      slots_[i] = slots_[j];
      i = j;
    }
    slots_[i].key = kEmpty;
    --size_;
    return true;
  }

  void clear() {
    for (auto& s : slots_) s.key = kEmpty;
    size_ = 0;
  }

 private:
  struct Slot {
    uint64_t key = kEmpty;
    uint32_t val = 0;
  };

  static std::size_t round_up(std::size_t n) {
    std::size_t c = 16;
    while (c < n) c <<= 1;
    return c;
  }
  static uint64_t mix(uint64_t x) {  // splitmix64 finalizer
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    x *= 0x94d049bb133111ebULL;
    x ^= x >> 31;
    return x;
  }
  std::size_t home(uint64_t key) const { return static_cast<std::size_t>(mix(key)) & mask_; }

  void rehash(std::size_t cap) {
    std::vector<Slot> old;
    old.swap(slots_);
    slots_.assign(cap, Slot{});
    mask_ = cap - 1;
    size_ = 0;
    for (const Slot& s : old)
      if (s.key != kEmpty) insert(s.key, s.val);
  }

  std::vector<Slot> slots_;
  std::size_t mask_ = 0;
  std::size_t size_ = 0;
};

}  // namespace tf
