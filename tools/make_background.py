#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
프로그램 배경 이미지(assets/background.png)를 생성한다.

업로드된 일러스트(어두운 보라 배경에서 곰이 TV를 보며 팝콘을 먹는 그림)를
표준 라이브러리만으로 재현한다. 위쪽 절반에 그림을 그리고, 아래쪽은 같은
보라색 단색으로 두어 그 위에 프로그램 컨트롤 패널이 자연스럽게 얹히도록 한다.

원본 그림 파일이 있다면 이 스크립트를 쓰지 않고 그냥
assets/background.png 를 720x720 크기로 직접 덮어써도 된다.
"""

import os
import struct
import zlib

W = H = 720          # 최종 이미지 크기 (정사각형)
SS = 2               # 슈퍼샘플링 배수 (가장자리 부드럽게)
WS, HS = W * SS, H * SS

PURPLE = (40, 21, 56)   # 배경 보라색 (아래 패널과 동일하게 맞춤)

buf = bytearray()
for _ in range(WS * HS):
    buf += bytes(PURPLE)


def put(x, y, color):
    if 0 <= x < WS and 0 <= y < HS:
        i = (y * WS + x) * 3
        buf[i:i + 3] = bytes(color)


def blend(x, y, color, a):
    if 0 <= x < WS and 0 <= y < HS:
        i = (y * WS + x) * 3
        for k in range(3):
            buf[i + k] = int(buf[i + k] * (1 - a) + color[k] * a)


def S(v):
    return int(round(v * SS))


def fill_rect(x0, y0, x1, y1, color):
    for y in range(S(y0), S(y1)):
        for x in range(S(x0), S(x1)):
            put(x, y, color)


def fill_circle(cx, cy, r, color):
    cx, cy, r = S(cx), S(cy), S(r)
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                put(x, y, color)


def fill_ellipse(cx, cy, rx, ry, color):
    cx, cy, rx, ry = S(cx), S(cy), S(rx), S(ry)
    for y in range(cy - ry, cy + ry + 1):
        for x in range(cx - rx, cx + rx + 1):
            if rx and ry and ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                put(x, y, color)


def fill_round_rect(x0, y0, x1, y1, r, color):
    x0, y0, x1, y1, r = S(x0), S(y0), S(x1), S(y1), S(r)
    for y in range(y0, y1):
        for x in range(x0, x1):
            # 모서리 둥글게: 코너 영역이면 원 안쪽만 채움
            cx = min(max(x, x0 + r), x1 - r)
            cy = min(max(y, y0 + r), y1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                put(x, y, color)


def _poly_bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def fill_polygon(pts, color, alpha=1.0):
    pts = [(S(px), S(py)) for px, py in pts]
    x0, y0, x1, y1 = _poly_bbox(pts)
    n = len(pts)
    for y in range(int(y0), int(y1) + 1):
        nodes = []
        j = n - 1
        for i in range(n):
            yi, yj = pts[i][1], pts[j][1]
            if (yi < y <= yj) or (yj < y <= yi):
                xi, xj = pts[i][0], pts[j][0]
                nodes.append(xi + (y - yi) / (yj - yi) * (xj - xi))
            j = i
        nodes.sort()
        for k in range(0, len(nodes) - 1, 2):
            for x in range(int(nodes[k]), int(nodes[k + 1]) + 1):
                if alpha >= 1.0:
                    put(x, y, color)
                else:
                    blend(x, y, color, alpha)


def stripe_box(pts, white, red):
    """팝콘 통: 흰 바탕에 빨간 세로 줄무늬."""
    fill_polygon(pts, white)
    x0, y0, x1, y1 = _poly_bbox(pts)
    width = x1 - x0
    nstripe = 5
    sw = width / (nstripe * 2 - 1)
    for s in range(nstripe):
        sx = x0 + s * 2 * sw
        # 사다리꼴 모양을 따라가도록 단순히 세로 띠로 근사
        fill_polygon([(sx, y0), (sx + sw, y0), (sx + sw, y1), (sx, y1)], red)


# ---------------- 장면 그리기 ----------------
def draw_scene():
    # TV 불빛 (붉은 보라 빛줄기) - 물체보다 먼저
    fill_polygon([(205, 210), (205, 250), (618, 120), (618, 325)],
                 (120, 55, 78), alpha=0.28)

    # 탁자 (검정)
    fill_polygon([(95, 320), (252, 320), (236, 340), (110, 340)], (12, 9, 18))
    fill_rect(110, 340, 122, 366, (12, 9, 18))
    fill_rect(226, 340, 238, 366, (12, 9, 18))

    # TV
    fill_round_rect(110, 218, 216, 320, 13, (120, 150, 165))
    fill_round_rect(121, 230, 186, 309, 8, (78, 112, 130))   # 화면
    # 스피커 줄
    for yy in (250, 268, 286):
        fill_rect(192, yy, 207, yy + 4, (70, 95, 112))
    # 안테나
    fill_polygon([(159, 222), (163, 222), (140, 180), (136, 181)], (70, 80, 90))
    fill_polygon([(161, 222), (165, 222), (192, 176), (188, 176)], (70, 80, 90))

    # 빨간 안락의자
    red = (205, 55, 48)
    fill_round_rect(470, 208, 612, 318, 22, red)     # 등받이
    fill_round_rect(446, 232, 490, 352, 18, red)     # 왼쪽 팔걸이
    fill_round_rect(600, 232, 644, 352, 18, red)     # 오른쪽 팔걸이
    fill_round_rect(446, 315, 644, 360, 16, red)     # 좌석 받침
    fill_round_rect(462, 305, 630, 352, 14, (224, 74, 62))  # 쿠션

    # 갈색 곰
    brown = (170, 120, 72)
    fill_round_rect(516, 300, 582, 356, 16, brown)   # 몸통
    fill_circle(548, 262, 38, brown)                 # 머리
    fill_circle(522, 233, 13, brown)                 # 귀
    fill_circle(574, 233, 13, brown)
    fill_circle(522, 233, 6, (120, 80, 50))          # 귀 안쪽
    fill_circle(574, 233, 6, (120, 80, 50))
    fill_ellipse(548, 278, 19, 14, (208, 165, 115))  # 주둥이
    fill_circle(536, 259, 3, (30, 20, 15))           # 눈
    fill_circle(561, 259, 3, (30, 20, 15))
    fill_circle(548, 272, 4, (45, 28, 20))           # 코

    # 곰이 든 팝콘 통
    stripe_box([(526, 312), (570, 312), (565, 354), (531, 354)],
               (238, 235, 228), (212, 62, 56))
    for (px, py) in [(529, 308), (539, 305), (548, 307), (558, 305), (566, 308)]:
        fill_circle(px, py, 6, (236, 206, 122))

    # 의자 위 하얀 곰
    white = (240, 240, 235)
    fill_circle(560, 190, 28, white)
    fill_circle(543, 170, 10, white)
    fill_circle(577, 170, 10, white)
    fill_circle(551, 189, 3, (60, 50, 55))           # 눈
    fill_circle(569, 189, 3, (60, 50, 55))
    fill_ellipse(560, 200, 6, 3, (210, 150, 160))    # 볼/입

    # 바닥 팝콘 통
    stripe_box([(582, 330), (638, 330), (630, 362), (590, 362)],
               (238, 235, 228), (212, 62, 56))
    for (px, py) in [(586, 326), (596, 322), (606, 325), (616, 322), (626, 326)]:
        fill_circle(px, py, 6, (236, 206, 122))


def downscale_and_encode():
    # 슈퍼샘플 버퍼를 최종 크기로 평균 다운스케일
    raw = bytearray()
    for y in range(H):
        raw.append(0)  # PNG 각 행 필터 바이트
        for x in range(W):
            r = g = b = 0
            for dy in range(SS):
                for dx in range(SS):
                    i = ((y * SS + dy) * WS + (x * SS + dx)) * 3
                    r += buf[i]; g += buf[i + 1]; b += buf[i + 2]
            n = SS * SS
            raw += bytes((r // n, g // n, b // n))

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return png


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(os.path.dirname(here), "assets")
    os.makedirs(out_dir, exist_ok=True)
    draw_scene()
    data = downscale_and_encode()
    out = os.path.join(out_dir, "background.png")
    with open(out, "wb") as f:
        f.write(data)
    print(f"생성 완료: {out} ({len(data)} bytes, {W}x{H})")


if __name__ == "__main__":
    main()
