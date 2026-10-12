"""ตัวสร้าง QR Code แบบ Python ล้วน (ไม่ต้องติดตั้งไลบรารีเพิ่ม)

รองรับข้อความแบบ byte mode, ระดับแก้ข้อผิดพลาด M, เวอร์ชัน 1–10 (ข้อความยาวได้ราว 200 ไบต์)
อัลกอริทึมดัดแปลงจาก "QR Code generator library" ของ Project Nayuki (MIT License)
"""

# ระดับ M: จำนวน ECC codeword ต่อบล็อก และจำนวนบล็อก (index = เวอร์ชัน)
_ECC_PER_BLOCK = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
_NUM_BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]
_ECL_FORMAT_BITS = 0  # M = 0b00
_PENALTY_N1, _PENALTY_N2, _PENALTY_N3, _PENALTY_N4 = 3, 3, 40, 10


def _raw_data_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        result -= (25 * align - 10) * align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver):
    return _raw_data_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _NUM_BLOCKS[ver]


# ---------------- Reed–Solomon บน GF(256) ----------------
def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---------------- ตัวสร้าง ----------------
class _QR:
    def __init__(self, ver, codewords, mask):
        self.ver = ver
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        self._draw_codewords(codewords)
        self._apply_mask(mask)
        self._draw_format(mask)

    def _set_fn(self, x, y, dark):
        self.mod[y][x] = dark
        self.fn[y][x] = True

    def _draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self._set_fn(6, i, i % 2 == 0)
            self._set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        dist = max(abs(dx), abs(dy))
                        self._set_fn(x, y, dist not in (2, 4))
        pos = self._alignment_positions()
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_fn(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format(0)
        self._draw_version()

    def _alignment_positions(self):
        if self.ver == 1:
            return []
        count = self.ver // 7 + 2
        step = (self.ver * 8 + count * 3 + 5) // (count * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(count - 1)] + [6]
        return list(reversed(result))

    def _draw_format(self, mask):
        data = _ECL_FORMAT_BITS << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 != 0  # noqa: E731
        for i in range(0, 6):
            self._set_fn(8, i, bit(i))
        self._set_fn(8, 7, bit(6))
        self._set_fn(8, 8, bit(7))
        self._set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self._set_fn(14 - i, 8, bit(i))
        n = self.size
        for i in range(0, 8):
            self._set_fn(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_fn(8, n - 15 + i, bit(i))
        self._set_fn(8, n - 8, True)

    def _draw_version(self):
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a, b = self.size - 11 + i % 3, i // 3
            self._set_fn(a, b, dark)
            self._set_fn(b, a, dark)

    def _draw_codewords(self, data):
        i, n = 0, self.size
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = (n - 1 - vert) if upward else vert
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.mod[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def _apply_mask(self, mask):
        rules = [
            lambda x, y: (x + y) % 2,
            lambda x, y: y % 2,
            lambda x, y: x % 3,
            lambda x, y: (x + y) % 3,
            lambda x, y: (x // 3 + y // 2) % 2,
            lambda x, y: x * y % 2 + x * y % 3,
            lambda x, y: (x * y % 2 + x * y % 3) % 2,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2,
        ]
        f = rules[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y) == 0:
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self):
        n, result, m = self.size, 0, self.mod

        def runs(line):
            score, run_color, run_len, hist = 0, False, 0, [0] * 7
            def finder_count(h):  # noqa: E306
                k = h[1]
                core = k > 0 and h[2] == k and h[3] == k * 3 and h[4] == k and h[5] == k
                return (1 if core and h[0] >= k * 4 and h[6] >= k else 0) + (1 if core and h[6] >= k * 4 and h[0] >= k else 0)
            def add_hist(length):  # noqa: E306
                if hist[0] == 0:
                    length += n
                hist.pop()
                hist.insert(0, length)
            for cell in line:
                if cell == run_color:
                    run_len += 1
                    if run_len == 5:
                        score += _PENALTY_N1
                    elif run_len > 5:
                        score += 1
                else:
                    add_hist(run_len)
                    if not run_color:
                        score += finder_count(hist) * _PENALTY_N3
                    run_color, run_len = cell, 1
            # ปิดท้ายบรรทัด
            if run_color:
                add_hist(run_len)
                run_len = 0
            run_len += n
            add_hist(run_len)
            score += finder_count(hist) * _PENALTY_N3
            return score

        for y in range(n):
            result += runs(m[y])
        for x in range(n):
            result += runs([m[y][x] for y in range(n)])
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    result += _PENALTY_N2
        dark = sum(sum(1 for c in row if c) for row in m)
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * _PENALTY_N4
        return result


def encode(text):
    """คืนค่าเมทริกซ์ (list ของ list bool) ของ QR Code สำหรับข้อความ text"""
    data = text.encode("utf-8")
    for ver in range(1, 11):
        cc_bits = 8 if ver < 10 else 16
        if 4 + cc_bits + len(data) * 8 <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("ข้อความยาวเกินไปสำหรับ QR")

    bits = []
    def append(val, length):  # noqa: E306
        bits.extend((val >> i) & 1 for i in reversed(range(length)))
    append(0b0100, 4)
    append(len(data), cc_bits)
    for b in data:
        append(b, 8)
    capacity = _data_codewords(ver) * 8
    append(0, min(4, capacity - len(bits)))
    append(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        append(pad, 8)
        pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # แบ่งบล็อก + ECC + สลับลำดับ
    num_blocks, ecc_len = _NUM_BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_data_modules(ver) // 8
    short_blocks = num_blocks - raw % num_blocks
    short_len = raw // num_blocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(num_blocks):
        dat = codewords[k: k + short_len - ecc_len + (0 if i < short_blocks else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < short_blocks:
            dat = dat + [0]
        blocks.append(dat + ecc)
    final = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:
                final.append(blk[i])

    best = min((_QR(ver, final, mask) for mask in range(8)), key=lambda q: q.penalty())
    return best.mod


def to_svg(text, label="QR Code", border=2):
    """สร้าง SVG ของ QR Code (รวมเป็น path เดียว ขนาดเล็ก)"""
    matrix = encode(text)
    n = len(matrix)
    size = n + border * 2
    parts = [f"M{x + border},{y + border}h1v1h-1z"
             for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark]
    return (f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges" '
            f'role="img" aria-label="{label}"><rect width="{size}" height="{size}" fill="#fff"/>'
            f'<path d="{"".join(parts)}" fill="#111"/></svg>')
