"""
MinerU OCR + 排版复刻 - 重构版
- 使用独立模块（fonts, latex_compiler, page_builder）
- 字体大小 block 级统一（众数）
- 支持中文公式（xeCJK）
- 调试信息输出
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
import time
import argparse
from pathlib import Path
from collections import Counter
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader

# 导入重构模块
from fonts import (
    register_fonts, find_cat_height, find_cat_area,
    calc_span_font_pt, calc_text_font_pt,
    unify_block_font_size, unify_line_font_size,
    debug_font_distribution,
    SHRINK, TEXT_TOLERANCE, CAPTION_TOLERANCE, REF_FONT_PT
)
from latex_compiler import (
    clean_latex, split_array_rows, batch_compile, has_chinese
)
from page_builder import build_and_merge

# ── 常量 ──────────────────────────────────────────────

DEBUG = True  # 调试模式

# ── 公式序号提取 ──────────────────────────────────────

def extract_tag(content, debug=False):
    """
    从公式内容中提取 \tag{} 公式序号
    返回: (cleaned_content, tag_text)
    """
    if not content:
        return content, ''
    
    # 匹配 \tag{...} 模式
    tag_pattern = r'\\tag\{([^}]*)\}'
    match = re.search(tag_pattern, content)
    
    if match:
        tag_text = match.group(1)  # 提取 tag 内容（如 "4.1.1"）
        cleaned = content[:match.start()] + content[match.end():]  # 移除 \tag{}
        cleaned = cleaned.strip()
        
        if debug:
            print(f"    [tag] 提取公式序号: {tag_text}")
            print(f"    [tag] 清理后公式: {cleaned[:50]}...")
        
        return cleaned, tag_text
    
    return content, ''

# ── MinerU ─────────────────────────────────────────────

PY312 = Path(r"C:\Users\29595\Miniconda3\envs\py312")

def _find_mineru():
    for p in [PY312 / "Scripts" / "mineru.exe",
              PY312 / "Scripts" / "mineru",
              Path("mineru")]:
        if p.exists():
            return str(p)
    return "mineru"


def _fmt_time(s):
    if s < 60:
        return f"{s:.0f}s"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def run_mineru(input_pdf, output_dir):
    """运行 MinerU OCR"""
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


# ── 坐标转换 ─────────────────────────────────────────

def px_to_pdf(bbox, pw, ph, sf):
    """像素坐标转PDF坐标"""
    return bbox[0]*sf, ph - bbox[3]*sf, bbox[2]*sf, ph - bbox[1]*sf


def unique_output_path(path):
    """生成唯一的输出路径"""
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


def get_page_size(path):
    """获取PDF页面尺寸"""
    try:
        box = PdfReader(path).pages[0].mediabox
        return float(box.width), float(box.height)
    except:
        return A4[0], A4[1]


# ── 数据收集（重构版：block级统一） ─────────────────────

def collect_data(pdf_infos, pw, ph, images_dir, config, font_name):
    """
    收集所有页面的数据
    改进：字体大小 block 级统一（众数）
    """
    text_cats = config['text']['categories']
    cap_cats = config['caption']['categories']
    formula_list, formula_targets, page_elements = [], [], []
    
    print(f"\n  [collect] 开始收集 {len(pdf_infos)} 页数据")
    print(f"  [collect] 字体类别: {[c['name'] for c in text_cats]}")

    for pi, info in enumerate(pdf_infos):
        mps = info.get('page_size', [499, 759])
        sf = pw / mps[0]
        images, texts, titles, captions, fidx = [], [], [], [], []
        
        if DEBUG:
            print(f"\n  [collect] 第 {pi+1} 页 (sf={sf:.3f}):")

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
                # 目录/索引：block 级统一
                block_spans = []
                for line in block.get('lines', []):
                    for span in line.get('spans', []):
                        c = span.get('content', '')
                        if not c:
                            continue
                        bb = span['bbox']
                        fpt = (bb[3]-bb[1]) * sf * SHRINK
                        fpt = calc_text_font_pt(c, (bb[2]-bb[0])*sf, fpt, font_name)
                        x1, y1, x2, y2 = px_to_pdf(bb, pw, ph, sf)
                        block_spans.append((c, x1, y1, fpt))
                
                if block_spans:
                    # block 级统一
                    unified_fpt = unify_block_font_size([(c, None, 0, fpt) for c, x, y, fpt in block_spans], debug=DEBUG)
                    for c, x, y, _ in block_spans:
                        texts.append((c, x, y, unified_fpt))

            elif bt == 'text':
                # 正文：block 级统一
                block_all_spans = []  # 收集整个 block 的所有 span
                
                for line in block.get('lines', []):
                    line_spans = []
                    for span in line.get('spans', []):
                        stype = span.get('type', '')
                        c = span.get('content', '')
                        if not c:
                            continue
                        if stype == 'text':
                            bb = span['bbox']
                            wpx, hpx = bb[2]-bb[0], bb[3]-bb[1]
                            fpt, cat_name = calc_span_font_pt(wpx, hpx, len(c), sf, text_cats, TEXT_TOLERANCE)
                            line_spans.append((c, bb, wpx, fpt, cat_name))
                            block_all_spans.append((c, bb, wpx, fpt))
                        elif stype == 'inline_equation':
                            cl = clean_latex(c)
                            if cl:
                                x1, y1, x2, y2 = px_to_pdf(span['bbox'], pw, ph, sf)
                                fidx.append(len(formula_list))
                                formula_list.append(cl)
                                formula_targets.append((x1, y1, x2-x1, y2-y1))
                    
                    # 行内统一（保留用于调试）
                    if line_spans:
                        unify_line_font_size([(c, bb, wpx, fpt) for c, bb, wpx, fpt, _ in line_spans], debug=DEBUG)
                
                # block 级统一
                if block_all_spans:
                    unified_fpt = unify_block_font_size(block_all_spans, debug=DEBUG)
                    
                    # 统一后重新计算每个 span（考虑宽度校验）
                    for line in block.get('lines', []):
                        for span in line.get('spans', []):
                            stype = span.get('type', '')
                            c = span.get('content', '')
                            if not c or stype != 'text':
                                continue
                            bb = span['bbox']
                            wpx = bb[2]-bb[0]
                            fpt = calc_text_font_pt(c, wpx*sf, unified_fpt, font_name)
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
                    
                    # 提取公式序号 \tag{}
                    cleaned_content, tag_text = extract_tag(content.strip(), debug=DEBUG)
                    
                    # 处理多行公式
                    rows = split_array_rows(cleaned_content, debug=DEBUG)
                    if rows:
                        rh = bh / len(rows)
                        for ri, rt in enumerate(rows):
                            fidx.append(len(formula_list))
                            formula_list.append(rt)
                            formula_targets.append((x1, y1+bh-rh*(ri+1), bw, rh))
                    else:
                        fidx.append(len(formula_list))
                        formula_list.append(cleaned_content)
                        formula_targets.append((x1, y1, bw, bh))
                    
                    # 如果有公式序号，添加为文本块（绘制在页面最右侧）
                    if tag_text:
                        # 公式序号位置：页面最右侧，垂直居中
                        tag_fpt = min(bh * 0.6, 10.0)  # 高度的60%，最大10pt
                        # 计算文本宽度，右对齐到页面边缘
                        tag_width = len(tag_text) * tag_fpt * 0.6  # 估算文本宽度
                        tag_x = pw - tag_width - 10  # 页面右侧留 10pt 边距
                        tag_y = y1 + bh / 2  # 垂直居中
                        texts.append((tag_text, tag_x, tag_y, tag_fpt))
                        
                        if DEBUG:
                            print(f"    [tag] 添加公式序号文本: '{tag_text}' at ({tag_x:.0f}, {tag_y:.0f}) {tag_fpt:.1f}pt")

        # 页眉页脚
        headers, page_nums, footers = [], [], []
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
            elif bt == 'footer':
                footers.append((c, x1, y1, fpt))
                if DEBUG:
                    print(f"    [footer] 页脚: '{c}' at ({x1:.0f}, {y1:.0f}) {fpt:.1f}pt")

        page_elements.append({
            'images': images, 'texts': texts, 'titles': titles,
            'captions': captions, 'headers': headers, 'page_numbers': page_nums,
            'footers': footers, 'formula_indices': fidx,
        })

    # 打印字号分布
    debug_font_distribution(page_elements)

    return formula_list, formula_targets, page_elements


# ── Main ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='MinerU OCR + PDF reconstruction (v2)')
    parser.add_argument('input_pdf', help='Input PDF file path')
    parser.add_argument('--output-dir', help='Output directory (default: output/<pdf_name>/)')
    parser.add_argument('--config', help='Font config JSON path')
    parser.add_argument('--project-dir', help='Project root directory')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug or True  # 默认开启调试

    input_pdf = Path(args.input_pdf).resolve()
    if not input_pdf.exists():
        print(f"Input PDF not found: {input_pdf}")
        return

    project_dir = Path(args.project_dir) if args.project_dir else Path(__file__).parent.parent
    pdf_name = input_pdf.stem
    output_dir = Path(args.output_dir) if args.output_dir else project_dir / "output" / pdf_name
    config_path = Path(args.config) if args.config else project_dir / "config" / "font_classes.json"

    wall_start = time.time()
    print(f"=== {pdf_name} (v2) ===")
    print(f"  调试模式: {'开启' if DEBUG else '关闭'}")

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
    print(f"\n[2/3] {len(pdf_infos)} pages, {pw:.0f}x{ph:.0f}pt")

    formula_list, formula_targets, page_elements = collect_data(
        pdf_infos, pw, ph, images_dir, config, font_name)
    
    print(f"\n  汇总:")
    print(f"    公式: {len(formula_list)}")
    print(f"    图片: {sum(len(e['images']) for e in page_elements)}")
    print(f"    文本: {sum(len(e['texts']) for e in page_elements)}")
    
    # 检查中文公式
    cn_formulas = [f for f in formula_list if has_chinese(f)]
    if cn_formulas:
        print(f"    中文公式: {len(cn_formulas)}")

    # Step 3: Compile + Build + Merge
    formula_pdfs = batch_compile(formula_list, str(formula_dir), debug=DEBUG)
    build_and_merge(page_elements, formula_list, formula_targets,
                    formula_pdfs, pw, ph, font_name, output_pdf, debug=DEBUG)

    print(f"\n=== total: {_fmt_time(time.time() - wall_start)} ===")


if __name__ == "__main__":
    main()