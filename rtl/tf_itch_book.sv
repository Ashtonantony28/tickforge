// ITCH 5.0 feed handler + top-of-book for one instrument (M7, ADR 0007).
//
//   byte stream -> tf_itch_parser -> control FSM -> tf_order_table (hash, linear probing)
//                                             \-> tf_price_levels x2 (bid, ask)
//
// After every message that changes this instrument's book it emits one top-of-book record
// (message index, best bid price/qty, best ask price/qty). The testbench folds these records into the
// same FNV-1a digest the C++ engine computes, so RTL and engine agree only if they match after every
// single update. Malformed traffic is handled exactly as the engine does: zero-share adds and
// duplicate references are rejected, references we do not hold are ignored, over-fills are clamped.
// Messages are filtered by the header's stock locate (ITCH guarantees it matches the order's).
module tf_itch_book
  import tf_itch_pkg::*;
#(
  parameter int AW     = 12,  // order table: 2^AW slots
  parameter int PROBES = 16,  // max linear-probe length
  parameter int LEVELS = 64,  // price levels held per side
  parameter int QTY_W  = 40,
  parameter int IDX_W  = 32
) (
  input  logic             clk,
  input  logic             rst,
  input  logic [15:0]      cfg_locate,
  input  logic             s_valid,
  input  logic [7:0]       s_data,
  output logic             s_ready,
  // top-of-book update
  output logic             tob_valid,
  output logic [IDX_W-1:0] tob_index,
  output logic [31:0]      tob_bid_px,
  output logic [QTY_W-1:0] tob_bid_qty,
  output logic [31:0]      tob_ask_px,
  output logic [QTY_W-1:0] tob_ask_qty,
  // event counters (match engine BookStats for this instrument)
  output logic [31:0]      cnt_unknown_ref,
  output logic [31:0]      cnt_duplicate_ref,
  output logic [31:0]      cnt_zero_add,
  output logic [31:0]      cnt_overfill,
  output logic [31:0]      cnt_bad_len,
  // capacity faults (must stay 0 for the book to be exact)
  output logic [31:0]      cnt_table_full,
  output logic [31:0]      cnt_level_overflow,
  output logic [31:0]      cnt_level_miss
);
  localparam logic [1:0] EMPTY = 2'd0, FULL = 2'd1, TOMB = 2'd2;

  // ---------------- parser ----------------
  logic             m_valid, m_ready, m_len_ok, m_buy;
  logic [7:0]       m_type;
  logic [15:0]      m_locate;
  logic [63:0]      m_ref, m_new_ref;
  logic [31:0]      m_shares, m_price;
  logic [IDX_W-1:0] m_index;

  tf_itch_parser #(.IDX_W(IDX_W)) u_parser (
    .clk(clk), .rst(rst),
    .s_valid(s_valid), .s_data(s_data), .s_ready(s_ready),
    .m_valid(m_valid), .m_ready(m_ready), .m_type(m_type), .m_len_ok(m_len_ok),
    .m_locate(m_locate), .m_ref(m_ref), .m_new_ref(m_new_ref), .m_buy(m_buy),
    .m_shares(m_shares), .m_price(m_price), .m_index(m_index)
  );

  // ---------------- order table ----------------
  logic [AW-1:0] rd_addr, wr_addr;
  logic [1:0]    rd_state, wr_state;
  logic [63:0]   rd_key, wr_key;
  logic          rd_buy, wr_buy, wr_en;
  logic [31:0]   rd_price, rd_shares, wr_price, wr_shares;

  tf_order_table #(.AW(AW)) u_table (
    .clk(clk), .rst(rst),
    .rd_addr(rd_addr), .rd_state(rd_state), .rd_key(rd_key), .rd_buy(rd_buy),
    .rd_price(rd_price), .rd_shares(rd_shares),
    .wr_en(wr_en), .wr_addr(wr_addr), .wr_state(wr_state), .wr_key(wr_key), .wr_buy(wr_buy),
    .wr_price(wr_price), .wr_shares(wr_shares)
  );

  // ---------------- price levels ----------------
  logic             lv_valid, lv_add, lv_buy;
  logic [31:0]      lv_price, lv_qty;
  logic             bid_ok, ask_ok, bid_ovf, ask_ovf, bid_miss, ask_miss;
  logic [31:0]      bid_px, ask_px;
  logic [QTY_W-1:0] bid_qty, ask_qty;

  tf_price_levels #(.L(LEVELS), .IS_BID(1'b1), .QTY_W(QTY_W)) u_bids (
    .clk(clk), .rst(rst), .op_valid(lv_valid && lv_buy), .op_add(lv_add), .op_price(lv_price),
    .op_qty(lv_qty), .best_valid(bid_ok), .best_price(bid_px), .best_qty(bid_qty),
    .overflow(bid_ovf), .miss(bid_miss)
  );
  tf_price_levels #(.L(LEVELS), .IS_BID(1'b0), .QTY_W(QTY_W)) u_asks (
    .clk(clk), .rst(rst), .op_valid(lv_valid && !lv_buy), .op_add(lv_add), .op_price(lv_price),
    .op_qty(lv_qty), .best_valid(ask_ok), .best_price(ask_px), .best_qty(ask_qty),
    .overflow(ask_ovf), .miss(ask_miss)
  );

  // ---------------- control ----------------
  typedef enum logic [2:0] {C_IDLE, C_FIND, C_INSERT, C_EMIT} cstate_t;
  cstate_t cs;

  logic [7:0]       r_type;
  logic [63:0]      r_ref, r_new_ref, ins_ref;
  logic             ins_buy;
  logic [31:0]      r_shares, r_price, ins_shares, ins_price;
  logic [IDX_W-1:0] r_index;
  logic [AW-1:0]    base;
  logic [$clog2(PROBES+1)-1:0] probe;
  logic             have_tomb;
  logic [AW-1:0]    tomb_addr;

  function automatic logic [AW-1:0] hash(input logic [63:0] k);
    logic [AW-1:0] h;
    h = '0;
    for (int i = 0; i < 64; i += AW) h = h ^ AW'(k >> i);
    return h;
  endfunction

  assign rd_addr = base + AW'(probe);
  assign m_ready = (cs == C_IDLE);

  wire is_add  = (m_type == T_ADD) || (m_type == T_ADD_MPID);
  wire is_red  = (m_type == T_EXEC) || (m_type == T_EXEC_PX) || (m_type == T_CANCEL);
  wire is_del  = (m_type == T_DELETE);
  wire is_repl = (m_type == T_REPLACE);
  wire decoded = is_add || is_red || is_del || is_repl;
  wire probe_last = (probe == ($clog2(PROBES+1))'(PROBES - 1));

  wire r_is_red = (r_type == T_EXEC) || (r_type == T_EXEC_PX) || (r_type == T_CANCEL);
  wire r_is_del = (r_type == T_DELETE);
  wire [31:0] take_qty = (r_is_red && r_shares < rd_shares) ? r_shares : rd_shares;

  always_ff @(posedge clk) begin
    wr_en <= 1'b0;
    lv_valid <= 1'b0;
    tob_valid <= 1'b0;
    if (bid_ovf || ask_ovf) cnt_level_overflow <= cnt_level_overflow + 1;
    if (bid_miss || ask_miss) cnt_level_miss <= cnt_level_miss + 1;
    if (rst) begin
      cs <= C_IDLE;
      base <= '0;
      probe <= '0;
      have_tomb <= 1'b0;
      tomb_addr <= '0;
      {r_type, r_ref, r_new_ref, r_shares, r_price, r_index} <= '0;
      {ins_ref, ins_buy, ins_shares, ins_price} <= '0;
      {wr_addr, wr_state, wr_key, wr_buy, wr_price, wr_shares} <= '0;
      {lv_add, lv_buy, lv_price, lv_qty} <= '0;
      {tob_index, tob_bid_px, tob_bid_qty, tob_ask_px, tob_ask_qty} <= '0;
      {cnt_unknown_ref, cnt_duplicate_ref, cnt_zero_add, cnt_overfill, cnt_bad_len} <= '0;
      {cnt_table_full, cnt_level_overflow, cnt_level_miss} <= '0;
    end else begin
      unique case (cs)
        C_IDLE: if (m_valid) begin
          r_type <= m_type;
          r_ref <= m_ref;
          r_new_ref <= m_new_ref;
          r_shares <= m_shares;
          r_price <= m_price;
          r_index <= m_index;
          probe <= '0;
          have_tomb <= 1'b0;
          if (decoded && !m_len_ok) begin
            cnt_bad_len <= cnt_bad_len + 1;
          end else if (decoded && m_locate == cfg_locate) begin
            if (is_add) begin
              if (m_shares == 32'd0) begin
                cnt_zero_add <= cnt_zero_add + 1;
              end else begin
                ins_ref <= m_ref;
                ins_buy <= m_buy;
                ins_shares <= m_shares;
                ins_price <= m_price;
                base <= hash(m_ref);
                cs <= C_INSERT;
              end
            end else begin
              base <= hash(m_ref);
              cs <= C_FIND;
            end
          end
        end

        // Locate r_ref; on success apply the reduction/delete/replace-out.
        C_FIND: begin
          if (rd_state == FULL && rd_key == r_ref) begin
            wr_en <= 1'b1;
            wr_addr <= rd_addr;
            wr_key <= rd_key;
            wr_buy <= rd_buy;
            wr_price <= rd_price;
            lv_valid <= 1'b1;
            lv_add <= 1'b0;
            lv_buy <= rd_buy;
            lv_price <= rd_price;
            if (r_is_red && r_shares > rd_shares) cnt_overfill <= cnt_overfill + 1;
            if (r_is_red && take_qty != rd_shares) begin  // partial reduction
              wr_state <= FULL;
              wr_shares <= rd_shares - take_qty;
              lv_qty <= take_qty;
            end else begin                                // order leaves the book
              wr_state <= TOMB;
              wr_shares <= '0;
              lv_qty <= rd_shares;
            end
            if (r_is_red || r_is_del) begin
              cs <= C_EMIT;
            end else if (r_shares == 32'd0) begin         // replace with zero shares: add rejected
              cnt_zero_add <= cnt_zero_add + 1;
              cs <= C_EMIT;
            end else begin                                // replace: insert the new order
              ins_ref <= r_new_ref;
              ins_buy <= rd_buy;
              ins_shares <= r_shares;
              ins_price <= r_price;
              base <= hash(r_new_ref);
              probe <= '0;
              have_tomb <= 1'b0;
              cs <= C_INSERT;
            end
          end else if (rd_state == EMPTY || probe_last) begin
            cnt_unknown_ref <= cnt_unknown_ref + 1;
            cs <= C_IDLE;
          end else begin
            probe <= probe + 1'b1;
          end
        end

        // Insert ins_ref unless it already exists. Waits a cycle if the previous state wrote the
        // table this cycle (replace), so the probe sees the tombstone.
        C_INSERT: begin
          if (wr_en) begin
            // table write from C_FIND lands this cycle; probe next cycle
          end else if (rd_state == FULL && rd_key == ins_ref) begin
            cnt_duplicate_ref <= cnt_duplicate_ref + 1;
            cs <= (r_type == T_REPLACE) ? C_EMIT : C_IDLE;
          end else if (rd_state == EMPTY || (probe_last && (have_tomb || rd_state == TOMB))) begin
            wr_en <= 1'b1;
            wr_addr <= have_tomb ? tomb_addr : rd_addr;
            wr_state <= FULL;
            wr_key <= ins_ref;
            wr_buy <= ins_buy;
            wr_price <= ins_price;
            wr_shares <= ins_shares;
            lv_valid <= 1'b1;
            lv_add <= 1'b1;
            lv_buy <= ins_buy;
            lv_price <= ins_price;
            lv_qty <= ins_shares;
            cs <= C_EMIT;
          end else if (probe_last) begin
            cnt_table_full <= cnt_table_full + 1;
            cs <= (r_type == T_REPLACE) ? C_EMIT : C_IDLE;
          end else begin
            if (rd_state == TOMB && !have_tomb) begin
              have_tomb <= 1'b1;
              tomb_addr <= rd_addr;
            end
            probe <= probe + 1'b1;
          end
        end

        // Level update lands at the end of the previous cycle (or this one, for a replace whose
        // second op was skipped); emit once the level registers have settled.
        C_EMIT: begin
          if (lv_valid) begin
            // wait one cycle for the level update issued last cycle
          end else begin
            tob_valid <= 1'b1;
            tob_index <= r_index;
            tob_bid_px <= bid_ok ? bid_px : 32'd0;
            tob_bid_qty <= bid_ok ? bid_qty : '0;
            tob_ask_px <= ask_ok ? ask_px : 32'd0;
            tob_ask_qty <= ask_ok ? ask_qty : '0;
            cs <= C_IDLE;
          end
        end
        default: cs <= C_IDLE;
      endcase
    end
  end
endmodule
