"""
MinerU OCR + 排版复刻 - 通用流程
用法: C:\\Users\\29595\\Miniconda3\\envs\\py312\\python.exe reconstruct.py <input.pdf>
"""

import sys
if sys.version_info[:2] != (3, 12):
    print(f"Python {sys.version_info.major}.{sys.version_info.minor} detected, need 3.12")
    print(f"Run with: C:\\Users\\29595\\Miniconda3\\envs\\py312\\python.exe {sys.argv[0]}")
    sys.exit(1)

import json
import os
import subprocess
import re
import io
import time
import argparse
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject


REF_FONT_PT = 12.0
SHRINK = 0.95
TEXT_TOLERANCE = 1.0
CAPTION_TOLERANCE = 1.0
BATCH_SIZE = 100


class Progress:
    def __init__(self, total, desc, unit="it"):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.done = 0
        self.start = time.time()
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
        elapsed = time.time() - self.start
        pct = self.done / self.total * 100 if self.total else 0
        if self.done > 0:
            eta = elapsed / self.done * (self.total - self.done)
            eta_str = _fmt_time(eta)
        else:
            eta_str = "?"
        err = f" err={self.errors}" if self.errors else ""
        print(f"  [{self.desc}] {self.done}/{self.total} ({pct:.0f}%) "
              f"elapsed={_fmt_time(elapsed)} ETA={eta_str}{err}", flush=True)

    def finish(self):
        elapsed = time.time() - self.start
        err = f", {self.errors} errors" if self.errors else ""
        print(f"  [{self.desc}] done {self.total} {self.unit} in {_fmt_time(elapsed)}{err}")


def _fmt_time(s):
    if s < 60:
        return f"{s:.0f}s"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# ── Fonts ──────────────────────────────────────────────

def register_fonts():
    for name, path in {'msyh': 'C:/Windows/Fonts/msyh.ttc',
                       'msyhbd': 'C:/Windows/Fonts/msyhbd.ttc'}.items():
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
            except:
                pass
    return 'msyh'


def get_page_size(path):
    try:
        box = PdfReader(path).pages[0].mediabox
        return float(box.width), float(box.height)
    except:
        return A4[0], A4[1]


def calc_text_font_pt(content, w_pt, font_pt, font_name):
    if font_pt <= 0 or not content:
        return font_pt
    tw = stringWidth(content, font_name, font_pt)
    if tw > w_pt and tw > 0:
        font_pt = font_pt * (w_pt / tw) * 0.95
    return font_pt


def find_cat_height(h_px, cats, tol):
    best, best_s = None, float('inf')
    for c in cats:
        d = abs(h_px - c['height_px']) / c['height_px']
        if d <= tol and d < best_s:
            best, best_s = c, d
    return best or min(cats, key=lambda c: abs(h_px - c['height_px']))


def find_cat_area(apc, cats, tol):
    best, best_s = None, float('inf')
    for c in cats:
        d = abs(apc - c['area_per_char']) / c['area_per_char']
        if d <= tol and d < best_s:
            best, best_s = c, d
    return best or min(cats, key=lambda c: abs(apc - c['area_per_char']))


# ── LaTeX ──────────────────────────────────────────────

def split_array_rows(content):
    bs = chr(92)
    m = re.search(re.escape(bs) + r'begin\{array\}\s*(\{[^}]*\})', content)
    if not m:
        return None
    cols = m.group(1)
    inner = content[m.end():]
    em = re.search(re.escape(bs) + r'end\{array\}', inner)
    if not em:
        return None
    body, after = inner[:em.start()], inner[em.end():]
    tag_m = re.search(re.escape(bs) + r'tag\{[^}]*\}', after)
    tag = ''
    if tag_m:
        tag = tag_m.group(0)
        after = after[:tag_m.start()] + after[tag_m.end():]
    rows, depth, cur = [], 0, []
    i = 0
    while i < len(body):
        if body[i] == bs and i + 1 < len(body) and body[i+1] == bs and depth == 0:
            rows.append(''.join(cur).strip())
            cur, i = [], i + 2
            continue
        if body[i] == '{':
            depth += 1
        elif body[i] == '}':
            depth -= 1
        cur.append(body[i])
        i += 1
    if cur:
        rows.append(''.join(cur).strip())
    rows = [r for r in rows if r]
    if len(rows) <= 1:
        return None
    prefix, suffix = content[:m.start()].strip(), after.strip()
    result = []
    for ri, row in enumerate(rows):
        rc = row.replace(bs + 'left', '').replace(bs + 'right', '')
        tex = prefix + bs + 'begin{array}' + cols + ' ' + rc + bs + 'end{array}'
        if suffix:
            tex += ' ' + suffix
        if tag and ri == len(rows) - 1:
            tex += ' ' + tag
        result.append(tex.strip())
    return result


def clean_latex(s):
    s = re.sub(r'\\frac\s+(\w)\s+(\w)', r'\\frac{\1}{\2}', s)
    s = s.replace('\\left', '').replace('\\right', '')
    s = re.sub(r'\\begin\s*\{\s*array\s*\}[^}]*\}', '', s)
    s = s.replace('\\end{array}', '').replace('\\ ', ' ')
    return s.strip()


def batch_compile(formulas, output_dir):
    if not formulas:
        return []
    os.makedirs(output_dir, exist_ok=True)
    all_paths = []
    prog = Progress(len(formulas), "compile", "formulas")

    for bi in range(0, len(formulas), BATCH_SIZE):
        batch = formulas[bi:bi + BATCH_SIZE]
        bdir = os.path.join(output_dir, f'batch_{bi}')
        os.makedirs(bdir, exist_ok=True)

        pages = [f"\\begin{{preview}}\n${f}$\n\\end{{preview}}\n\\newpage" for f in batch]
        tex = f"""\\documentclass{{article}}
\\usepackage[active,tightpage]{{preview}}
\\usepackage{{amsmath}}
\\usepackage{{unicode-math}}
\\setmathfont{{Asana-Math}}
\\fontsize{{{REF_FONT_PT:.1f}}}{{{REF_FONT_PT*1.2:.1f}}}\\selectfont
\\begin{{document}}
{chr(10).join(pages)}
\\end{{document}}
"""
        tex_path = os.path.join(bdir, 'batch.tex')
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(tex)

        env = os.environ.copy()
        env['MIKTEX_ENABLE_INSTALLER'] = '1'
        result = subprocess.run(
            ['xelatex', '-interaction=nonstopmode', '-output-directory', bdir, tex_path],
            capture_output=True, timeout=600, env=env
        )

        pdf_path = os.path.join(bdir, 'batch.pdf')
        if not os.path.exists(pdf_path):
            prog.errors += len(batch)
            prog.update(len(batch))
            print(f"  batch {bi} FAILED ({len(batch)} formulas)")
            continue

        reader = PdfReader(pdf_path)
        if len(reader.pages) != len(batch):
            print(f"  batch {bi}: expected {len(batch)} pages, got {len(reader.pages)}")
            log_path = os.path.join(bdir, 'batch.log')
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                    for line in lf:
                        if '!' in line:
                            print(f"    {line.strip()[:120]}")

        for i in range(len(reader.pages)):
            w = PdfWriter()
            w.add_page(reader.pages[i])
            op = os.path.join(bdir, f'formula_{i}.pdf')
            with open(op, 'wb') as f:
                w.write(f)
            all_paths.append(op)

        prog.update(len(batch))

        if len(reader.pages) == len(batch):
            for ext in ['.tex', '.aux', '.log']:
                p = os.path.join(bdir, 'batch' + ext)
                if os.path.exists(p):
                    os.remove(p)

    prog.finish()
    return all_paths


# ── MinerU ─────────────────────────────────────────────

PY312 = Path(r"C:\Users\29595\Miniconda3\envs\py312")

def _find_mineru():
    for p in [PY312 / "Scripts" / "mineru.exe",
              PY312 / "Scripts" / "mineru",
              Path("mineru")]:
        if p.exists():
            return str(p)
    return "mineru"

def run_mineru(input_pdf, output_dir):
    output_dir = Path(output_dir)
    for d in output_dir.glob("*/ocr"):
        if list(d.glob("*_middle.json")):
            print(f"[1/3] MinerU output exists: {d}")
            return d

    mineru_cmd = _find_mineru()
    print(f"[1/3] Running MinerU ({mineru_cmd}) ...")
    print(f"  input:  {input_pdf}")
    print(f"  output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    env = os.environ.copy()
    env['MINERU_DEVICE_MODE'] = 'cuda'
    proc = subprocess.Popen(
        [mineru_cmd, '-p', str(input_pdf), '-o', str(output_dir), '-b', 'hybrid'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env
    )
    for line in proc.stdout:
        elapsed = time.time() - t0
        txt = line.decode('utf-8', errors='replace').rstrip()
        if txt:
            safe = txt.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding)
            print(f"  [{_fmt_time(elapsed)}] {safe}", flush=True)
    proc.wait()
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(f"[1/3] MinerU FAILED in {_fmt_time(elapsed)} (rc={proc.returncode})")
        return None

    for d in output_dir.glob("*/ocr"):
        if list(d.glob("*_middle.json")):
            print(f"[1/3] MinerU done in {_fmt_time(elapsed)}: {d}")
            return d
    print("[1/3] MinerU output not found")
    return None


# ── Coordinate ─────────────────────────────────────────

def px_to_pdf(bbox, pw, ph, sf):
    return bbox[0]*sf, ph - bbox[3]*sf, bbox[2]*sf, ph - bbox[1]*sf


def unique_output_path(path):
    p = Path(path)
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 2
    while True:
        c = p.parent / f"{stem}_v{i}{suffix}"
        if not c.exists():
            return c
        i += 1


# ── Collect ────────────────────────────────────────────

def collect_data(pdf_infos, pw, ph, images_dir, config, font_name):
    text_cats = config['text']['categories']
    cap_cats = config['caption']['categories']
    formula_list, formula_targets, page_elements = [], [], []
    prog = Progress(len(pdf_infos), "collect", "pages")

    for pi, info in enumerate(pdf_infos):
        mps = info.get('page_size', [499, 759])
        sf = pw / mps[0]
        images, texts, titles, captions, fidx = [], [], [], [], []

        for block in info.get('preproc_blocks', []):
            bt = block.get('type')

            if bt == 'image':
                for sub in block.get('blocks', []):
                    st = sub.get('type')
                    if st == 'image_body':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                if span.get('type') == 'image':
                                    img = span.get('image_path', '')
                                    if img:
                                        full = os.path.join(str(images_dir), img)
                                        if os.path.exists(full):
                                            x1, y1, x2, y2 = px_to_pdf(span['bbox'], pw, ph, sf)
                                            images.append((full, x1, y1, x2-x1, y2-y1))
                    elif st == 'image_caption':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                c = span.get('content', '')
                                if c:
                                    bb = span['bbox']
                                    cat = find_cat_height(bb[3]-bb[1], cap_cats, CAPTION_TOLERANCE)
                                    fpt = cat['height_px'] * sf * SHRINK
                                    fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
                                    x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                                    captions.append((c, x1, y1, fpt))

            elif bt == 'chart':
                for sub in block.get('blocks', []):
                    st = sub.get('type')
                    if st == 'chart_body':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                img = span.get('image_path', '')
                                if img:
                                    full = os.path.join(str(images_dir), img)
                                    if os.path.exists(full):
                                        x1, y1, x2, y2 = px_to_pdf(span['bbox'], pw, ph, sf)
                                        images.append((full, x1, y1, x2-x1, y2-y1))
                    elif st == 'chart_caption':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                c = span.get('content', '')
                                if c:
                                    bb = span['bbox']
                                    cat = find_cat_height(bb[3]-bb[1], cap_cats, CAPTION_TOLERANCE)
                                    fpt = cat['height_px'] * sf * SHRINK
                                    fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
                                    x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                                    captions.append((c, x1, y1, fpt))

            elif bt == 'table':
                for sub in block.get('blocks', []):
                    st = sub.get('type')
                    if st == 'table_caption':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                c = span.get('content', '')
                                if c:
                                    bb = span['bbox']
                                    cat = find_cat_height(bb[3]-bb[1], cap_cats, CAPTION_TOLERANCE)
                                    fpt = cat['height_px'] * sf * SHRINK
                                    fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
                                    x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                                    captions.append((c, x1, y1, fpt))
                    elif st == 'table_body':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                img = span.get('image_path', '')
                                if img:
                                    full = os.path.join(str(images_dir), img)
                                    if os.path.exists(full):
                                        x1, y1, x2, y2 = px_to_pdf(span['bbox'], pw, ph, sf)
                                        images.append((full, x1, y1, x2-x1, y2-y1))

            elif bt == 'title':
                level = block.get('level', 2)
                for line in block.get('lines', []):
                    for span in line.get('spans', []):
                        c = span.get('content', '')
                        if not c:
                            continue
                        bb = span['bbox']
                        fpt = (bb[3]-bb[1]) * sf * SHRINK
                        fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
                        x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                        titles.append((c, x1, y1, fpt, level))

            elif bt == 'index':
                for line in block.get('lines', []):
                    lsp = []
                    for span in line.get('spans', []):
                        c = span.get('content', '')
                        if not c:
                            continue
                        bb = span['bbox']
                        fpt = (bb[3]-bb[1]) * sf * SHRINK
                        fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
                        x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                        lsp.append((c, x1, y1, fpt))
                    if lsp:
                        uf = min(s[3] for s in lsp)
                        for c, x, y, _ in lsp:
                            texts.append((c, x, y, uf))

            elif bt == 'text':
                for line in block.get('lines', []):
                    lsp = []
                    for span in line.get('spans', []):
                        stype = span.get('type', '')
                        c = span.get('content', '')
                        if not c:
                            continue
                        if stype == 'text':
                            bb = span['bbox']
                            wpx, hpx = bb[2]-bb[0], bb[3]-bb[1]
                            apc = wpx * hpx / max(len(c), 1)
                            cat = find_cat_area(apc, text_cats, TEXT_TOLERANCE)
                            fpt = cat['height_px'] * sf * SHRINK
                            lsp.append((c, bb, wpx, fpt))
                        elif stype == 'inline_equation':
                            cl = clean_latex(c)
                            if cl:
                                x1, y1, x2, y2 = px_to_pdf(span['bbox'], pw, ph, sf)
                                fidx.append(len(formula_list))
                                formula_list.append(cl)
                                formula_targets.append((x1, y1, x2-x1, y2-y1))
                    if lsp:
                        uf = min(s[3] for s in lsp)
                        for c, bb, wpx, _ in lsp:
                            fpt = calc_text_font_pt(c, wpx*sf, uf, font_name)
                            x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                            texts.append((c, x1, y1, fpt))

            elif bt == 'interline_equation':
                content = block.get('content', '')
                if not content:
                    for line in block.get('lines', []):
                        for span in line.get('spans', []):
                            c = span.get('content', '')
                            if c:
                                content = c
                                break
                        if content:
                            break
                if content and content.strip():
                    bb = block['bbox']
                    x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                    bw, bh = x2-x1, y2-y1
                    rows = split_array_rows(content.strip())
                    if rows:
                        rh = bh / len(rows)
                        for ri, rt in enumerate(rows):
                            fidx.append(len(formula_list))
                            formula_list.append(rt)
                            formula_targets.append((x1, y1+bh-rh*(ri+1), bw, rh))
                    else:
                        fidx.append(len(formula_list))
                        formula_list.append(content.strip())
                        formula_targets.append((x1, y1, bw, bh))

        headers, page_nums = [], []
        for block in info.get('discarded_blocks', []):
            bt = block.get('type')
            c = ''
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    sc = span.get('content', '')
                    if sc:
                        c += sc
            if not c:
                continue
            bb = block['bbox']
            fpt = (bb[3]-bb[1]) * sf * SHRINK
            fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
            x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
            if bt == 'header':
                headers.append((c, x1, y1, fpt))
            elif bt == 'page_number':
                page_nums.append((c, x1, y1, fpt))

        page_elements.append({
            'images': images, 'texts': texts, 'titles': titles,
            'captions': captions, 'headers': headers, 'page_numbers': page_nums,
            'formula_indices': fidx,
        })
        prog.tick()

    prog.finish()
    return formula_list, formula_targets, page_elements


# ── Build + Merge ──────────────────────────────────────

def build_and_merge(page_elements, formula_list, formula_targets, formula_pdfs,
                    pw, ph, font_name, output_pdf):
    writer = PdfWriter()
    total_pages = len(page_elements)
    total_formulas = sum(len(e['formula_indices']) for e in page_elements)

    # Phase 1: build pages
    prog = Progress(total_pages, "build", "pages")
    for pi, elems in enumerate(page_elements):
        pkt = io.BytesIO()
        c = canvas.Canvas(pkt, pagesize=(pw, ph))

        for img, x, y, w, h in elems['images']:
            try:
                c.drawImage(img, x, y, width=w, height=h)
            except:
                pass

        for ct, x, y, fpt, lv in elems['titles']:
            try:
                fn = 'msyhbd' if lv == 1 else font_name
                c.setFont(fn, fpt)
                c.drawString(x, y, ct)
            except:
                c.setFont(font_name, fpt)
                c.drawString(x, y, ct)

        for ct, x, y, fpt in elems['texts']:
            try:
                c.setFont(font_name, fpt)
                c.drawString(x, y, ct)
            except:
                pass

        for ct, x, y, fpt in elems['captions']:
            try:
                c.setFont(font_name, fpt)
                c.drawString(x, y, ct)
            except:
                pass

        for ct, x, y, fpt in elems['headers']:
            try:
                c.setFont(font_name, fpt)
                c.drawString(x, y, ct)
            except:
                pass

        for ct, x, y, fpt in elems['page_numbers']:
            try:
                c.setFont(font_name, fpt)
                c.drawString(x, y, ct)
            except:
                pass

        c.save()
        pkt.seek(0)
        rd = PdfReader(pkt)
        if len(rd.pages) > 0:
            writer.add_page(rd.pages[0])
        else:
            writer.add_blank_page(width=pw, height=ph)
        prog.tick()

    prog.finish()

    # Phase 2: merge formulas
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
            try:
                fp = PdfReader(formula_pdfs[fi]).pages[0]
                fw = float(fp.mediabox.width)
                fh = float(fp.mediabox.height)
                if fw <= 0 or fh <= 0:
                    prog.error()
                    prog.tick()
                    continue
                tx, ty, tw, th = formula_targets[fi]
                sc = (th / fh) * SHRINK
                dw, dh = fw * sc, fh * sc
                if dw > tw and dw > 0:
                    sc = sc * (tw / dw) * 0.95
                    dw, dh = fw * sc, fh * sc
                op = Transformation().scale(sc, sc).translate(tx, ty)
                fp.add_transformation(op)
                fp.mediabox = RectangleObject([tx, ty, tx + dw, ty + dh])
                page.merge_page(fp)
            except Exception as e:
                prog.error()
            prog.tick()

    prog.finish()

    with open(str(output_pdf), 'wb') as f:
        writer.write(f)
    print(f"\n  saved: {output_pdf}")


# ── Main ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='MinerU OCR + PDF reconstruction')
    parser.add_argument('input_pdf', help='Input PDF file path')
    parser.add_argument('--output-dir', help='Output directory (default: output/<pdf_name>/)')
    parser.add_argument('--config', help='Font config JSON path')
    parser.add_argument('--project-dir', help='Project root directory')
    args = parser.parse_args()

    input_pdf = Path(args.input_pdf).resolve()
    if not input_pdf.exists():
        print(f"Input PDF not found: {input_pdf}")
        return

    project_dir = Path(args.project_dir) if args.project_dir else Path(__file__).parent.parent
    pdf_name = input_pdf.stem
    output_dir = Path(args.output_dir) if args.output_dir else project_dir / "output" / pdf_name
    config_path = Path(args.config) if args.config else project_dir / "config" / "font_classes.json"

    wall_start = time.time()
    print(f"=== {pdf_name} ===")

    # Step 1: MinerU
    ocr_dir = run_mineru(input_pdf, output_dir)
    if not ocr_dir:
        return

    middle_json = list(ocr_dir.glob("*_middle.json"))[0]
    layout_pdf = list(ocr_dir.glob("*_layout.pdf"))[0]
    images_dir = ocr_dir / "images"
    output_pdf = unique_output_path(ocr_dir / f"{pdf_name}_复刻版.pdf")
    formula_dir = ocr_dir / '_batch_formulas'

    # Step 2: Load config + data
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    with open(middle_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    font_name = register_fonts()
    pw, ph = get_page_size(str(layout_pdf))
    pdf_infos = data.get('pdf_info', [])
    print(f"[2/3] {len(pdf_infos)} pages, {pw:.0f}x{ph:.0f}pt")

    formula_list, formula_targets, page_elements = collect_data(
        pdf_infos, pw, ph, images_dir, config, font_name)
    print(f"  formulas: {len(formula_list)}, "
          f"images: {sum(len(e['images']) for e in page_elements)}, "
          f"texts: {sum(len(e['texts']) for e in page_elements)}")

    # Step 3: Compile + Build + Merge
    formula_pdfs = batch_compile(formula_list, str(formula_dir))
    build_and_merge(page_elements, formula_list, formula_targets,
                    formula_pdfs, pw, ph, font_name, output_pdf)

    print(f"\n=== total: {_fmt_time(time.time() - wall_start)} ===")


if __name__ == "__main__":
    main()
