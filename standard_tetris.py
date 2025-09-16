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

# 优先使用包含箭头和扩展字符的字体，避免文字显示为方框
FONT_PREFERENCE = [
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "Segoe UI Symbol",
    "Arial Unicode MS",
    "WenQuanYi Micro Hei",
    "DejaVu Sans",
    "SimHei",
    "Arial",
]

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


def clone_board(board):
    return [row[:] for row in board]


def clear_lines_from_board(board):
    new_rows = []
    cleared = 0
    for row in board:
        if all(cell is not None for cell in row):
            cleared += 1
        else:
            new_rows.append(row[:])
    for _ in range(cleared):
        new_rows.insert(0, [None for _ in range(COLS)])
    return new_rows, cleared


def valid_position_on_board(board, coords, ox, oy):
    for (cx, cy) in coords:
        x, y = ox + cx, oy + cy
        if x < 0 or x >= COLS or y >= ROWS:
            return False
        if y >= 0 and board[y][x] is not None:
            return False
    return True


def drop_y_for_coords(board, coords, x):
    y = -4
    while valid_position_on_board(board, coords, x, y + 1):
        y += 1
    if not valid_position_on_board(board, coords, x, y):
        return None
    return y


def evaluate_board_metrics(board):
    heights = [0 for _ in range(COLS)]
    holes = 0
    bumpiness = 0
    aggregate_height = 0
    for x in range(COLS):
        column_height = 0
        block_found = False
        column_holes = 0
        for y in range(ROWS):
            if board[y][x] is not None:
                if not block_found:
                    block_found = True
                    column_height = ROWS - y
            else:
                if block_found:
                    column_holes += 1
        heights[x] = column_height
        aggregate_height += column_height
        holes += column_holes
    for x in range(COLS - 1):
        bumpiness += abs(heights[x] - heights[x + 1])
    well_sum = 0
    for x in range(COLS):
        left = heights[x - 1] if x > 0 else heights[x]
        right = heights[x + 1] if x < COLS - 1 else heights[x]
        if heights[x] < left and heights[x] < right:
            well_sum += min(left, right) - heights[x]
    return {
        'aggregate_height': aggregate_height,
        'holes': holes,
        'bumpiness': bumpiness,
        'well_sum': well_sum,
        'max_height': max(heights) if heights else 0,
        'heights': heights,
    }


def score_position(metrics, lines_cleared, top_out):
    score = (
        -0.510066 * metrics['aggregate_height']
        + 0.760666 * lines_cleared
        - 0.35663 * metrics['holes']
        - 0.184483 * metrics['bumpiness']
        + 0.1 * metrics['well_sum']
    )
    if top_out:
        score -= 10
    return score


def simulate_lock_result(board, kind, rot, x, y):
    if kind == 'O':
        coords = SHAPES['O']
    else:
        coords = rotate_coords(SHAPES[kind], rot)
    board_copy = clone_board(board)
    top_out = False
    for (cx, cy) in coords:
        gx, gy = x + cx, y + cy
        if gy < 0:
            top_out = True
            continue
        board_copy[gy][gx] = COLORS[kind]
    cleared = 0
    final_board = board_copy
    if not top_out:
        final_board, cleared = clear_lines_from_board(board_copy)
    metrics = evaluate_board_metrics(final_board)
    score = score_position(metrics, cleared, top_out)
    return {
        'board_after': final_board,
        'lines_cleared': cleared,
        'top_out': top_out,
        'metrics': metrics,
        'score': score,
        'cells': [(x + cx, y + cy) for (cx, cy) in coords],
        'rot': rot,
        'x': x,
        'y': y,
    }


def evaluate_piece_moves(board, kind):
    best = None
    seen = set()
    for rot in range(4):
        if kind == 'O' and rot > 0:
            continue
        if kind == 'O':
            coords = SHAPES['O']
        else:
            coords = rotate_coords(SHAPES[kind], rot)
        key = tuple(sorted(coords))
        if key in seen:
            continue
        seen.add(key)
        minx = min(cx for (cx, _) in coords)
        maxx = max(cx for (cx, _) in coords)
        for x in range(-minx, COLS - maxx):
            drop_y = drop_y_for_coords(board, coords, x)
            if drop_y is None:
                continue
            result = simulate_lock_result(board, kind, rot, x, drop_y)
            if best is None or result['score'] > best['score']:
                best = result
    return best


def enrich_move_result(result, *, used_hold, source, piece_kind):
    if result is None:
        return None
    enriched = {}
    for key, value in result.items():
        if key == 'cells':
            enriched[key] = list(value)
        elif key == 'board_after':
            enriched[key] = clone_board(value)
        elif key == 'metrics':
            metrics_copy = dict(value)
            if 'heights' in value:
                metrics_copy['heights'] = list(value['heights'])
            enriched[key] = metrics_copy
        else:
            enriched[key] = value
    enriched['used_hold'] = used_hold
    enriched['source'] = source
    enriched['piece_kind'] = piece_kind
    return enriched


def find_best_moves(board, current_kind, hold_available, hold_kind, queue):
    current_best = enrich_move_result(
        evaluate_piece_moves(board, current_kind),
        used_hold=False,
        source='current',
        piece_kind=current_kind,
    )

    hold_best = None
    if hold_available:
        target_kind = None
        source = None
        if hold_kind:
            target_kind = hold_kind
            source = 'hold'
        elif queue:
            target_kind = queue[0]
            source = 'queue'
        if target_kind:
            hold_best = enrich_move_result(
                evaluate_piece_moves(board, target_kind),
                used_hold=True,
                source=source,
                piece_kind=target_kind,
            )

    overall = current_best
    if hold_best and (overall is None or hold_best['score'] > overall['score']):
        overall = hold_best

    return {
        'current': current_best,
        'hold': hold_best,
        'overall': overall,
    }


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
        self.turn_hold_used = False

        self.history = []

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
        """将当前块固定到棋盘，判行、加分、出新块并记录复盘信息"""
        p = self.current
        board_before = clone_board(self.board)
        queue_before = list(self.queue)
        hold_before = self.hold_kind
        hold_available = not self.turn_hold_used
        hold_used_in_turn = self.turn_hold_used

        player_eval = simulate_lock_result(board_before, p.kind, p.rot, p.x, p.y)
        player_eval_record = dict(player_eval)
        player_eval_record['cells'] = list(player_eval['cells'])
        player_eval_record['board_after'] = clone_board(player_eval['board_after'])
        metrics_copy = dict(player_eval['metrics'])
        if 'heights' in player_eval['metrics']:
            metrics_copy['heights'] = list(player_eval['metrics']['heights'])
        player_eval_record['metrics'] = metrics_copy
        player_eval_record['piece_kind'] = p.kind

        best_moves = find_best_moves(board_before, p.kind, hold_available, hold_before, queue_before)
        best_overall = best_moves['overall']
        diff_vs_best = None
        if best_overall is not None:
            diff_vs_best = player_eval_record['score'] - best_overall['score']

        score_before = self.score
        lines_before = self.lines
        level_before = self.level

        top_out = player_eval_record['top_out']
        spawn_blocked = False
        cleared = player_eval_record['lines_cleared']

        if top_out:
            self.game_over = True
            board_after_actual = clone_board(self.board)
        else:
            board_after_actual = clone_board(player_eval_record['board_after'])
            self.board = board_after_actual
            if cleared > 0:
                self.lines += cleared
                self.score += LINE_SCORES.get(cleared, 0) * self.level
                new_level = 1 + self.lines // 10
                if new_level != self.level:
                    self.level = new_level
                    self.fall_ms = self.calc_fall_ms()

            self.current = Tetromino(self.queue.pop(0))
            self.rand.refill_queue(self.queue)
            self.hold_used = False
            self.turn_hold_used = False
            self.lock_timer = None

            if not self.valid(self.current.cells()):
                self.game_over = True
                spawn_blocked = True

        score_after = self.score
        lines_after = self.lines
        queue_after = list(self.queue)

        entry = {
            'board_before': board_before,
            'board_after_actual': clone_board(self.board),
            'player_eval': player_eval_record,
            'best_overall': best_overall,
            'best_current': best_moves['current'],
            'best_hold': best_moves['hold'],
            'score_diff_to_best': diff_vs_best,
            'score_before': score_before,
            'score_after': score_after,
            'score_gain': score_after - score_before,
            'lines_before': lines_before,
            'lines_after': lines_after,
            'level_before': level_before,
            'level_after': self.level,
            'hold_kind_before': hold_before,
            'hold_kind_after': self.hold_kind,
            'hold_available': hold_available,
            'hold_used_this_turn': hold_used_in_turn,
            'queue_before': queue_before,
            'queue_after': queue_after,
            'cleared': cleared,
            'game_over_after': self.game_over,
            'game_over_reason': 'top_out' if top_out else ('spawn_block' if spawn_blocked else None),
        }
        self.history.append(entry)

        if top_out:
            return

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
        self.turn_hold_used = True

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


def draw_panel(surface, game, panel_rect, fonts, hint_lines):
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


def cells_equal(a, b):
    return set(tuple(c) for c in a) == set(tuple(c) for c in b)


class ReviewSession:
    def __init__(self, history):
        self.history = history or []
        self.index = max(0, len(self.history) - 1)
        self.view_best = False
        self.active = bool(self.history)

    def current_entry(self):
        if not self.history:
            return None
        return self.history[self.index]

    def step(self, delta):
        if not self.history:
            return
        self.index = max(0, min(self.index + delta, len(self.history) - 1))

    def toggle_view(self):
        self.view_best = not self.view_best

    def total_steps(self):
        return len(self.history)

    def is_player_best(self):
        entry = self.current_entry()
        if not entry:
            return True
        best = entry['best_overall']
        if not best:
            return True
        player = entry['player_eval']
        if best['piece_kind'] != player['piece_kind']:
            return False
        return cells_equal(best['cells'], player['cells'])


def draw_overlay(surface, board_rect, cells, color, *, fill=True, outline=True, fill_alpha=140, outline_width=2):
    if not cells:
        return
    x0, y0, _, _ = board_rect
    if fill:
        fill_color = lighten(color, 0.4)
        fill_surf = pygame.Surface((CELL - 2, CELL - 2), pygame.SRCALPHA)
        fill_surf.fill((*fill_color, fill_alpha))
    else:
        fill_surf = None
    outline_color = lighten(color, 0.15)
    for (x, y) in cells:
        if y < 0 or y >= ROWS:
            continue
        px = x0 + x * CELL
        py = y0 + y * CELL
        rect = pygame.Rect(px + 1, py + 1, CELL - 2, CELL - 2)
        if fill and fill_surf is not None:
            surface.blit(fill_surf, rect.topleft)
        if outline:
            pygame.draw.rect(surface, outline_color, rect, outline_width)


def draw_review_board(surface, board_rect, review, fonts):
    x0, y0, w, h = board_rect
    pygame.draw.rect(surface, BOARD_BG, (x0, y0, w, h))
    for i in range(COLS + 1):
        x = x0 + i * CELL
        pygame.draw.line(surface, GRID_LINE, (x, y0), (x, y0 + ROWS * CELL))
    for j in range(ROWS + 1):
        y = y0 + j * CELL
        pygame.draw.line(surface, GRID_LINE, (x0, y), (x0 + COLS * CELL, y))

    entry = review.current_entry() if review else None
    board = entry['board_before'] if entry else [[None for _ in range(COLS)] for _ in range(ROWS)]

    for y in range(ROWS):
        for x in range(COLS):
            color = board[y][x]
            if color:
                draw_cell(surface, x0 + x * CELL, y0 + y * CELL, color)

    if entry:
        player_move = entry['player_eval']
        best_move = entry['best_overall']
        if review.view_best and best_move:
            best_color = COLORS[best_move['piece_kind']]
            draw_overlay(surface, board_rect, best_move['cells'], best_color, fill=True, outline=True)
            if not cells_equal(best_move['cells'], player_move['cells']) or best_move['piece_kind'] != player_move['piece_kind']:
                player_color = COLORS[player_move['piece_kind']]
                draw_overlay(surface, board_rect, player_move['cells'], player_color, fill=False, outline=True, outline_width=1)
        else:
            player_color = COLORS[player_move['piece_kind']]
            draw_overlay(surface, board_rect, player_move['cells'], player_color, fill=True, outline=True)
            if best_move and (best_move['piece_kind'] != player_move['piece_kind'] or not cells_equal(best_move['cells'], player_move['cells'])):
                best_color = COLORS[best_move['piece_kind']]
                draw_overlay(surface, board_rect, best_move['cells'], best_color, fill=False, outline=True, outline_width=2)

    pygame.draw.rect(surface, OUTLINE, (x0, y0, w, h), 2)

    label = "最优落点" if review and review.view_best else "玩家落点"
    if review and review.view_best and (not entry or entry['best_overall'] is None):
        label += "（无）"
    label_surf = fonts['small'].render(label, True, ACCENT)
    surface.blit(label_surf, (x0 + 8, y0 + 6))

    if entry and entry['score_diff_to_best'] is not None:
        diff = entry['score_diff_to_best']
        if diff < -2:
            diff_color = (255, 120, 120)
        elif diff < -0.5:
            diff_color = (255, 200, 120)
        elif diff > 0.5:
            diff_color = (120, 220, 140)
        else:
            diff_color = TEXT
        diff_text = f"差距: {diff:+.2f}"
        diff_surf = fonts['tiny'].render(diff_text, True, diff_color)
        surface.blit(diff_surf, (x0 + 8, y0 + 6 + label_surf.get_height() + 4))


def draw_review_panel(surface, panel_rect, fonts, review):
    x0, y0, w, h = panel_rect
    pygame.draw.rect(surface, (26, 26, 30), (x0, y0, w, h))
    pygame.draw.rect(surface, OUTLINE, (x0, y0, w, h), 2)

    entry = review.current_entry() if review else None
    if not entry:
        text = fonts['small'].render("暂无复盘数据", True, TEXT)
        surface.blit(text, (x0 + 12, y0 + 12))
        return

    def write(line, font_key='small', color=TEXT, spacing=26):
        nonlocal y
        surf = fonts[font_key].render(line, True, color)
        surface.blit(surf, (x0 + 12, y))
        y += spacing

    y = y0 + 14
    write("复盘模式", 'big', ACCENT, 42)
    step_text = f"第 {review.index + 1}/{review.total_steps()} 手"
    write(step_text)
    view_text = "展示：最优" if review.view_best else "展示：玩家"
    write(view_text, 'tiny', ACCENT, 20)

    player_eval = entry['player_eval']
    piece_kind = player_eval['piece_kind']
    write(f"方块：{piece_kind}")

    hold_before = entry['hold_kind_before'] or '空'
    hold_status = '已用' if entry['hold_used_this_turn'] else '可用'
    write(f"Hold 槽：{hold_before}（{hold_status}）", 'tiny', ACCENT, 20)
    next_hint = entry['queue_before'][0] if entry['queue_before'] else '-'
    write(f"Next：{next_hint}", 'tiny', ACCENT, 22)

    write(f"实际得分变化：{entry['score_gain']}")
    write(f"玩家估值：{player_eval['score']:.2f}，消行：{player_eval['lines_cleared']}")

    best_move = entry['best_overall']
    if best_move:
        best_desc = "最优："
        if best_move['used_hold']:
            if best_move['source'] == 'hold':
                best_desc += f"Hold → {best_move['piece_kind']}"
            else:
                best_desc += f"Hold 换出 {best_move['piece_kind']}"
        else:
            best_desc += f"直接放置 {best_move['piece_kind']}"
        write(best_desc)
        write(f"最优估值：{best_move['score']:.2f}，消行：{best_move['lines_cleared']}")
    else:
        write("最优估值：无可行落点", 'small', (255, 150, 150))

    diff = entry['score_diff_to_best']
    if diff is not None:
        if diff < -2:
            comment = "评语：严重失误，需要重点反思"
            color = (255, 120, 120)
        elif diff < -0.5:
            comment = "评语：尚可，但仍有改进空间"
            color = (255, 200, 120)
        elif diff > 0.5:
            comment = "评语：表现优秀，超过基准"
            color = (140, 220, 140)
        else:
            comment = "评语：选择稳健"
            color = (200, 200, 220)
        write(f"与最优差距：{diff:+.2f}")
        write(comment, 'tiny', color, 24)
    else:
        write("与最优差距：--", 'tiny', ACCENT, 24)

    player_metrics = player_eval['metrics']
    write("玩家指标：", 'small', ACCENT, 24)
    write(f"高度和：{player_metrics['aggregate_height']:.0f}", 'tiny', TEXT, 20)
    write(f"洞数：{player_metrics['holes']}", 'tiny', TEXT, 20)
    write(f"起伏：{player_metrics['bumpiness']:.1f}", 'tiny', TEXT, 24)

    if best_move and 'metrics' in best_move:
        best_metrics = best_move['metrics']
        write("最优指标：", 'small', ACCENT, 24)
        write(f"高度和：{best_metrics['aggregate_height']:.0f}", 'tiny', TEXT, 20)
        write(f"洞数：{best_metrics['holes']}", 'tiny', TEXT, 20)
        write(f"起伏：{best_metrics['bumpiness']:.1f}", 'tiny', TEXT, 24)

    if entry['game_over_reason'] == 'top_out':
        write("结果：本手顶出导致结束", 'small', (255, 140, 140), 28)
    elif entry['game_over_reason'] == 'spawn_block':
        write("结果：下一块无法生成，游戏结束", 'small', (255, 140, 140), 28)

    y = max(y + 6, y0 + h - 90)
    write("操作提示：", 'small', ACCENT, 24)
    write("←/→：切换手数", 'tiny', TEXT, 20)
    write("Tab：切换玩家/最优展示", 'tiny', TEXT, 20)
    write("Enter：退出复盘   R：重新开始", 'tiny', TEXT, 20)

def font_supports_text(font, text):
    """检查字体是否支持文本中的所有字符（避免渲染出方框）。"""
    try:
        metrics = font.metrics(text)
    except ValueError:
        # 某些 Pygame 版本在空字符串时会抛错，这里宽容处理
        return False
    return all(m is not None for m in metrics)


def choose_hint_lines(font):
    """优先使用带箭头的提示，若字体不支持则退回 ASCII 文本。"""
    candidates = [
        ("←/→: Move", "Left/Right: Move"),
        ("↓: Soft Drop", "Down: Soft Drop"),
        ("↑/X: Rotate CW", "Up/X: Rotate CW"),
        ("Z: Rotate CCW", None),
        ("Space: Hard Drop", None),
        ("C: Hold", None),
        ("P: Pause  R: Restart", None),
    ]

    lines = []
    for preferred, fallback in candidates:
        if font_supports_text(font, preferred):
            lines.append(preferred)
        elif fallback is not None:
            lines.append(fallback)
        else:
            lines.append(preferred)
    return lines


def load_font(size, bold=False):
    return pygame.font.SysFont(FONT_PREFERENCE, size, bold=bold)


def main():
    pygame.init()
    pygame.display.set_caption("Tetris (本地)")
    font_big = load_font(36)
    font_small = load_font(24)
    font_tiny = load_font(18)
    fonts = {'big': font_big, 'small': font_small, 'tiny': font_tiny}
    hint_lines = choose_hint_lines(font_tiny)

    screen_w = MARGIN + COLS * CELL + 20 + SIDE_W + MARGIN
    screen_h = MARGIN + ROWS * CELL + MARGIN
    screen = pygame.display.set_mode((screen_w, screen_h))

    clock = pygame.time.Clock()

    board_rect = (MARGIN, MARGIN, COLS * CELL, ROWS * CELL)
    panel_rect = (MARGIN + COLS * CELL + 20, MARGIN, SIDE_W, ROWS * CELL)

    game = Game()
    review = None

    running = True
    while running:
        dt = clock.tick(FPS)  # 毫秒
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                    continue

                if review and review.active:
                    if event.key == pygame.K_LEFT:
                        review.step(-1)
                    elif event.key == pygame.K_RIGHT:
                        review.step(1)
                    elif event.key == pygame.K_TAB:
                        review.toggle_view()
                    elif event.key in (pygame.K_RETURN, pygame.K_v):
                        review.active = False
                    elif event.key == pygame.K_r:
                        game = Game()
                        review = None
                    continue

                if game.game_over:
                    if event.key == pygame.K_r:
                        game = Game()
                        review = None
                    elif event.key in (pygame.K_RETURN, pygame.K_v):
                        if game.history:
                            review = ReviewSession(game.history)
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
                    review = None
                elif event.key == pygame.K_DOWN:
                    game.down_pressed = True

            elif event.type == pygame.KEYUP:
                if event.key == pygame.K_DOWN:
                    game.down_pressed = False

        # --- 更新 ---
        if not game.game_over and not game.paused and not (review and review.active):
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

        if review and review.active:
            draw_review_board(screen, board_rect, review, fonts)
            draw_review_panel(screen, panel_rect, fonts, review)
        else:
            draw_board(screen, game, board_rect, fonts)
            draw_panel(screen, game, panel_rect, fonts, hint_lines)

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
                c = fonts['small'].render("Press Enter to Review", True, TEXT)
                screen.blit(a, (screen_w // 2 - a.get_width() // 2, screen_h // 2 - 60))
                screen.blit(b, (screen_w // 2 - b.get_width() // 2, screen_h // 2 - 12))
                screen.blit(c, (screen_w // 2 - c.get_width() // 2, screen_h // 2 + 28))

        pygame.display.flip()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
