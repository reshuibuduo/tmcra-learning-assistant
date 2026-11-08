"""Expose only top-level JSON string prefixes; never expose syntax or citations."""
import json
import re

LIMIT = 32768
FIELDS = {'answer': 1600, 'example': 400}

def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)

def string_prefix(text, start):
    out = []; i = start + 1
    escapes = {'"':'"', '\\':'\\', '/':'/', 'b':'\b', 'f':'\f', 'n':'\n', 'r':'\r', 't':'\t'}
    while i < len(text):
        char = text[i]
        if char == '"': break
        if ord(char) < 32: raise ValueError('invalid JSON control character')
        if char != '\\': out.append(char); i += 1; continue
        if i + 1 >= len(text): break
        char = text[i+1]
        if char in escapes: out.append(escapes[char]); i += 2; continue
        if char != 'u': raise ValueError('invalid JSON escape')
        if i + 6 > len(text): break
        digits = text[i+2:i+6]
        if not re.fullmatch('[0-9a-fA-F]{4}', digits): raise ValueError('invalid unicode escape')
        code = int(digits, 16); step = 6
        if 0xD800 <= code <= 0xDBFF:
            if i + 12 > len(text): break
            tail = text[i+6:i+12]
            if not re.fullmatch(r'\\u[0-9a-fA-F]{4}', tail): raise ValueError('unpaired surrogate')
            low = int(tail[2:], 16)
            if not 0xDC00 <= low <= 0xDFFF: raise ValueError('unpaired surrogate')
            code = 0x10000 + ((code-0xD800) << 10) + low-0xDC00; step = 12
        elif 0xDC00 <= code <= 0xDFFF: raise ValueError('unpaired surrogate')
        out.append(chr(code)); i += step
    return ''.join(out)

def partial_fields(text):
    if len(text) > LIMIT: raise ValueError('stream output too long')
    decoder = json.JSONDecoder(); values = {}; seen = set(); i = 0
    def whitespace(pos):
        while pos < len(text) and text[pos] in ' \r\n\t': pos += 1
        return pos
    i = whitespace(i)
    if i == len(text): return values
    if text[i] != '{': raise ValueError('stream must be a JSON object')
    i += 1
    while True:
        i = whitespace(i)
        if i >= len(text) or text[i] == '}': break
        try: key, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError: break
        if not isinstance(key, str) or key in seen: raise ValueError('invalid or duplicate JSON key')
        seen.add(key); i = whitespace(end)
        if i >= len(text): break
        if text[i] != ':': raise ValueError('missing JSON colon')
        i = whitespace(i+1)
        if i >= len(text): break
        try: value, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            if key in FIELDS and text[i] == '"': values[key] = string_prefix(text, i)
            break
        if key in FIELDS:
            if not isinstance(value, str): raise ValueError('invalid text field')
            values[key] = value
        i = whitespace(end)
        if i >= len(text) or text[i] == '}': break
        if text[i] != ',': raise ValueError('missing JSON comma')
        i += 1
    for key, value in values.items():
        if len(value) > FIELDS[key]: raise ValueError('stream field too long')
        # JSONDecoder accepts escaped isolated surrogates; do not transmit them.
        value.encode('utf-8', errors='strict')
    return values
