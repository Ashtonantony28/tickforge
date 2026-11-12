// Shared constants for the ITCH feed handler (M7, ADR 0007). Message types and payload lengths
// from the Nasdaq TotalView-ITCH 5.0 specification; these mirror engine/include/tickforge/itch.hpp.
package tf_itch_pkg;
  localparam logic [7:0] T_ADD       = 8'h41;  // 'A'
  localparam logic [7:0] T_ADD_MPID  = 8'h46;  // 'F'
  localparam logic [7:0] T_EXEC      = 8'h45;  // 'E'
  localparam logic [7:0] T_EXEC_PX   = 8'h43;  // 'C'
  localparam logic [7:0] T_CANCEL    = 8'h58;  // 'X'
  localparam logic [7:0] T_DELETE    = 8'h44;  // 'D'
  localparam logic [7:0] T_REPLACE   = 8'h55;  // 'U'
  localparam logic [7:0] SIDE_BUY    = 8'h42;  // 'B'

  localparam logic [15:0] LEN_ADD      = 16'd36;
  localparam logic [15:0] LEN_ADD_MPID = 16'd40;
  localparam logic [15:0] LEN_EXEC     = 16'd31;
  localparam logic [15:0] LEN_EXEC_PX  = 16'd36;
  localparam logic [15:0] LEN_CANCEL   = 16'd23;
  localparam logic [15:0] LEN_DELETE   = 16'd19;
  localparam logic [15:0] LEN_REPLACE  = 16'd35;

  // Expected payload length for the types the book decodes; 0 = type not decoded.
  function automatic logic [15:0] decoded_len(input logic [7:0] t);
    case (t)
      T_ADD:      decoded_len = LEN_ADD;
      T_ADD_MPID: decoded_len = LEN_ADD_MPID;
      T_EXEC:     decoded_len = LEN_EXEC;
      T_EXEC_PX:  decoded_len = LEN_EXEC_PX;
      T_CANCEL:   decoded_len = LEN_CANCEL;
      T_DELETE:   decoded_len = LEN_DELETE;
      T_REPLACE:  decoded_len = LEN_REPLACE;
      default:    decoded_len = 16'd0;
    endcase
  endfunction
endpackage
