// One side of a price-level book (M7, ADR 0007): up to L levels kept sorted best-first in registers.
// One operation per cycle: add shares at a price (inserting a level) or remove shares (deleting the
// level when it empties). If an insert finds the array full, the worst level is dropped and
// `overflow` pulses; the book is then only exact for the levels that remain.
module tf_price_levels #(
  parameter int L     = 64,
  parameter bit IS_BID = 1'b1,
  parameter int QTY_W = 40
) (
  input  logic             clk,
  input  logic             rst,
  input  logic             op_valid,
  input  logic             op_add,      // 1 = add shares, 0 = remove shares
  input  logic [31:0]      op_price,
  input  logic [31:0]      op_qty,
  output logic             best_valid,
  output logic [31:0]      best_price,
  output logic [QTY_W-1:0] best_qty,
  output logic             overflow,    // pulse: insert dropped the worst level
  output logic             miss         // pulse: remove for a price not held
);
  logic             v   [L];
  logic [31:0]      px  [L];
  logic [QTY_W-1:0] qty [L];

  assign best_valid = v[0];
  assign best_price = v[0] ? px[0] : 32'd0;
  assign best_qty   = v[0] ? qty[0] : '0;

  function automatic logic better(input logic [31:0] a, input logic [31:0] b);
    better = IS_BID ? (a > b) : (a < b);
  endfunction

  // Match index and insertion position (valid entries form a prefix, sorted best-first).
  localparam int IW = $clog2(L);
  logic                   hit;
  logic [IW-1:0]          hit_idx;
  logic [$clog2(L+1)-1:0] ins_pos;   // can equal L (worse than every held level)
  wire  [IW-1:0]          ins_idx = ins_pos[IW-1:0];
  always_comb begin
    hit = 1'b0;
    hit_idx = '0;
    ins_pos = '0;
    for (int i = 0; i < L; i++) begin
      if (v[i] && px[i] == op_price && !hit) begin
        hit = 1'b1;
        hit_idx = i[IW-1:0];
      end
      if (v[i] && better(px[i], op_price)) ins_pos = ins_pos + 1'b1;
    end
  end

  wire [QTY_W-1:0] op_qty_w = {{(QTY_W-32){1'b0}}, op_qty};

  always_ff @(posedge clk) begin
    overflow <= 1'b0;
    miss <= 1'b0;
    if (rst) begin
      for (int i = 0; i < L; i++) begin
        v[i] <= 1'b0;
        px[i] <= '0;
        qty[i] <= '0;
      end
    end else if (op_valid) begin
      if (op_add) begin
        if (hit) begin
          qty[hit_idx] <= qty[hit_idx] + op_qty_w;
        end else begin
          if (v[L-1]) overflow <= 1'b1;
          for (int i = L - 1; i > 0; i--)
            if (i > int'(ins_pos)) begin
              v[i] <= v[i-1];
              px[i] <= px[i-1];
              qty[i] <= qty[i-1];
            end
          if (int'(ins_pos) < L) begin
            v[ins_idx] <= 1'b1;
            px[ins_idx] <= op_price;
            qty[ins_idx] <= op_qty_w;
          end
        end
      end else if (!hit) begin
        miss <= 1'b1;
      end else if (qty[hit_idx] == op_qty_w) begin  // level empties: close the gap
        for (int i = 0; i < L - 1; i++)
          if (i >= int'(hit_idx)) begin
            v[i] <= v[i+1];
            px[i] <= px[i+1];
            qty[i] <= qty[i+1];
          end
        v[L-1] <= 1'b0;
      end else begin
        qty[hit_idx] <= qty[hit_idx] - op_qty_w;
      end
    end
  end
endmodule
