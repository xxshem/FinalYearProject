#pragma once

#include <stddef.h>
#include <string.h>

enum class SyncAckResult {
  Positive,
  Negative,
  Unrelated,
  WrongTransfer,
  Malformed
};

namespace sync_ack_detail {

struct Token {
  char value[65];
  size_t length;
  bool fits;
};

enum class ValueKind { String, Boolean, Other };

struct Value {
  ValueKind kind;
  Token string;
  bool boolean;
};

class Parser {
 public:
  Parser(const char *input, size_t length)
      : input_(input), length_(length), position_(0), valid_(true) {}

  bool parseAck(const char *expected_id, bool &is_positive,
                bool &is_wrong_transfer, bool &is_unrelated) {
    skipWhitespace();
    if (!consume('{')) return false;

    unsigned type_count = 0;
    unsigned id_count = 0;
    unsigned ok_count = 0;
    Value type_value = {};
    Value id_value = {};
    Value ok_value = {};

    skipWhitespace();
    if (!consume('}')) {
      while (valid_) {
        Token key = {};
        if (!parseString(key)) return false;
        skipWhitespace();
        if (!consume(':')) return false;
        skipWhitespace();

        Value value = {};
        if (keyEquals(key, "type")) {
          ++type_count;
          if (!parseValue(1, value)) return false;
          type_value = value;
        } else if (keyEquals(key, "id")) {
          ++id_count;
          if (!parseValue(1, value)) return false;
          id_value = value;
        } else if (keyEquals(key, "ok")) {
          ++ok_count;
          if (!parseValue(1, value)) return false;
          ok_value = value;
        } else if (!parseValue(1, value)) {
          return false;
        }

        skipWhitespace();
        if (consume('}')) break;
        if (!consume(',')) return false;
        skipWhitespace();
      }
    }

    skipWhitespace();
    if (!valid_ || position_ != length_) return false;
    if (type_count > 1 || id_count > 1 || ok_count > 1) return false;
    if (type_count == 0) {
      is_unrelated = true;
      return true;
    }
    if (type_value.kind != ValueKind::String) return false;
    if (!tokenEquals(type_value.string, "SYNC_ACK")) {
      is_unrelated = true;
      return true;
    }
    if (id_count != 1 || ok_count != 1
        || id_value.kind != ValueKind::String
        || ok_value.kind != ValueKind::Boolean
        || !id_value.string.fits) {
      return false;
    }
    if (!expected_id || !tokenEquals(id_value.string, expected_id)) {
      is_wrong_transfer = true;
      return true;
    }
    is_positive = ok_value.boolean;
    return true;
  }

 private:
  const char *input_;
  size_t length_;
  size_t position_;
  bool valid_;

  static bool keyEquals(const Token &token, const char *text) {
    return tokenEquals(token, text);
  }

  static bool tokenEquals(const Token &token, const char *text) {
    size_t length = strlen(text);
    return token.fits && token.length == length
        && memcmp(token.value, text, length) == 0;
  }

  void skipWhitespace() {
    while (position_ < length_) {
      char value = input_[position_];
      if (value != ' ' && value != '\t' && value != '\r' && value != '\n')
        break;
      ++position_;
    }
  }

  bool consume(char expected) {
    if (position_ >= length_ || input_[position_] != expected) return false;
    ++position_;
    return true;
  }

  void append(Token &token, char value) {
    if (token.length < sizeof(token.value) - 1) {
      token.value[token.length] = value;
    } else {
      token.fits = false;
    }
    ++token.length;
  }

  bool parseHex4(unsigned &value) {
    value = 0;
    for (unsigned index = 0; index < 4; ++index) {
      if (position_ >= length_) return false;
      char digit = input_[position_++];
      value <<= 4;
      if (digit >= '0' && digit <= '9') value |= digit - '0';
      else if (digit >= 'a' && digit <= 'f') value |= digit - 'a' + 10;
      else if (digit >= 'A' && digit <= 'F') value |= digit - 'A' + 10;
      else return false;
    }
    return true;
  }

  bool parseString(Token &token) {
    token.length = 0;
    token.fits = true;
    if (!consume('"')) return false;
    while (position_ < length_) {
      unsigned char character = static_cast<unsigned char>(input_[position_++]);
      if (character == '"') {
        token.value[token.length < sizeof(token.value)
                        ? token.length : sizeof(token.value) - 1] = '\0';
        return true;
      }
      if (character < 0x20) return false;
      if (character > 0x7f) {
        if (!consumeUtf8(character)) return false;
        append(token, '?');
        continue;
      }
      if (character != '\\') {
        append(token, static_cast<char>(character));
        continue;
      }
      if (position_ >= length_) return false;
      char escape = input_[position_++];
      switch (escape) {
        case '"': append(token, '"'); break;
        case '\\': append(token, '\\'); break;
        case '/': append(token, '/'); break;
        case 'b': append(token, '\b'); break;
        case 'f': append(token, '\f'); break;
        case 'n': append(token, '\n'); break;
        case 'r': append(token, '\r'); break;
        case 't': append(token, '\t'); break;
        case 'u': {
          unsigned codepoint;
          if (!parseHex4(codepoint)) return false;
          if (codepoint >= 0xd800 && codepoint <= 0xdbff) {
            if (position_ + 2 > length_ || input_[position_] != '\\'
                || input_[position_ + 1] != 'u') return false;
            position_ += 2;
            unsigned low;
            if (!parseHex4(low) || low < 0xdc00 || low > 0xdfff) return false;
            codepoint = 0x10000 + ((codepoint - 0xd800) << 10)
                + (low - 0xdc00);
          } else if (codepoint >= 0xdc00 && codepoint <= 0xdfff) {
            return false;
          }
          append(token, codepoint <= 0x7e
                            ? static_cast<char>(codepoint) : '?');
          break;
        }
        default: return false;
      }
    }
    return false;
  }

  bool consumeUtf8(unsigned char first) {
    unsigned count;
    unsigned char second_min = 0x80;
    unsigned char second_max = 0xbf;
    if (first >= 0xc2 && first <= 0xdf) {
      count = 2;
    } else if (first == 0xe0) {
      count = 3;
      second_min = 0xa0;
    } else if (first >= 0xe1 && first <= 0xec) {
      count = 3;
    } else if (first == 0xed) {
      count = 3;
      second_max = 0x9f;
    } else if (first >= 0xee && first <= 0xef) {
      count = 3;
    } else if (first == 0xf0) {
      count = 4;
      second_min = 0x90;
    } else if (first >= 0xf1 && first <= 0xf3) {
      count = 4;
    } else if (first == 0xf4) {
      count = 4;
      second_max = 0x8f;
    } else {
      return false;
    }

    for (unsigned index = 1; index < count; ++index) {
      if (position_ >= length_) return false;
      unsigned char continuation =
          static_cast<unsigned char>(input_[position_++]);
      unsigned char minimum = index == 1 ? second_min : 0x80;
      unsigned char maximum = index == 1 ? second_max : 0xbf;
      if (continuation < minimum || continuation > maximum) return false;
    }
    return true;
  }

  bool parseLiteral(const char *literal) {
    size_t length = strlen(literal);
    if (length_ - position_ < length
        || memcmp(input_ + position_, literal, length) != 0) return false;
    position_ += length;
    return true;
  }

  bool parseNumber() {
    if (position_ < length_ && input_[position_] == '-') ++position_;
    if (position_ >= length_) return false;
    if (input_[position_] == '0') {
      ++position_;
      if (position_ < length_ && input_[position_] >= '0'
          && input_[position_] <= '9') return false;
    } else {
      if (input_[position_] < '1' || input_[position_] > '9') return false;
      while (position_ < length_ && input_[position_] >= '0'
             && input_[position_] <= '9') ++position_;
    }
    if (position_ < length_ && input_[position_] == '.') {
      ++position_;
      size_t start = position_;
      while (position_ < length_ && input_[position_] >= '0'
             && input_[position_] <= '9') ++position_;
      if (position_ == start) return false;
    }
    if (position_ < length_
        && (input_[position_] == 'e' || input_[position_] == 'E')) {
      ++position_;
      if (position_ < length_
          && (input_[position_] == '+' || input_[position_] == '-'))
        ++position_;
      size_t start = position_;
      while (position_ < length_ && input_[position_] >= '0'
             && input_[position_] <= '9') ++position_;
      if (position_ == start) return false;
    }
    return true;
  }

  bool parseValue(unsigned depth, Value &value) {
    value.kind = ValueKind::Other;
    value.boolean = false;
    value.string.length = 0;
    value.string.fits = true;
    if (position_ >= length_ || depth > 16) return false;
    char first = input_[position_];
    if (first == '"') {
      value.kind = ValueKind::String;
      return parseString(value.string);
    }
    if (first == '{') return parseObject(depth + 1);
    if (first == '[') return parseArray(depth + 1);
    if (first == 't') {
      if (!parseLiteral("true")) return false;
      value.kind = ValueKind::Boolean;
      value.boolean = true;
      return true;
    }
    if (first == 'f') {
      if (!parseLiteral("false")) return false;
      value.kind = ValueKind::Boolean;
      return true;
    }
    if (first == 'n') return parseLiteral("null");
    return parseNumber();
  }

  bool parseObject(unsigned depth) {
    if (depth > 16 || !consume('{')) return false;
    skipWhitespace();
    if (consume('}')) return true;
    while (valid_) {
      Token key = {};
      if (!parseString(key)) return false;
      skipWhitespace();
      if (!consume(':')) return false;
      skipWhitespace();
      Value value = {};
      if (!parseValue(depth, value)) return false;
      skipWhitespace();
      if (consume('}')) return true;
      if (!consume(',')) return false;
      skipWhitespace();
    }
    return false;
  }

  bool parseArray(unsigned depth) {
    if (depth > 16 || !consume('[')) return false;
    skipWhitespace();
    if (consume(']')) return true;
    while (valid_) {
      Value value = {};
      if (!parseValue(depth, value)) return false;
      skipWhitespace();
      if (consume(']')) return true;
      if (!consume(',')) return false;
      skipWhitespace();
    }
    return false;
  }
};

}  // namespace sync_ack_detail

inline SyncAckResult parseSyncAck(const char *message, size_t length,
                                  const char *expected_id) {
  if (!message || length > 255) return SyncAckResult::Malformed;
  bool positive = false;
  bool wrong_transfer = false;
  bool unrelated = false;
  sync_ack_detail::Parser parser(message, length);
  if (!parser.parseAck(expected_id, positive, wrong_transfer, unrelated))
    return SyncAckResult::Malformed;
  if (unrelated) return SyncAckResult::Unrelated;
  if (wrong_transfer) return SyncAckResult::WrongTransfer;
  return positive ? SyncAckResult::Positive : SyncAckResult::Negative;
}
