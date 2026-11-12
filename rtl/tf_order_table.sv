// Order table for the feed handler (M7, ADR 0007): open-addressing hash memory keyed by ITCH order
// reference. Combinational read port (one probe per cycle), synchronous write port. Slots are
// EMPTY, FULL or TOMB (deleted); the probing policy lives in tf_itch_book.
module tf_order_table #(
  parameter int AW = 12  // 2^AW slots
) (
  input  logic          clk,
  input  logic          rst,
  input  logic [AW-1:0] rd_addr,
  output logic [1:0]    rd_state,
  output logic [63:0]   rd_key,
  output logic          rd_buy,
  output logic [31:0]   rd_price,
  output logic [31:0]   rd_shares,
  input  logic          wr_en,
  input  logic [AW-1:0] wr_addr,
  input  logic [1:0]    wr_state,
  input  logic [63:0]   wr_key,
  input  logic          wr_buy,
  input  logic [31:0]   wr_price,
  input  logic [31:0]   wr_shares
);
  localparam int N = 1 << AW;
  logic [1:0]  st  [N];
  logic [63:0] key [N];
  logic        buy [N];
  logic [31:0] px  [N];
  logic [31:0] sh  [N];

  assign rd_state  = st[rd_addr];
  assign rd_key    = key[rd_addr];
  assign rd_buy    = buy[rd_addr];
  assign rd_price  = px[rd_addr];
  assign rd_shares = sh[rd_addr];

  always_ff @(posedge clk) begin
    if (rst) begin
      for (int i = 0; i < N; i++) st[i] <= 2'd0;
    end else if (wr_en) begin
      st[wr_addr]  <= wr_state;
      key[wr_addr] <= wr_key;
      buy[wr_addr] <= wr_buy;
      px[wr_addr]  <= wr_price;
      sh[wr_addr]  <= wr_shares;
    end
  end
endmodule
