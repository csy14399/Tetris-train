# -*- coding: utf-8 -*-
"""
本地俄罗斯方块 - Python + Pygame 单文件实现
特性：
- 10x20 经典棋盘，7-Bag 随机
- 旋转+简单踢墙（Wall Kick），O 块定轴
- Hold（保留）、Next（预览，显示前5个）
- Ghost（影子）、软降 & 硬降计分
- 消行计分、等级与重力加速、暂停/重启
作者：你 + ChatGPT
"""

import sys
import random
import pygame

# === 配置 ===
COLS, ROWS = 10, 20        # 棋盘尺寸
CELL = 32                  # 方块像素尺寸
MARGIN = 20                # 画面边距
SIDE_W = 200               # 侧栏宽度（Hold/Next/分数）
FPS = 60                   # 帧率
LOCK_DELAY = 500           # 触底锁定延迟(ms)
GHOST_ALPHA = 60           # 幽灵块透明度(0-255)
PREVIEW_COUNT = 5          # Next 预览数量
SOFT_DROP_ACCEL = 12.0     # 软降加速倍率（重力间隔被除以该数）

# 分数：软降每格+1；硬降每格+2；消行：1/2/3/4 = 100/300/500/800 * 等级
LINE_SCORES = {1: 100, 2: 300, 3: 500, 4: 800}


# === 颜色 ===
BG = (18, 18, 18)
BOARD_BG = (30, 30, 35)
GRID_LINE = (50, 50, 60)
OUTLINE = (0, 0, 0)
TEXT = (220, 220, 220)
ACCENT = (180, 180, 200)

COLORS = {
    'I': (0, 240, 240),
    'O': (240, 240, 0),
    'T': (160, 0, 240),
    'S': (0, 240, 0),
    'Z': (240, 0, 0),
    'J': (0, 0, 240),
    'L': (240, 160, 0),
}

# === 形状（以 4x4 网格坐标存储基础形状，旋转以左上为原点） ===
# 坐标为 (x, y)，x/y 范围 0..3
SHAPES = {
    'I': [(0, 1), (1, 1), (2, 1), (3, 1)],
    'O': [(1, 0), (2, 0), (1, 1), (2, 1)],
    'T': [(1, 0), (0, 1), (1, 1), (2, 1)],
    'S': [(1, 0), (2, 0), (0, 1), (1, 1)],
    'Z': [(0, 0), (1, 0), (1, 1), (2, 1)],
    'J': [(0, 0), (0, 1), (1, 1), (2, 1)],
    'L': [(2, 0), (0, 1), (1, 1), (2, 1)],
}

# 简单通用踢墙顺序（非完整 SRS，但体验流畅）
KICKS = [(0, 0), (-1, 0), (1, 0), (-2, 0), (2, 0), (0, -1), (0, -2)]


def rotate_point_cw(x, y):
    """(x,y) 围绕 4x4 左上角顺时针旋转 90°"""
    return (3 - y, x)


def rotate_coords(coords, rot):
    """对基础坐标做 rot 次顺时针旋转"""
    rot = rot % 4
    res = coords
    for _ in range(rot):
        res = [rotate_point_cw(x, y) for (x, y) in res]
    return res


def lighten(color, factor=0.35):
    """将颜色向白色提亮，用于幽灵块"""
    return tuple(min(255, int(c + (255 - c) * factor)) for c in color)


class Tetromino:
    def __init__(self, kind):
        self.kind = kind
        self.rot = 0
        self.x = 3              # 以棋盘坐标计，左上为原点
        self.y = -2             # 允许负数，表示还在顶部之外
        self.color = COLORS[kind]

    def cells(self, rot=None, x=None, y=None):
        """返回当前（或指定）状态下的棋盘坐标格子列表"""
        if rot is None:
            rot = self.rot
        if x is None:
            x = self.x
        if y is None:
            y = self.y

        if self.kind == 'O':
            # O 块旋转不改变相对形状与位置
            coords = SHAPES['O']
        else:
            coords = rotate_coords(SHAPES[self.kind], rot)

        return [(x + cx, y + cy) for (cx, cy) in coords]


class SevenBag:
    """7-Bag 随机：确保 7 种块轮流打乱出现，稳定体验"""
    def __init__(self):
        self.bag = []

    def next(self):
        if not self.bag:
            self.bag = list('IOTSZJL')
            random.shuffle(self.bag)
        return self.bag.pop()

    def refill_queue(self, queue, target_len=PREVIEW_COUNT + 3):
        while len(queue) < target_len:
            queue.append(self.next())


class Game:
    def __init__(self):
        self.board = [[None for _ in range(COLS)] for _ in range(ROWS)]
        self.score = 0
        self.lines = 0
        self.level = 1
        self.game_over = False
        self.paused = False

        self.rand = SevenBag()
        self.queue = []
        self.rand.refill_queue(self.queue)

        self.current = Tetromino(self.queue.pop(0))
        self.rand.refill_queue(self.queue)

        self.hold_kind = None
        self.hold_used = False

        self.fall_ms = self.calc_fall_ms()
        self.timer_ms = 0
        self.lock_timer = None

        self.down_pressed = False

    def calc_fall_ms(self):
        """重力间隔（毫秒），等级越高越快，保留下限"""
        base = 1000.0
        ms = max(80.0, base * (0.85 ** (self.level - 1)))
        return ms

    # --- 检测与移动 ---
    def valid(self, cells):
        """检测坐标合法 & 不与已落定方块重叠"""
        for (x, y) in cells:
            if x < 0 or x >= COLS or y >= ROWS:
                return False
            if y >= 0 and self.board[y][x] is not None:
                return False
        return True

    def on_floor(self, piece=None):
        """是否无法再向下移动（触底或压住其他块）"""
        p = piece or self.current
        return not self.valid(p.cells(p.rot, p.x, p.y + 1))

    def move(self, dx, dy):
        p = self.current
        if self.valid(p.cells(p.rot, p.x + dx, p.y + dy)):
            p.x += dx
            p.y += dy
            # 一旦离开地面，重置锁定计时
            if not self.on_floor():
                self.lock_timer = None
            return True
        return False

    def rotate(self, cw=True):
        p = self.current
        new_rot = (p.rot + (1 if cw else -1)) % 4

        # O 块旋转等效（固定轴）
        if p.kind == 'O':
            return True

        for (kx, ky) in KICKS:
            if self.valid(p.cells(new_rot, p.x + kx, p.y + ky)):
                p.rot = new_rot
                p.x += kx
                p.y += ky
                if not self.on_floor():
                    self.lock_timer = None
                return True
        return False

    # --- 落定/消行/新块 ---
    def hard_drop(self):
        steps = 0
        while self.move(0, 1):
            steps += 1
        self.score += steps * 2
        self.lock_piece()

    def soft_step(self, accelerated=False):
        """下落一步；若处于软降状态（accelerated=True），成功下落+1 分"""
        if self.move(0, 1):
            if accelerated:
                self.score += 1
            return
        # 无法下落，进入/推进锁定
        if self.lock_timer is None:
            self.lock_timer = 0
        # 锁定延迟由主循环计时推进

    def lock_piece(self):
        """将当前块固定到棋盘，判行、加分、出新块"""
        p = self.current
        # 若有格子在可视上方（y<0）即锁定则 Game Over
        for (x, y) in p.cells():
            if y < 0:
                self.game_over = True
                return
        # 写入棋盘
        for (x, y) in p.cells():
            if 0 <= y < ROWS:
                self.board[y][x] = p.color

        # 统计消行
        cleared = self.clear_lines()
        if cleared > 0:
            self.lines += cleared
            self.score += LINE_SCORES.get(cleared, 0) * self.level
            # 升级
            new_level = 1 + self.lines // 10
            if new_level != self.level:
                self.level = new_level
                self.fall_ms = self.calc_fall_ms()

        # 新块
        self.current = Tetromino(self.queue.pop(0))
        self.rand.refill_queue(self.queue)
        self.hold_used = False
        self.lock_timer = None

        # 刚生成就冲突 => Game Over
        if not self.valid(self.current.cells()):
            self.game_over = True

    def clear_lines(self):
        new_rows = []
        cleared = 0
        for y in range(ROWS):
            if all(self.board[y][x] is not None for x in range(COLS)):
                cleared += 1
            else:
                new_rows.append(self.board[y])
        for _ in range(cleared):
            new_rows.insert(0, [None for _ in range(COLS)])
        self.board = new_rows
        return cleared

    def hold(self):
        if self.hold_used:
            return
        cur_kind = self.current.kind
        if self.hold_kind is None:
            self.hold_kind = cur_kind
            self.current = Tetromino(self.queue.pop(0))
            self.rand.refill_queue(self.queue)
        else:
            self.current = Tetromino(self.hold_kind)
            self.hold_kind = cur_kind
        self.hold_used = True

    # --- 渲染辅助 ---
    def ghost_cells(self):
        """计算幽灵块落点的格子坐标"""
        p = self.current
        gy = p.y
        while self.valid(p.cells(p.rot, p.x, gy + 1)):
            gy += 1
        return p.cells(p.rot, p.x, gy)


def draw_board(surface, game, board_rect, fonts):
    x0, y0, w, h = board_rect
    # 背景与网格
    pygame.draw.rect(surface, BOARD_BG, (x0, y0, w, h))
    for i in range(COLS + 1):
        x = x0 + i * CELL
        pygame.draw.line(surface, GRID_LINE, (x, y0), (x, y0 + ROWS * CELL))
    for j in range(ROWS + 1):
        y = y0 + j * CELL
        pygame.draw.line(surface, GRID_LINE, (x0, y), (x0 + COLS * CELL, y))

    # 已落定方块
    for y in range(ROWS):
        for x in range(COLS):
            color = game.board[y][x]
            if color:
                draw_cell(surface, x0 + x * CELL, y0 + y * CELL, color)

    # 幽灵块
    if not game.game_over:
        ghost = game.ghost_cells()
        ghost_color = lighten(game.current.color, 0.6)
        ghost_surf = pygame.Surface((CELL - 2, CELL - 2), pygame.SRCALPHA)
        ghost_surf.fill((*ghost_color, GHOST_ALPHA))
        for (x, y) in ghost:
            if y >= 0:
                surface.blit(ghost_surf, (x0 + x * CELL + 1, y0 + y * CELL + 1))

    # 当前块
    if not game.game_over:
        for (x, y) in game.current.cells():
            if y >= 0:
                draw_cell(surface, x0 + x * CELL, y0 + y * CELL, game.current.color)

    # 外边框
    pygame.draw.rect(surface, OUTLINE, (x0, y0, w, h), 2)


def draw_cell(surface, px, py, color):
    rect = pygame.Rect(px + 1, py + 1, CELL - 2, CELL - 2)
    pygame.draw.rect(surface, color, rect)
    pygame.draw.rect(surface, OUTLINE, rect, 1)


def draw_panel(surface, game, panel_rect, fonts):
    x0, y0, w, h = panel_rect
    title = fonts['big'].render("TETRIS", True, TEXT)
    surface.blit(title, (x0, y0))

    y = y0 + 50
    def label(k, v):
        nonlocal y
        t = fonts['small'].render(f"{k}: {v}", True, TEXT)
        surface.blit(t, (x0, y))
        y += 26

    label("SCORE", game.score)
    label("LINES", game.lines)
    label("LEVEL", game.level)

    y += 8
    t_hold = fonts['small'].render("HOLD", True, ACCENT)
    surface.blit(t_hold, (x0, y))
    y += 8
    draw_mino_box(surface, x0, y, game.hold_kind, fonts)
    y += 110

    t_next = fonts['small'].render("NEXT", True, ACCENT)
    surface.blit(t_next, (x0, y))
    y += 8
    # 逐个绘制 Next 预览
    for i in range(min(PREVIEW_COUNT, len(game.queue))):
        draw_mino_box(surface, x0, y, game.queue[i], fonts)
        y += 90

    # 底部提示
    hint_lines = [
        "←/→: Move",
        "↓: Soft Drop",
        "↑/X: Rotate CW",
        "Z: Rotate CCW",
        "Space: Hard Drop",
        "C: Hold",
        "P: Pause  R: Restart",
    ]
    y = panel_rect[1] + panel_rect[3] - 18 * len(hint_lines) - 10
    for s in hint_lines:
        t = fonts['tiny'].render(s, True, (140, 140, 150))
        surface.blit(t, (x0, y))
        y += 18


def draw_mino_box(surface, x, y, kind, fonts):
    box_w, box_h = 120, 80
    pygame.draw.rect(surface, (26, 26, 30), (x, y, box_w, box_h))
    pygame.draw.rect(surface, OUTLINE, (x, y, box_w, box_h), 1)

    if not kind:
        return

    mini = 18  # 预览小格尺寸
    # 以 4x4 局部坐标绘制（旋转0）
    coords = SHAPES[kind]
    color = COLORS[kind]

    # 将方块居中到盒子
    xs = [cx for (cx, _) in coords]
    ys = [cy for (_, cy) in coords]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    w = (maxx - minx + 1) * mini
    h = (maxy - miny + 1) * mini
    ox = x + (box_w - w) // 2 - minx * mini
    oy = y + (box_h - h) // 2 - miny * mini

    for (cx, cy) in coords:
        rx = ox + cx * mini
        ry = oy + cy * mini
        rect = pygame.Rect(rx + 1, ry + 1, mini - 2, mini - 2)
        pygame.draw.rect(surface, color, rect)
        pygame.draw.rect(surface, OUTLINE, rect, 1)


def main():
    pygame.init()
    pygame.display.set_caption("Tetris (本地)")
    font_big = pygame.font.SysFont(None, 36)
    font_small = pygame.font.SysFont(None, 24)
    font_tiny = pygame.font.SysFont(None, 18)
    fonts = {'big': font_big, 'small': font_small, 'tiny': font_tiny}

    screen_w = MARGIN + COLS * CELL + 20 + SIDE_W + MARGIN
    screen_h = MARGIN + ROWS * CELL + MARGIN
    screen = pygame.display.set_mode((screen_w, screen_h))

    clock = pygame.time.Clock()

    board_rect = (MARGIN, MARGIN, COLS * CELL, ROWS * CELL)
    panel_rect = (MARGIN + COLS * CELL + 20, MARGIN, SIDE_W, ROWS * CELL)

    game = Game()

    running = True
    while running:
        dt = clock.tick(FPS)  # 毫秒
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                if game.game_over:
                    if event.key == pygame.K_r:
                        game = Game()
                    continue
                if event.key == pygame.K_LEFT:
                    game.move(-1, 0)
                elif event.key == pygame.K_RIGHT:
                    game.move(1, 0)
                elif event.key in (pygame.K_UP, pygame.K_x):
                    game.rotate(cw=True)
                elif event.key == pygame.K_z:
                    game.rotate(cw=False)
                elif event.key == pygame.K_SPACE:
                    game.hard_drop()
                elif event.key == pygame.K_c:
                    game.hold()
                elif event.key == pygame.K_p:
                    game.paused = not game.paused
                elif event.key == pygame.K_r:
                    game = Game()
                elif event.key == pygame.K_DOWN:
                    game.down_pressed = True

            elif event.type == pygame.KEYUP:
                if event.key == pygame.K_DOWN:
                    game.down_pressed = False

        # --- 更新 ---
        if not game.game_over and not game.paused:
            # 计算当前重力间隔
            fall_ms = game.fall_ms
            accelerated = False
            if game.down_pressed:
                fall_ms = max(20.0, fall_ms / SOFT_DROP_ACCEL)
                accelerated = True

            game.timer_ms += dt

            # 自由落体
            while game.timer_ms >= fall_ms and not game.game_over:
                game.timer_ms -= fall_ms
                game.soft_step(accelerated=accelerated)

                # 处理锁定延迟
                if game.on_floor():
                    if game.lock_timer is None:
                        game.lock_timer = 0
                    else:
                        game.lock_timer += fall_ms
                        if game.lock_timer >= LOCK_DELAY:
                            game.lock_piece()

        # --- 绘制 ---
        screen.fill(BG)
        draw_board(screen, game, board_rect, fonts)
        draw_panel(screen, game, panel_rect, fonts)

        # 暂停/结束遮罩
        if game.paused and not game.game_over:
            overlay = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 120))
            screen.blit(overlay, (0, 0))
            t = fonts['big'].render("PAUSED", True, TEXT)
            screen.blit(t, (screen_w // 2 - t.get_width() // 2, screen_h // 2 - 24))

        if game.game_over:
            overlay = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 140))
            screen.blit(overlay, (0, 0))
            a = fonts['big'].render("GAME OVER", True, TEXT)
            b = fonts['small'].render("Press R to Restart", True, TEXT)
            screen.blit(a, (screen_w // 2 - a.get_width() // 2, screen_h // 2 - 40))
            screen.blit(b, (screen_w // 2 - b.get_width() // 2, screen_h // 2))

        pygame.display.flip()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
