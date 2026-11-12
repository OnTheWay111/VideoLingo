from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN = ROOT / "article.md"

P_STYLE = "margin:0 0 14px 0;color:#24292F;font-size:16px;line-height:1.85;overflow-wrap:anywhere;word-break:break-word;"
H2_STYLE = "margin:28px 0 14px 0;padding:0 0 8px 0;border-bottom:1px solid #D8E0DC;font-size:20px;line-height:1.5;font-weight:700;color:#111111;"
LINK_STYLE = "color:#173F35;text-decoration:underline;overflow-wrap:anywhere;word-break:break-all;"
IMG_STYLE = "display:block;margin:0 auto 10px auto;width:100%;max-width:100%;height:auto;box-sizing:border-box;"


def inline(text):
    value = escape(text, quote=True)
    value = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{match.group(2)}" style="{LINK_STYLE}">{match.group(1)}</a>',
        value,
    )
    value = re.sub(r"\*\*(.+?)\*\*", r'<strong style="font-weight:700;color:#111111;">\1</strong>', value)
    value = re.sub(r"\*([^*]+)\*", r'<em style="font-style:italic;">\1</em>', value)
    return value


def render(markdown):
    lines = markdown.splitlines()
    title = next(line[2:].strip() for line in lines if line.startswith("# "))
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("# "):
            index += 1
            continue
        if line.startswith("## "):
            blocks.append(f'<h2 style="{H2_STYLE}">{inline(line[3:].strip())}</h2>')
            index += 1
            continue
        if line.startswith("> "):
            blocks.append(
                '<blockquote style="margin:18px 0;padding:10px 0 10px 14px;border-left:4px solid #173F35;color:#111111;line-height:1.8;">'
                f'<p style="margin:0;color:#111111;font-weight:700;">{inline(line[2:].strip())}</p></blockquote>'
            )
            index += 1
            continue
        image = re.fullmatch(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if image:
            alt, src = image.groups()
            blocks.append(
                f'<section style="margin:18px 0 20px 0;max-width:100%;box-sizing:border-box;">'
                f'<img src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}" style="{IMG_STYLE}"></section>'
            )
            index += 1
            continue
        if line == "---":
            blocks.append('<section style="margin:26px 0;border-top:1px solid #D8E0DC;"></section>')
            index += 1
            continue
        if re.match(r"^\d+\. ", line):
            items = []
            while index < len(lines) and re.match(r"^\d+\. ", lines[index].strip()):
                item = re.sub(r"^\d+\. ", "", lines[index].strip())
                items.append(f'<li style="margin:0 0 12px 0;padding:0 0 0 2px;">{inline(item)}</li>')
                index += 1
            blocks.append(
                '<ol style="margin:0 0 20px 0;padding:0 0 0 24px;color:#24292F;font-size:16px;line-height:1.85;">'
                + "".join(items)
                + "</ol>"
            )
            continue
        if line.startswith("- "):
            items = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(
                    f'<li style="margin:0 0 10px 0;padding:0 0 0 2px;overflow-wrap:anywhere;word-break:break-word;">{inline(lines[index].strip()[2:])}</li>'
                )
                index += 1
            blocks.append(
                '<ul style="margin:0 0 20px 0;padding:0 0 0 22px;color:#57606A;font-size:14px;line-height:1.75;">'
                + "".join(items)
                + "</ul>"
            )
            continue
        style = P_STYLE
        if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            style = "margin:0 0 16px 0;color:#57606A;font-size:13px;line-height:1.75;overflow-wrap:anywhere;word-break:break-word;"
        blocks.append(f'<p style="{style}">{inline(line)}</p>')
        index += 1
    body = (
        '<meta charset="utf-8">\n'
        '<section style="margin:0 auto;padding:0;max-width:100%;box-sizing:border-box;color:#24292F;font-size:16px;line-height:1.85;font-family:-apple-system,BlinkMacSystemFont,\'Helvetica Neue\',\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;">\n'
        + "\n".join(blocks)
        + "\n</section>\n"
    )
    return title, body


def build_preview(title, body):
    article_body = body.split("\n", 1)[1]
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #EEF2F0; color: #24292F; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; }}
main {{ max-width: 760px; margin: 0 auto; background: #FFFFFF; }}
.article {{ padding: 28px 22px 36px; }}
h1 {{ margin: 0 0 22px; font-size: 26px; line-height: 1.42; color: #111111; }}
.publishing {{ padding: 28px 22px 40px; border-top: 10px solid #EEF2F0; }}
.publishing h2 {{ margin: 0 0 16px; font-size: 20px; color: #173F35; }}
.publishing h3 {{ margin: 24px 0 10px; font-size: 16px; color: #111111; }}
.publishing p {{ color: #57606A; font-size: 14px; line-height: 1.75; overflow-wrap: anywhere; }}
.publishing img {{ display: block; width: 100%; height: auto; margin: 8px 0 16px; }}
.square {{ max-width: 420px; }}
.notice {{ padding: 12px 14px; background: #F4F7F5; border-left: 4px solid #173F35; }}
@media (max-width: 480px) {{ .article {{ padding: 24px 18px 32px; }} h1 {{ font-size: 24px; }} }}
</style>
</head>
<body>
<main>
<article class="article">
<h1>{escape(title)}</h1>
{article_body}
</article>
<section class="publishing">
<h2>发布字段与封面审核</h2>
<p class="notice">请浏览并确认文章内容和视觉格式；确认后再发布。人物改编图采用 CC BY-SA 4.0，发布时须保留正文中的摄影署名、来源、许可和修改说明。</p>
<h3>平台摘要</h3>
<p>从一张 GPU 采购截图出发，分清公开事实与未决指控，用五类证据复核交易主体、用途、部署和异常处置。</p>
<h3>主封面 hero.png（1920×820）</h3>
<img src="hero.png" alt="黄仁勋风波之外，GPU采购真正该看清的这道合规关">
<h3>信息流缩略图 hero-feed.png（900×384）</h3>
<img src="hero-feed.png" alt="横版信息流封面缩略图">
<h3>横版安全裁切 hero-safe-wide.png（1200×512）</h3>
<img src="hero-safe-wide.png" alt="横版安全裁切封面">
<h3>方形安全裁切 hero-safe-square.png（900×900）</h3>
<img class="square" src="hero-safe-square.png" alt="方形安全裁切封面">
<h3>发布前人工步骤</h3>
<p>将 article.html 正文粘贴到公众号编辑器后，在后台重新上传并替换两张本地正文图片；上传所选封面，核对标题、摘要、图片署名和全部资料链接。本文未执行外部发布。</p>
</section>
</main>
</body>
</html>
'''


def main():
    title, body = render(MARKDOWN.read_text(encoding="utf-8"))
    (ROOT / "article.html").write_text(body, encoding="utf-8")
    (ROOT / "article.preview.html").write_text(build_preview(title, body), encoding="utf-8")
    for src in re.findall(r'<img src="([^"]+)"', body):
        if not (ROOT / src).is_file():
            raise FileNotFoundError(src)
    print(f"generated HTML for: {title}")


if __name__ == "__main__":
    main()
