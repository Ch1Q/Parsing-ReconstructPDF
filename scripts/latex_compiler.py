"""
LaTeX 公式编译模块
- 支持中文公式（ctex）
- 批量编译
- 多行公式拆分
"""

import os
import re
import subprocess
from pathlib import Path
from pypdf import PdfReader, PdfWriter

# ── 常量 ──────────────────────────────────────────────

REF_FONT_PT = 12.0
BATCH_SIZE = 500  # 增大批次，减少字体嵌入次数

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

# ── LaTeX 清理 ─────────────────────────────────────────

def clean_latex(s, debug=False):
    """清理 LaTeX 公式，保留中文"""
    original = s
    s = re.sub(r'\\frac\s+(\w)\s+(\w)', r'\\frac{\1}{\2}', s)
    s = s.replace('\\left', '').replace('\\right', '')
    # 保留 array 环境（用于多行公式）
    # s = re.sub(r'\\begin\s*\{\s*array\s*\}[^}]*\}', '', s)
    # s = s.replace('\\end{array}', '')
    s = s.replace('\\ ', ' ')
    
    if debug and s != original:
        print(f"    [clean] 原始: {original[:50]}...")
        print(f"    [clean] 清理后: {s[:50]}...")
    
    return s.strip()


def has_chinese(text):
    """检测文本是否包含中文"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def wrap_chinese_in_text(formula, debug=False):
    """
    将公式中的中文部分用 \mbox{} 包裹，让 xeCJK 处理
    例如: P_e = \frac{错误码元数}{传输总码元数}
    ->   P_e = \frac{\mbox{错误码元数}}{\mbox{传输总码元数}}
    """
    if not has_chinese(formula):
        return formula
    
    # 匹配连续的中文字符（可能包含空格、标点）
    # 在数学模式中，中文需要被 \mbox{} 包裹
    result = re.sub(
        r'([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+(?:\s*[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+)*)',
        r'\\mbox{\1}',
        formula
    )
    
    if debug and result != formula:
        print(f"    [wrap] 原始: {formula[:80]}...")
        print(f"    [wrap] 包裹后: {result[:80]}...")
    
    return result


def split_array_rows(content, debug=False):
    """拆分多行 array 公式"""
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
    
    if debug:
        print(f"    [split] 拆分 {len(rows)} 行:")
        for i, r in enumerate(result):
            print(f"      [{i}] {r[:60]}...")
    
    return result

# ── LaTeX 模板 ─────────────────────────────────────────

def make_latex_template(formulas, debug=False):
    """生成支持中文的 LaTeX 模板"""
    
    # 预处理：将公式中的中文用 \text{} 包裹
    processed_formulas = []
    for f in formulas:
        wrapped = wrap_chinese_in_text(f, debug=debug)
        processed_formulas.append(wrapped)
    
    # 检测是否包含中文
    has_cn = any(has_chinese(f) for f in processed_formulas)
    
    if has_cn and debug:
        print(f"    [template] 检测到中文公式，启用 xeCJK 支持")
    
    # 根据公式内容选择模式：align/aligned 等环境用 \[...\]，普通公式用 $...$
    pages = []
    for f in processed_formulas:
        needs_display = bool(re.search(r'\\begin\{(align|aligned|gather|eqnarray)', f))
        if needs_display:
            pages.append(f"\\begin{{preview}}\n\\[{f}\\]\n\\end{{preview}}\n\\newpage")
        else:
            pages.append(f"\\begin{{preview}}\n${f}$\n\\end{{preview}}\n\\newpage")
    
    # 基础模板
    template = r"""\documentclass{article}
\usepackage[active,tightpage]{preview}
\usepackage{amsmath}
\usepackage{unicode-math}
"""
    
    # 如果有中文，添加 xeCJK 支持
    if has_cn:
        template += r"""\usepackage{xeCJK}
\setCJKmainfont{Microsoft YaHei}
"""
    
    template += rf"""\setmathfont{{Asana-Math}}
\fontsize{{{REF_FONT_PT:.1f}}}{{{REF_FONT_PT*1.2:.1f}}}\selectfont
\begin{{document}}
{chr(10).join(pages)}
\end{{document}}
"""
    
    if debug:
        print(f"    [template] LaTeX 模板生成，{len(formulas)} 个公式")
        # 打印包含中文的公式示例
        cn_formulas = [f for f in formulas if has_chinese(f)]
        if cn_formulas:
            print(f"    [template] 中文公式示例: {cn_formulas[0][:60]}...")
    
    return template

# ── 批量编译 ──────────────────────────────────────────

def batch_compile(formulas, output_dir, debug=False):
    """批量编译 LaTeX 公式为矢量 PDF"""
    if not formulas:
        return []
    
    os.makedirs(output_dir, exist_ok=True)
    all_paths = []
    prog = Progress(len(formulas), "compile", "formulas")
    
    if debug:
        print(f"  [compile] 开始编译 {len(formulas)} 个公式")
        print(f"  [compile] 输出目录: {output_dir}")
    
    for bi in range(0, len(formulas), BATCH_SIZE):
        batch = formulas[bi:bi + BATCH_SIZE]
        bdir = os.path.join(output_dir, f'batch_{bi}')
        os.makedirs(bdir, exist_ok=True)
        
        # 生成 LaTeX 模板
        tex_content = make_latex_template(batch, debug=debug)
        
        tex_path = os.path.join(bdir, 'batch.tex')
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)
        
        if debug:
            print(f"    [compile] 批次 {bi//BATCH_SIZE}: {len(batch)} 个公式")
        
        # 编译
        env = os.environ.copy()
        env['MIKTEX_ENABLE_INSTALLER'] = '1'
        
        try:
            result = subprocess.run(
                ['xelatex', '-interaction=nonstopmode', '-output-directory', bdir, tex_path],
                capture_output=True, timeout=600, env=env
            )
        except subprocess.TimeoutExpired:
            print(f"    [compile] 批次 {bi//BATCH_SIZE} 超时")
            prog.errors += len(batch)
            prog.update(len(batch))
            continue
        
        pdf_path = os.path.join(bdir, 'batch.pdf')
        if not os.path.exists(pdf_path):
            prog.errors += len(batch)
            prog.update(len(batch))
            print(f"    [compile] 批次 {bi//BATCH_SIZE} FAILED ({len(batch)} formulas)")
            
            if debug:
                log_path = os.path.join(bdir, 'batch.log')
                if os.path.exists(log_path):
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                        for line in lf:
                            if '!' in line:
                                print(f"      ERROR: {line.strip()[:120]}")
            continue
        
        reader = PdfReader(pdf_path)
        if len(reader.pages) != len(batch):
            print(f"    [compile] 批次 {bi//BATCH_SIZE}: expected {len(batch)} pages, got {len(reader.pages)}")
            
            if debug:
                log_path = os.path.join(bdir, 'batch.log')
                if os.path.exists(log_path):
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                        for line in lf:
                            if '!' in line:
                                print(f"      ERROR: {line.strip()[:120]}")
        
        # 不拆分，直接返回 (pdf_path, page_index) 引用
        for i in range(len(reader.pages)):
            all_paths.append((pdf_path, i))
        
        prog.update(len(batch))
        
        # 清理临时文件
        if len(reader.pages) == len(batch):
            for ext in ['.tex', '.aux', '.log']:
                p = os.path.join(bdir, 'batch' + ext)
                if os.path.exists(p):
                    os.remove(p)
    
    prog.finish()
    
    if debug:
        print(f"  [compile] 编译完成: {len(all_paths)} 个公式 PDF")
        print(f"  [compile] 错误数: {prog.errors}")
    
    return all_paths

# ── 测试 ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=== 测试 latex_compiler.py ===\n")
    
    # 测试中文检测
    test_cases = [
        ("E = mc^2", False),
        (r"\text{能量} = mc^2", True),
        (r"\frac{1}{2}mv^2", False),
        (r"\text{速度} = \frac{s}{t}", True),
    ]
    
    print("1. 测试中文检测:")
    for formula, expected in test_cases:
        result = has_chinese(formula)
        status = "✓" if result == expected else "✗"
        print(f"   {status} '{formula}' -> {result}")
    
    print("\n2. 测试 LaTeX 模板生成:")
    formulas = [r"E = mc^2", r"\text{动能} = \frac{1}{2}mv^2"]
    template = make_latex_template(formulas, debug=True)
    print(f"   模板长度: {len(template)} 字符")
    
    print("\n=== 测试完成 ===")