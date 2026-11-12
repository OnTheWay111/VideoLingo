from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT / "jensen-huang-stanford-2026-original.jpg"
FONT_REGULAR = "/System/Library/Fonts/STHeiti Light.ttc"
FONT_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"

GREEN = "#173F35"
GOLD = "#B7791F"
TEXT = "#24292F"
MUTED = "#57606A"
BG = "#F4F7F5"
BORDER = "#D8E0DC"
WHITE = "#FFFFFF"
WARNING = "#8A3B2E"


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def crop_resize(image, box, size):
    return image.crop(box).resize(size, Image.Resampling.LANCZOS)


def draw_wrapped(draw, text, xy, max_width, text_font, fill, line_gap=10):
    x, y = xy
    lines = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=text_font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    line_height = text_font.size + line_gap
    for line in lines:
        draw.text((x, y), line, font=text_font, fill=fill)
        y += line_height
    return y


def add_credit(draw, width, height, dark=False):
    credit = "图：Anders Eidesvik / CC BY-SA 4.0（裁切与排版）"
    credit_font = font(18)
    draw.rectangle((0, height - 42, width, height), fill=GREEN)
    text_width = draw.textlength(credit, font=credit_font)
    draw.text((width - text_width - 24, height - 32), credit, font=credit_font, fill=WHITE)


def build_portrait(original):
    portrait = crop_resize(original, (400, 900, 3600, 3300), (1200, 900))
    portrait.save(ROOT / "jensen-huang-stanford-2026.jpg", quality=90, optimize=True)


def build_hero_a(original):
    canvas = Image.new("RGB", (1920, 820), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 18, 820), fill=GREEN)
    draw.text((90, 70), "黄仁勋风波之外", font=font(36, True), fill=GREEN)
    draw.text((90, 150), "GPU 采购真正该看清的", font=font(76, True), fill=TEXT)
    draw.text((90, 255), "这道合规关", font=font(104, True), fill=GREEN)
    draw.text((90, 405), "注册地不是全部答案，证据链才是", font=font(36), fill=MUTED)
    draw.line((90, 505, 1110, 505), fill=BORDER, width=3)
    labels = ["物项规则", "交易主体", "最终用途", "部署地点", "异常处置"]
    x = 90
    for index, label in enumerate(labels, 1):
        draw.ellipse((x, 570, x + 54, 624), fill=GREEN if index < 5 else GOLD)
        draw.text((x + 15, 580), str(index), font=font(24, True), fill=WHITE)
        draw.text((x, 646), label, font=font(28, True), fill=TEXT)
        if index < 5:
            draw.line((x + 66, 597, x + 174, 597), fill=BORDER, width=4)
        x += 210
    portrait = crop_resize(original, (760, 760, 3200, 3700), (690, 820))
    canvas.paste(portrait, (1230, 0))
    draw.rectangle((1224, 0, 1230, 820), fill=GREEN)
    add_credit(draw, 1920, 820, dark=False)
    canvas.save(ROOT / "hero-draft-a.png", optimize=True)
    canvas.save(ROOT.parent / "hero.png", optimize=True)
    canvas.resize((900, 384), Image.Resampling.LANCZOS).save(ROOT.parent / "hero-feed.png", optimize=True)
    canvas.resize((1200, 512), Image.Resampling.LANCZOS).save(ROOT.parent / "hero-safe-wide.png", optimize=True)


def build_hero_b(original):
    canvas = Image.new("RGB", (1920, 820), WHITE)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1920, 18), fill=GOLD)
    draw.text((90, 60), "GPU 采购合规复核", font=font(70, True), fill=TEXT)
    draw.text((90, 155), "不是一张表，而是一条证据链", font=font(38), fill=GREEN)
    draw.rounded_rectangle((90, 245, 1260, 730), radius=18, fill=BG, outline=BORDER, width=3)
    checks = [
        ("01", "物项与规则", "ECCN、目的地、版本与许可"),
        ("02", "主体与用途", "最终母公司、最终用户、实际业务"),
        ("03", "部署与异常", "安装地址、基础设施、复核结论"),
    ]
    y = 290
    for number, title, detail in checks:
        draw.text((140, y), number, font=font(34, True), fill=GOLD)
        draw.text((245, y), title, font=font(38, True), fill=GREEN)
        draw.text((490, y + 5), detail, font=font(30), fill=TEXT)
        y += 135
    portrait = crop_resize(original, (850, 800, 3100, 3730), (560, 820))
    canvas.paste(portrait, (1360, 0))
    add_credit(draw, 1920, 820)
    canvas.save(ROOT / "hero-draft-b.png", optimize=True)


def build_hero_square(original):
    canvas = Image.new("RGB", (900, 900), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 14, 900), fill=GREEN)
    draw.text((60, 58), "黄仁勋风波之外", font=font(30, True), fill=GREEN)
    draw.text((60, 120), "GPU 采购", font=font(70, True), fill=TEXT)
    draw.text((60, 210), "真正该看清的", font=font(58, True), fill=TEXT)
    draw.text((60, 290), "这道合规关", font=font(70, True), fill=GREEN)
    draw.text((60, 400), "注册地不是全部答案", font=font(30), fill=MUTED)
    portrait = crop_resize(original, (760, 760, 3200, 3700), (360, 428))
    canvas.paste(portrait, (540, 472))
    draw.line((60, 505, 470, 505), fill=BORDER, width=3)
    items = ["物项规则", "交易主体", "最终用途", "部署地点", "异常处置"]
    y = 555
    for index, item in enumerate(items, 1):
        draw.ellipse((60, y, 92, y + 32), fill=GREEN if index < 5 else GOLD)
        draw.text((70, y + 4), str(index), font=font(18, True), fill=WHITE)
        draw.text((110, y - 2), item, font=font(28, True), fill=TEXT)
        y += 58
    add_credit(draw, 900, 900)
    canvas.save(ROOT.parent / "hero-safe-square.png", optimize=True)


def build_chain():
    canvas = Image.new("RGB", (1080, 1500), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((70, 60), "一笔 GPU 交易，要串起五类证据", font=font(54, True), fill=TEXT)
    draw.text((70, 140), "注册地只是其中一项，不是最终答案", font=font(32), fill=MUTED)
    items = [
        ("1", "物项与规则", "确认 ECCN、目的地、规则版本与许可要求"),
        ("2", "交易主体", "核对买方、收货方、最终母公司与控制关系"),
        ("3", "最终用户与用途", "说明谁使用、用于什么业务，是否涉及算力服务"),
        ("4", "交付与部署", "核实安装地址及供电、冷却、空间等基础设施"),
        ("5", "异常处置", "补充证据、复核留痕；无法解释时停止推进"),
    ]
    y = 240
    for index, (number, title, detail) in enumerate(items):
        draw.rounded_rectangle((70, y, 1010, y + 180), radius=16, fill=WHITE, outline=BORDER, width=2)
        circle_color = GREEN if index < 4 else GOLD
        draw.ellipse((105, y + 48, 185, y + 128), fill=circle_color)
        draw.text((127, y + 62), number, font=font(38, True), fill=WHITE)
        draw.text((225, y + 35), title, font=font(40, True), fill=GREEN)
        draw_wrapped(draw, detail, (225, y + 100), 720, font(29), TEXT, line_gap=8)
        if index < 4:
            draw.line((145, y + 180, 145, y + 210), fill=GREEN, width=5)
        y += 210
    draw.rounded_rectangle((70, 1300, 1010, 1415), radius=12, fill="#F8F1E8", outline="#E4C99E", width=2)
    draw.text((105, 1323), "红旗没有解释清楚：不要先交货，再补手续。", font=font(32, True), fill=WARNING)
    draw.text((70, 1450), "依据：BIS KYC 与先进计算防转移指引｜规则核对日期：2026-11-12", font=font(22), fill=MUTED)
    canvas.save(ROOT / "compliance-evidence-chain.png", optimize=True)


def main():
    original = Image.open(ORIGINAL).convert("RGB")
    build_portrait(original)
    build_hero_a(original)
    build_hero_b(original)
    build_hero_square(original)
    build_chain()
    print("visual assets generated")


if __name__ == "__main__":
    main()
