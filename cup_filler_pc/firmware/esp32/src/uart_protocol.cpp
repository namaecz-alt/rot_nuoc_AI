#include "uart_protocol.h"

#include "link_io.h"

#include <string.h>

namespace cupfiller {

// ---------------------------------------------------------------------------
uint8_t frame_checksum(const char *body, size_t len) {
  uint32_t sum = 0;
  for (size_t i = 0; i < len; ++i) sum += (uint8_t)body[i];
  return (uint8_t)(sum & 0xFF);
}

static char hex_nibble(uint8_t v) {
  v &= 0x0F;
  return (char)(v < 10 ? ('0' + v) : ('a' + v - 10));
}

size_t encode_frame(char *out, size_t out_cap, const char *type, const char *args) {
  if (out == NULL || type == NULL) return 0;
  char body[kMaxBody];
  size_t n = 0;
  for (const char *p = type; *p && n + 1 < kMaxBody; ++p) body[n++] = *p;
  if (args && args[0]) {
    if (n + 1 < kMaxBody) body[n++] = ',';
    for (const char *p = args; *p && n + 1 < kMaxBody; ++p) {
      if (*p == '$' || *p == '*' || *p == '\n' || *p == '\r') break;  // ky tu cam
      body[n++] = *p;
    }
  }
  body[n] = '\0';

  const uint8_t crc = frame_checksum(body, n);
  // can: '$' + body + '*' + 2 hex + '\n' + '\0'
  if (out_cap < n + 6) return 0;
  size_t k = 0;
  out[k++] = '$';
  memcpy(out + k, body, n);
  k += n;
  out[k++] = '*';
  out[k++] = hex_nibble((uint8_t)(crc >> 4));
  out[k++] = hex_nibble(crc);
  out[k++] = '\n';
  out[k] = '\0';
  return k;
}

size_t send_frame(const char *type, const char *args) {
  char buf[kMaxBody + 8];
  const size_t n = encode_frame(buf, sizeof(buf), type, args);
  if (n == 0) return 0;
  link_write((const uint8_t *)buf, n);
  return n;
}

// ---------------------------------------------------------------------------
void FrameParser::reset() {
  st_ = S_IDLE;
  n_ = 0;
  crc_ = 0;
  body_[0] = '\0';
  type_[0] = '\0';
  args_[0] = '\0';
}

static int hex_val(uint8_t c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool FrameParser::feed(uint8_t b) {
  const char c = (char)b;

  if (c == '$') {  // bat dau khuung moi (ke ca khi dang giua khuung -> dong bo)
    st_ = S_BODY;
    n_ = 0;
    crc_ = 0;
    return false;
  }

  switch (st_) {
    case S_IDLE:
      return false;  // rac ngoai khuung: bo qua

    case S_BODY:
      if (c == '*') {
        body_[n_] = '\0';
        st_ = S_CRC1;
        return false;
      }
      if (c == '\n' || c == '\r') {  // khuung khong co '*' -> bo, doi khuung khac
        st_ = S_IDLE;
        ++errors_;
        return false;
      }
      if (n_ + 1 >= kMaxBody) {  // tran: huy khuung
        st_ = S_IDLE;
        ++errors_;
        return false;
      }
      body_[n_++] = c;
      return false;

    case S_CRC1: {
      const int hi = hex_val(b);
      if (hi < 0) {
        st_ = S_IDLE;
        ++errors_;
        return false;
      }
      crc_ = (uint8_t)(hi << 4);
      st_ = S_CRC2;
      return false;
    }

    case S_CRC2: {
      const int lo = hex_val(b);
      if (lo < 0) {
        st_ = S_IDLE;
        ++errors_;
        return false;
      }
      crc_ |= (uint8_t)lo;
      st_ = S_IDLE;
      if (crc_ != frame_checksum(body_, n_)) {
        ++errors_;
        return false;
      }
      // tach TYPE va ARGS o dau phan cach dau tien
      char *comma = strchr(body_, ',');
      if (comma == NULL) {
        strncpy(type_, body_, kMaxBody - 1);
        type_[kMaxBody - 1] = '\0';
        args_[0] = '\0';
      } else {
        const size_t tlen = (size_t)(comma - body_);
        memcpy(type_, body_, tlen);
        type_[tlen] = '\0';
        strncpy(args_, comma + 1, kMaxBody - 1);
        args_[kMaxBody - 1] = '\0';
      }
      return type_[0] != '\0';
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
bool get_arg(const char *args, int index, char *out, size_t cap) {
  if (out == NULL || cap == 0) return false;
  out[0] = '\0';
  if (args == NULL) return false;
  int seen = 0;
  const char *p = args;
  while (index > 0) {
    p = strchr(p, ',');
    if (p == NULL) return false;
    ++p;
    --index;
    ++seen;
  }
  (void)seen;
  const char *end = strchr(p, ',');
  const size_t len = (end == NULL) ? strlen(p) : (size_t)(end - p);
  if (len + 1 > cap) return false;
  memcpy(out, p, len);
  out[len] = '\0';
  return true;
}

int get_arg_int(const char *args, int index, int def) {
  char tmp[16];
  if (!get_arg(args, index, tmp, sizeof(tmp))) return def;
  // strtol khong can <stdlib.h> o day? co: dung ham tu viet cho gon va an toan
  int sign = 1, i = 0, val = 0;
  if (tmp[i] == '-') { sign = -1; ++i; }
  if (tmp[i] == '\0') return def;
  for (; tmp[i]; ++i) {
    if (tmp[i] < '0' || tmp[i] > '9') return def;
    val = val * 10 + (tmp[i] - '0');
    if (val > 1000000) return def;
  }
  return sign * val;
}

}  // namespace cupfiller
