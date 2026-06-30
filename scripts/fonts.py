"""
字体管理模块
- 字体注册
- 字号类别匹配
- 字号统一（block级众数）
- 宽度校验
"""

import os
from collections import Counter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth

# ── 常量 ──────────────────────────────────────────────

SHRINK = 0.88          # 整体缩小因子（从0.95调小，避免文字溢出）
SHRINK_SHORT = 0.75    # 短文本(1-2字)的缩小因子（area_per_char不准确，需要更大缩小）
TEXT_TOLERANCE = 1.0
CAPTION_TOLERANCE = 1.0
REF_FONT_PT = 12.0

# ── 字体注册 ──────────────────────────────────────────

def register_fonts():
    """注册中文字体，返回主字体名"""
    fonts = {
        'msyh': 'C:/Windows/Fonts/msyh.ttc',
        'msyhbd': 'C:/Windows/Fonts/msyhbd.ttc',
    }
    for name, path in fonts.items():
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                print(f"  [fonts] 注册字体: {name} -> {path}")
            except Exception as e:
                print(f"  [fonts] 注册失败: {name} ({e})")
        else:
            print(f"  [fonts] 字体文件不存在: {path}")
    return 'msyh'

# ── 字号匹配 ──────────────────────────────────────────

def find_cat_height(h_px, cats, tol):
    """根据像素高度匹配类别"""
    best, best_s = None, float('inf')
    for c in cats:
        d = abs(h_px - c['height_px']) / c['height_px']
        if d <= tol and d < best_s:
            best, best_s = c, d
    result = best or min(cats, key=lambda c: abs(h_px - c['height_px']))
    return result


def find_cat_area(apc, cats, tol):
    """根据面积/字符数匹配类别"""
    best, best_s = None, float('inf')
    for c in cats:
        d = abs(apc - c['area_per_char']) / c['area_per_char']
        if d <= tol and d < best_s:
            best, best_s = c, d
    result = best or min(cats, key=lambda c: abs(apc - c['area_per_char']))
    return result

# ── 字号计算 ──────────────────────────────────────────

def calc_span_font_pt(wpx, hpx, len_c, sf, cats, tol):
    """计算单个span的字号（基于面积/字符数）"""
    apc = wpx * hpx / max(len_c, 1)
    cat = find_cat_area(apc, cats, tol)
    
    # 短文本(1-2字)使用更大的缩小因子
    # 因为area_per_char对短文本不准确，容易匹配到错误类别
    if len_c <= 2:
        shrink = SHRINK_SHORT
    else:
        shrink = SHRINK
    
    fpt = cat['height_px'] * sf * shrink
    return fpt, cat['name']


def calc_text_font_pt(content, w_pt, font_pt, font_name):
    """宽度校验：如果文本超出宽度则缩小字号"""
    if font_pt <= 0 or not content:
        return font_pt
    tw = stringWidth(content, font_name, font_pt)
    if tw > w_pt and tw > 0:
        font_pt = font_pt * (w_pt / tw) * 0.95
    return font_pt

# ── 字号统一（block级） ────────────────────────────────

def unify_block_font_size(block_spans, debug=False):
    """
    对整个block内的所有span取统一字号（众数）
    
    参数:
        block_spans: [(content, bbox, wpx, fpt), ...]
        debug: 是否输出调试信息
    
    返回:
        统一后的字号
    """
    if not block_spans:
        return 0
    
    # 收集所有字号，取整到0.5pt避免浮点误差
    all_fpts = [round(s[3] * 2) / 2 for s in block_spans if s[3] > 0]
    
    if not all_fpts:
        return 0
    
    # 用众数作为统一字号
    counter = Counter(all_fpts)
    unified_fpt = counter.most_common(1)[0][0]
    
    if debug:
        print(f"    [unify] 候选字号: {dict(counter)}")
        print(f"    [unify] 统一字号: {unified_fpt}pt")
    
    return unified_fpt


def unify_line_font_size(line_spans, debug=False):
    """
    对同一行内的所有span取最小字号（保持原有逻辑）
    """
    if not line_spans:
        return 0
    
    fpts = [s[3] for s in line_spans if s[3] > 0]
    if not fpts:
        return 0
    
    min_fpt = min(fpts)
    
    if debug and len(set(round(f, 1) for f in fpts)) > 1:
        print(f"    [line] 行内字号不一致: {[round(f, 1) for f in fpts]} -> {round(min_fpt, 1)}")
    
    return min_fpt

# ── 调试工具 ──────────────────────────────────────────

def debug_font_distribution(page_elements):
    """打印整个页面的字号分布"""
    all_fpts = []
    for elem in page_elements:
        for item in elem['texts']:
            # texts 元素是 (content, x, y, fpt) 四元组
            if len(item) >= 4:
                fpt = item[3]
                if fpt > 0:
                    all_fpts.append(round(fpt * 2) / 2)
    
    if not all_fpts:
        print("  [debug] 无文本")
        return
    
    counter = Counter(all_fpts)
    print(f"  [debug] 页面字号分布 (共{len(all_fpts)}个文本):")
    for fpt, count in counter.most_common(5):
        print(f"    {fpt}pt: {count}个 ({count/len(all_fpts)*100:.1f}%)")
