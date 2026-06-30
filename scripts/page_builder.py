"""
页面构建模块
- 构建页面（文本、图片、标题等）
- 合并公式（矢量 PDF）
"""

import io
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

# ── 常量 ──────────────────────────────────────────────

SHRINK = 0.95

# ── 进度显示 ──────────────────────────────────────────

class Progress:
    def __init__(self, total, desc, unit="it"):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.done = 0
        self.start = __import__('time').time()
        self.errors = 0

    def update(self, n=1):
        self.done += n

    def tick(self, n=1):
        self.update(n)
        if self.done % max(1, self.total // 20) == 0 or self.done == self.total:
            self._print()

    def error(self):
        self.errors += 1

    def _print(self):
        import time
        elapsed = time.time() - self.start
        pct = self.done / self.total * 100 if self.total else 0
        if self.done > 0:
            eta = elapsed / self.done * (self.total - self.done)
            eta_str = self._fmt_time(eta)
        else:
            eta_str = "?"
        err = f" err={self.errors}" if self.errors else ""
        print(f"  [{self.desc}] {self.done}/{self.total} ({pct:.0f}%) "
              f"elapsed={self._fmt_time(elapsed)} ETA={eta_str}{err}", flush=True)

    def finish(self):
        import time
        elapsed = time.time() - self.start
        err = f", {self.errors} errors" if self.errors else ""
        print(f"  [{self.desc}] done {self.total} {self.unit} in {self._fmt_time(elapsed)}{err}")

    def _fmt_time(self, s):
        if s < 60:
            return f"{s:.0f}s"
        m, s = divmod(int(s), 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

# ── 页面构建 ──────────────────────────────────────────

def build_page(page_elems, pw, ph, font_name, debug=False):
    """构建单个页面（不含公式）"""
    pkt = io.BytesIO()
    c = canvas.Canvas(pkt, pagesize=(pw, ph))
    
    # 绘制图片
    for img, x, y, w, h in page_elems['images']:
        try:
            c.drawImage(img, x, y, width=w, height=h)
            if debug:
                print(f"    [build] 图片: ({x:.0f},{y:.0f}) {w:.0f}x{h:.0f}")
        except Exception as e:
            if debug:
                print(f"    [build] 图片绘制失败: {e}")
    
    # 绘制标题
    for ct, x, y, fpt, lv in page_elems['titles']:
        try:
            fn = 'msyhbd' if lv == 1 else font_name
            c.setFont(fn, fpt)
            c.drawString(x, y, ct)
            if debug:
                print(f"    [build] 标题 L{lv}: '{ct[:20]}...' {fpt:.1f}pt")
        except Exception as e:
            try:
                c.setFont(font_name, fpt)
                c.drawString(x, y, ct)
            except:
                if debug:
                    print(f"    [build] 标题绘制失败: {e}")
    
    # 绘制文本
    for ct, x, y, fpt in page_elems['texts']:
        try:
            c.setFont(font_name, fpt)
            c.drawString(x, y, ct)
        except Exception as e:
            if debug:
                print(f"    [build] 文本绘制失败: {e}")
    
    # 绘制标注
    for ct, x, y, fpt in page_elems['captions']:
        try:
            c.setFont(font_name, fpt)
            c.drawString(x, y, ct)
        except Exception as e:
            if debug:
                print(f"    [build] 标注绘制失败: {e}")
    
    # 绘制页眉
    for ct, x, y, fpt in page_elems['headers']:
        try:
            c.setFont(font_name, fpt)
            c.drawString(x, y, ct)
        except Exception as e:
            if debug:
                print(f"    [build] 页眉绘制失败: {e}")
    
    # 绘制页码
    for ct, x, y, fpt in page_elems['page_numbers']:
        try:
            c.setFont(font_name, fpt)
            c.drawString(x, y, ct)
        except Exception as e:
            if debug:
                print(f"    [build] 页码绘制失败: {e}")
    
    # 绘制页脚
    for ct, x, y, fpt in page_elems.get('footers', []):
        try:
            c.setFont(font_name, fpt)
            c.drawString(x, y, ct)
        except Exception as e:
            if debug:
                print(f"    [build] 页脚绘制失败: {e}")
    
    c.save()
    pkt.seek(0)
    rd = PdfReader(pkt)
    
    if len(rd.pages) > 0:
        return rd.pages[0]
    else:
        return None

# ── 公式合并 ──────────────────────────────────────────

def merge_formula(page, formula_pdf_path, tx, ty, tw, th, debug=False):
    """将公式 PDF 合并到页面指定位置"""
    try:
        fp = PdfReader(formula_pdf_path).pages[0]
        fw = float(fp.mediabox.width)
        fh = float(fp.mediabox.height)
        
        if fw <= 0 or fh <= 0:
            if debug:
                print(f"    [merge] 公式尺寸无效: {fw}x{fh}")
            return False
        
        # 计算缩放比例
        sc = (th / fh) * SHRINK
        dw, dh = fw * sc, fh * sc
        
        # 如果宽度超出，二次缩小
        if dw > tw and dw > 0:
            sc = sc * (tw / dw) * 0.95
            dw, dh = fw * sc, fh * sc
        
        if debug:
            print(f"    [merge] 公式: {fw:.0f}x{fh:.0f} -> {dw:.0f}x{dh:.0f} (sc={sc:.3f})")
        
        # 应用变换
        op = Transformation().scale(sc, sc).translate(tx, ty)
        fp.add_transformation(op)
        fp.mediabox = RectangleObject([tx, ty, tx + dw, ty + dh])
        page.merge_page(fp)
        
        return True
    except Exception as e:
        if debug:
            print(f"    [merge] 公式合并失败: {e}")
        return False

# ── 构建并合并 ─────────────────────────────────────────

def build_and_merge(page_elements, formula_list, formula_targets, formula_pdfs,
                    pw, ph, font_name, output_pdf, debug=False):
    """构建所有页面并合并公式"""
    writer = PdfWriter()
    total_pages = len(page_elements)
    total_formulas = sum(len(e['formula_indices']) for e in page_elements)
    
    if debug:
        print(f"\n  [build_merge] 开始构建 {total_pages} 页，{total_formulas} 个公式")
    
    # Phase 1: 构建页面
    prog = Progress(total_pages, "build", "pages")
    for pi, elems in enumerate(page_elements):
        if debug:
            print(f"\n  [build] 第 {pi+1} 页:")
            print(f"    文本: {len(elems['texts'])} 个")
            print(f"    图片: {len(elems['images'])} 个")
            print(f"    标题: {len(elems['titles'])} 个")
            print(f"    公式: {len(elems['formula_indices'])} 个")
        
        page = build_page(elems, pw, ph, font_name, debug=debug)
        
        if page:
            writer.add_page(page)
        else:
            writer.add_blank_page(width=pw, height=ph)
            if debug:
                print(f"    [build] 页面构建失败，使用空白页")
        
        prog.tick()
    
    prog.finish()
    
    # Phase 2: 合并公式
    prog = Progress(total_formulas, "merge", "formulas")
    for pi, elems in enumerate(page_elements):
        page = writer.pages[pi]
        
        for fi in elems['formula_indices']:
            if fi >= len(formula_pdfs) or fi >= len(formula_targets):
                prog.error()
                prog.tick()
                continue
            
            if formula_targets[fi] is None:
                prog.tick()
                continue
            
            tx, ty, tw, th = formula_targets[fi]
            success = merge_formula(page, formula_pdfs[fi], tx, ty, tw, th, debug=debug)
            
            if not success:
                prog.error()
            
            prog.tick()
    
    prog.finish()
    
    # 保存
    with open(str(output_pdf), 'wb') as f:
        writer.write(f)
    
    print(f"\n  saved: {output_pdf}")
    
    return output_pdf

# ── 测试 ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=== 测试 page_builder.py ===\n")
    
    # 测试进度条
    print("1. 测试进度条:")
    prog = Progress(10, "test", "items")
    for i in range(10):
        prog.tick()
    prog.finish()
    
    print("\n=== 测试完成 ===")