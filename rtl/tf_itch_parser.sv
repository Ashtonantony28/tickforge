// Byte-stream ITCH 5.0 parser (M7, ADR 0007).
//
// Input: one byte per cycle (valid/ready), framed as in Nasdaq sample files: 2-byte big-endian length,
// then the payload. Output: one decoded record per message (valid/ready), carrying the fields the
// book needs and the message's index in the stream (every message counts, decoded or not, so the
// index matches the C++ engine's).
//
// Fields are captured by byte offset while the message streams in. A completed message is copied to
// the output register on the following cycle; the input stalls only while a completed message is
// waiting for a busy output register. Sustained rate: one byte per cycle.
module tf_itch_parser
  import tf_itch_pkg::*;
#(
  parameter int IDX_W = 32
) (
  input  logic             clk,
  input  logic             rst,
  // byte stream
  input  logic             s_valid,
  input  logic [7:0]       s_data,
  output logic             s_ready,
  // decoded message
  output logic             m_valid,
  input  logic             m_ready,
  output logic [7:0]       m_type,
  output logic             m_len_ok,   // payload length matches the spec for a decoded type
  output logic [15:0]      m_locate,
  output logic [63:0]      m_ref,
  output logic [63:0]      m_new_ref,
  output logic             m_buy,
  output logic [31:0]      m_shares,
  output logic [31:0]      m_price,
  output logic [IDX_W-1:0] m_index
);
  typedef enum logic [1:0] {S_LEN0, S_LEN1, S_BODY} state_t;
  state_t state;

  logic [7:0]  len_hi;
  logic [15:0] len;
  logic [15:0] cnt;
  logic [7:0]  f_type;
  logic [15:0] f_locate;
  logic [63:0] f_ref, f_new_ref;
  logic [7:0]  f_side;
  logic [31:0] f_shares, f_price;
  logic        done_pending;   // a message completed and awaits the output register
  logic [IDX_W-1:0] index;

  wire out_free = !m_valid || m_ready;
  // A completed message is copied out in the same cycle the next byte (always a length byte, never
  // a field byte) is accepted, so field registers are never overwritten before they are copied.
  assign s_ready = !done_pending || out_free;
  wire take = s_valid && s_ready;

  wire is_add  = (f_type == T_ADD) || (f_type == T_ADD_MPID);
  wire is_red  = (f_type == T_EXEC) || (f_type == T_EXEC_PX) || (f_type == T_CANCEL);
  wire is_repl = (f_type == T_REPLACE);

  always_ff @(posedge clk) begin
    if (rst) begin
      state <= S_LEN0;
      len_hi <= '0;
      len <= '0;
      cnt <= '0;
      done_pending <= 1'b0;
      index <= '0;
      m_valid <= 1'b0;
      f_type <= '0;
      f_locate <= '0;
      f_ref <= '0;
      f_new_ref <= '0;
      f_side <= '0;
      f_shares <= '0;
      f_price <= '0;
      m_type <= '0;
      m_len_ok <= 1'b0;
      m_locate <= '0;
      m_ref <= '0;
      m_new_ref <= '0;
      m_buy <= 1'b0;
      m_shares <= '0;
      m_price <= '0;
      m_index <= '0;
    end else begin
      if (m_valid && m_ready) m_valid <= 1'b0;

      // Hand a completed message to the output register.
      if (done_pending && out_free) begin
        m_valid <= 1'b1;
        m_type <= f_type;
        m_len_ok <= (len != 16'd0) && (decoded_len(f_type) == len);
        m_locate <= f_locate;
        m_ref <= f_ref;
        m_new_ref <= f_new_ref;
        m_buy <= (f_side == SIDE_BUY);
        m_shares <= f_shares;
        m_price <= f_price;
        m_index <= index;
        index <= index + 1'b1;
        done_pending <= 1'b0;
      end

      if (take) begin
        unique case (state)
          S_LEN0: begin
            len_hi <= s_data;
            state <= S_LEN1;
          end
          S_LEN1: begin
            len <= {len_hi, s_data};
            cnt <= '0;
            if ({len_hi, s_data} == 16'd0) begin  // empty message: counts, decodes to nothing
              f_type <= 8'h00;
              done_pending <= 1'b1;
              state <= S_LEN0;
            end else begin
              state <= S_BODY;
            end
          end
          S_BODY: begin
            if (cnt == 16'd0) f_type <= s_data;
            if (cnt >= 16'd1 && cnt <= 16'd2) f_locate <= {f_locate[7:0], s_data};
            if (cnt >= 16'd11 && cnt <= 16'd18) f_ref <= {f_ref[55:0], s_data};
            if (is_add) begin
              if (cnt == 16'd19) f_side <= s_data;
              if (cnt >= 16'd20 && cnt <= 16'd23) f_shares <= {f_shares[23:0], s_data};
              if (cnt >= 16'd32 && cnt <= 16'd35) f_price <= {f_price[23:0], s_data};
            end
            if (is_red && cnt >= 16'd19 && cnt <= 16'd22) f_shares <= {f_shares[23:0], s_data};
            if (is_repl) begin
              if (cnt >= 16'd19 && cnt <= 16'd26) f_new_ref <= {f_new_ref[55:0], s_data};
              if (cnt >= 16'd27 && cnt <= 16'd30) f_shares <= {f_shares[23:0], s_data};
              if (cnt >= 16'd31 && cnt <= 16'd34) f_price <= {f_price[23:0], s_data};
            end
            cnt <= cnt + 16'd1;
            if (cnt == len - 16'd1) begin
              done_pending <= 1'b1;
              state <= S_LEN0;
            end
          end
          default: state <= S_LEN0;
        endcase
      end
    end
  end
endmodule
