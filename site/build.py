#!/usr/bin/env python3
"""
Bilingual (EN / DE) multi-page static site builder for https://kreier.github.io/H3C_MAGIC_RT3000/

Features:
- 3-column docs layout inspired by FlyFish-go/IPQ50XX-Uboot:
  * Left sidebar: categorized multi-page navigation with labeled sections
  * Center: focused page content + breadcrumbs + Prev/Next pager
  * Right sidebar: auto-extracted "On this page" (h2/h3) scrollspy TOC
- Top-right language switcher (English <-> Deutsch) preserving the exact page,
  GitHub repository button, and Light/Dark theme toggle.
- Pure Python 3 standard library only (zero npm/Ruby dependencies).
- Built-in validation: contiguous 128 MiB partition math, zero broken internal
  links/anchors/assets across all generated EN & DE pages.

Usage:
    python3 site/build.py
    python3 -m http.server -d site/dist 8000
"""

import html
import json
import os
import posixpath
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
DIST_DIR = SITE_DIR / "dist"
DATA_FILE = SITE_DIR / "data" / "partitions.json"
LAYOUT_FILE = SITE_DIR / "layout.html"
PAGES_DIR = SITE_DIR / "pages"
ASSETS_SRC = SITE_DIR / "assets"
SCREENSHOT_SRC = ROOT / "docs" / "landing_page.jpg"

# Navigation tree (ordered — also defines Prev/Next pager order)
NAV_GROUPS = [
    {
        "group": None,
        "items": [
            {
                "path": "index.html",
                "en": ("Home", "Complete OpenWrt 21.02.7 (QSDK 11.5) and IPQ50XX-Uboot guide for H3C Magic RT3000 HW-B."),
                "de": ("Startseite", "Vollständige OpenWrt-21.02.7- (QSDK 11.5) und IPQ50XX-Uboot-Anleitung für den H3C Magic RT3000 HW-B."),
            },
        ],
    },
    {
        "group": {"en": "Hardware", "de": "Hardware"},
        "items": [
            {
                "path": "hardware/index.html",
                "en": ("Hardware & Variants", "Identify HW-A/B/C/D PCBs via 0:CDT and 0:ART, verify NAND/ECC parameters."),
                "de": ("Hardware & Varianten", "HW-A/B/C/D-Platinen über 0:CDT und 0:ART bestimmen und NAND/ECC prüfen."),
            },
            {
                "path": "hardware/partitions.html",
                "en": ("128 MiB Partition Layout", "Interactive 128 MiB SPI NAND flash diagram and complete 21-partition MIBIB table."),
                "de": ("128-MiB-Partitionslayout", "Interaktives 128-MiB-SPI-NAND-Diagramm und vollständige 21-Partitionen-MIBIB-Tabelle."),
                "embed_partitions": True,
            },
            {
                "path": "hardware/stock-firmware.html",
                "en": ("Stock Firmware (QSDK 4.4)", "Original H3C RT3000V100R005L01 web UI, Chaos Calmer 15.05 shell, and stock env."),
                "de": ("Original-Firmware (QSDK 4.4)", "Originale H3C-Weboberfläche RT3000V100R005L01, Chaos-Calmer-Shell und Standard-Env."),
            },
        ],
    },
    {
        "group": {"en": "Bootloader & Firmware", "de": "Bootloader & Firmware"},
        "items": [
            {
                "path": "bootloader/index.html",
                "en": ("uBootKit Origin & Slot", "Origin of IPQ50XX-Uboot (QSDK 12.5 -> uBootKit) and APPSBL/APPSBL_1 installation slots."),
                "de": ("uBootKit-Herkunft & Aufbau", "Herkunft von IPQ50XX-Uboot (QSDK 12.5 -> uBootKit) und Installation in APPSBL/APPSBL_1."),
            },
            {
                "path": "firmware/index.html",
                "en": ("OpenWrt 21.02.7 (QSDK 11.5)", "OpenWrt 21.02.7 QSDK 11.5 with NSS hardware offload, .ubi vs sysupgrade, LuCI setup."),
                "de": ("OpenWrt 21.02.7 (QSDK 11.5)", "OpenWrt 21.02.7 QSDK 11.5 mit NSS-Beschleunigung, .ubi vs. sysupgrade und LuCI-Einrichtung."),
            },
        ],
    },
    {
        "group": {"en": "Installation", "de": "Installation"},
        "items": [
            {
                "path": "install/telnet-backup.html",
                "en": ("1. Telnet & MTD Backup", "Enable Telnet via RT3000.cfg and create verified MTD backups before touching flash."),
                "de": ("1. Telnet & MTD-Backup", "Telnet über RT3000.cfg aktivieren und verifizierte MTD-Backups erstellen."),
            },
            {
                "path": "install/uboot.html",
                "en": ("2. Install U-Boot", "Flash IPQ50XX-Uboot v0.2.0 safely into mtd12 & mtd11 via Telnet or BootBridge Web."),
                "de": ("2. U-Boot installieren", "IPQ50XX-Uboot v0.2.0 sicher per Telnet in mtd12 & mtd11 oder über BootBridge Web installieren."),
            },
            {
                "path": "install/openwrt.html",
                "en": ("3. Flash OpenWrt", "Enter U-Boot Web Rescue on 192.168.10.1 and flash OpenWrt into Slot 2 (rootfs_1)."),
                "de": ("3. OpenWrt flashen", "U-Boot-Web-Rettungsseite unter 192.168.10.1 starten und OpenWrt in Slot 2 flashen."),
            },
        ],
    },
    {
        "group": {"en": "Operations & Tools", "de": "Betrieb & Werkzeuge"},
        "items": [
            {
                "path": "use/dual-boot.html",
                "en": ("Dual-Boot & Upgrades", "Interactive LED timing guide to switch slots without a PC and perform routine upgrades."),
                "de": ("Dual-Boot & Updates", "Interaktive LED-Zeittabelle zum Slot-Wechsel ohne PC und reguläre Updates."),
            },
            {
                "path": "tools/helper.html",
                "en": ("Helper Script (Python)", "CLI reference for scripts/rt3000_helper.py: probe, inspect-backup, releases, backup, serve."),
                "de": ("Hilfsskript (Python)", "Befehlsreferenz für scripts/rt3000_helper.py: probe, inspect-backup, releases, backup, serve."),
            },
        ],
    },
]

LANG_META = {
    "en": {
        "prefix": "",
        "brand_sub": "OpenWrt Guide",
        "alt_code": "de",
        "alt_label": "Deutsch",
        "menu_label": "Menu",
        "nav_aria": "Documentation navigation",
        "toc_title": "On this page",
        "prev_label": "Previous",
        "next_label": "Next",
    },
    "de": {
        "prefix": "de/",
        "brand_sub": "OpenWrt-Handbuch",
        "alt_code": "en",
        "alt_label": "English",
        "menu_label": "Menü",
        "nav_aria": "Dokumentationsmenü",
        "toc_title": "Auf dieser Seite",
        "prev_label": "Zurück",
        "next_label": "Weiter",
    },
}


def get_git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or "local"
    except Exception:
        return "local"


def validate_partitions(data: dict) -> None:
    chip = data["chip"]
    total_kib = chip["total_kib"]
    total_bytes = total_kib * 1024
    cursor = 0
    sum_kib = 0
    for p in data["partitions"]:
        if p["start"] != cursor:
            raise ValueError(
                f"Non-contiguous partition {p['name']}: expected 0x{cursor:08x}, got 0x{p['start']:08x}"
            )
        cursor += p["kib"] * 1024
        sum_kib += p["kib"]
    if sum_kib != total_kib or cursor != total_bytes:
        raise ValueError(f"Partition sum mismatch: {sum_kib} KiB != {total_kib} KiB")
    for mode, layout in data["layouts"].items():
        seg_sum = sum(s["kib"] for s in layout["segments"])
        if seg_sum != total_kib:
            raise ValueError(f"Layout '{mode}' sums to {seg_sum} KiB, expected {total_kib} KiB")


def render_partition_rows(data: dict, lang: str) -> str:
    total_kib = data["chip"]["total_kib"]
    rows = []
    for p in data["partitions"]:
        start = p["start"]
        end = start + p["kib"] * 1024
        kib = p["kib"]
        pct = (kib / total_kib) * 100.0
        size_str = f"{kib:,} KiB ({kib / 1024:.2f} MiB)"
        if lang == "de":
            size_str = size_str.replace(",", ".").replace(" (", " (")
        cls = "part-row uboot-row" if p.get("highlight") else "part-row"
        swatch_style = (
            "background: repeating-linear-gradient(-45deg, transparent, transparent 3px, var(--muted) 3px, var(--muted) 6px); border: 1px solid var(--border);"
            if p.get("hatch")
            else f"background: {p['color']};"
        )
        name_text = p.get("name_de") if lang == "de" and p.get("name_de") else p["name"]
        desc_text = p.get("desc_de") if lang == "de" and p.get("desc_de") else p["desc"]
        name_html = f"<strong>{html.escape(name_text)}</strong>" if p.get("highlight") else html.escape(name_text)
        rows.append(
            f'      <tr class="{cls}" data-mtd="{html.escape(p["mtd"])}">\n'
            f'        <td class="mono">{html.escape(p["mtd"])}</td>\n'
            f'        <td class="mono"><span class="swatch" style="{swatch_style};vertical-align:middle;margin-right:6px;"></span>{name_html}</td>\n'
            f'        <td class="mono">0x{start:08x}–0x{end:08x}</td>\n'
            f'        <td class="num">{size_str}</td>\n'
            f'        <td class="num">{pct:.2f}%</td>\n'
            f'        <td>{html.escape(desc_text)}</td>\n'
            f"      </tr>"
        )
    return "\n".join(rows)


def rel_href(from_out_path: str, to_out_path: str) -> str:
    """Compute a clean relative href between two paths inside site/dist/."""
    from_dir = posixpath.dirname(from_out_path) or "."
    rel = posixpath.relpath(to_out_path, from_dir)
    if rel == "index.html":
        return "./"
    if rel.endswith("/index.html"):
        return rel[: -len("index.html")]
    return rel


def root_prefix(out_path: str) -> str:
    depth = len(Path(out_path).parts) - 1
    return "../" * depth


def extract_right_toc(body_html: str) -> str:
    pattern = re.compile(r'<(h[23])\s+[^>]*id="([^"]+)"[^>]*>(.*?)</\1>', re.DOTALL)
    entries = []
    for tag, hid, inner in pattern.findall(body_html):
        text = re.sub(r"<[^>]+>", "", inner).strip()
        cls = ' class="sub"' if tag == "h3" else ""
        entries.append(f'        <li{cls}><a href="#{html.escape(hid)}">{text}</a></li>')
    if not entries:
        return "      <ul></ul>"
    return "      <ul>\n" + "\n".join(entries) + "\n      </ul>"


def build_sidebar(lang: str, current_rel: str, current_out: str) -> str:
    prefix = LANG_META[lang]["prefix"]
    parts = []
    for grp in NAV_GROUPS:
        if grp["group"]:
            parts.append(f'      <p class="g">{html.escape(grp["group"][lang])}</p>')
        parts.append("      <ul>")
        for item in grp["items"]:
            target_out = prefix + item["path"]
            href = rel_href(current_out, target_out)
            title = item[lang][0]
            on = ' class="on"' if item["path"] == current_rel else ""
            parts.append(f'        <li><a href="{href}"{on}>{html.escape(title)}</a></li>')
        parts.append("      </ul>")
    return "\n".join(parts)


def build_pager(flat_items: list, idx: int, lang: str, current_out: str) -> str:
    prefix = LANG_META[lang]["prefix"]
    meta = LANG_META[lang]
    links = []
    if idx > 0:
        prev_item = flat_items[idx - 1]
        href = rel_href(current_out, prefix + prev_item["path"])
        links.append(
            f'      <a class="prev" href="{href}"><small>← {meta["prev_label"]}</small>'
            f'<strong>{html.escape(prev_item[lang][0])}</strong></a>'
        )
    if idx + 1 < len(flat_items):
        next_item = flat_items[idx + 1]
        href = rel_href(current_out, prefix + next_item["path"])
        links.append(
            f'      <a class="next" href="{href}"><small>{meta["next_label"]} →</small>'
            f'<strong>{html.escape(next_item[lang][0])}</strong></a>'
        )
    if not links:
        return ""
    return '      <nav class="pager" aria-label="Page navigation">\n' + "\n".join(links) + "\n      </nav>"


def verify_links(dist_dir: Path) -> int:
    html_files = sorted(dist_dir.rglob("*.html"))
    ids_by_file = {}
    for f in html_files:
        txt = f.read_text(encoding="utf-8")
        ids_by_file[f.resolve()] = set(re.findall(r'\bid="([^"]+)"', txt))

    checked = 0
    for f in html_files:
        txt = f.read_text(encoding="utf-8")
        leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", txt)
        if leftover:
            raise RuntimeError(f"Unreplaced placeholders in {f.relative_to(dist_dir)}: {leftover}")

        for attr, ref in re.findall(r'(href|src)="([^"]+)"', txt):
            if ref.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            checked += 1
            if ref.startswith("#"):
                anchor = ref[1:]
                if anchor and anchor not in ids_by_file[f.resolve()]:
                    raise RuntimeError(f"Broken anchor {ref} in {f.relative_to(dist_dir)}")
                continue
            path_part, _, anchor = ref.partition("#")
            target = (f.parent / path_part).resolve()
            if target.is_dir():
                target = (target / "index.html").resolve()
            if not target.is_file():
                raise FileNotFoundError(
                    f"Broken local {attr}=\"{ref}\" in {f.relative_to(dist_dir)} (resolved: {target})"
                )
            if anchor and target.suffix == ".html":
                if anchor not in ids_by_file.get(target, set()):
                    raise RuntimeError(
                        f"Broken anchor {ref} in {f.relative_to(dist_dir)} (missing #{anchor} in {target.relative_to(dist_dir)})"
                    )
    return checked


def build() -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    validate_partitions(data)
    layout_tpl = LAYOUT_FILE.read_text(encoding="utf-8")
    git_sha = get_git_sha()

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    (DIST_DIR / "assets").mkdir(parents=True)
    (DIST_DIR / "data").mkdir(parents=True)

    for src in ASSETS_SRC.iterdir():
        if src.is_file():
            shutil.copy2(src, DIST_DIR / "assets" / src.name)
    if not SCREENSHOT_SRC.is_file():
        raise FileNotFoundError(f"Missing required screenshot: {SCREENSHOT_SRC}")
    shutil.copy2(SCREENSHOT_SRC, DIST_DIR / "assets" / SCREENSHOT_SRC.name)
    shutil.copy2(DATA_FILE, DIST_DIR / "data" / "partitions.json")

    flat_items = []
    group_by_path = {}
    for grp in NAV_GROUPS:
        for item in grp["items"]:
            flat_items.append(item)
            group_by_path[item["path"]] = grp["group"]

    built_count = 0
    for lang in ("en", "de"):
        meta = LANG_META[lang]
        alt_lang = meta["alt_code"]
        alt_prefix = LANG_META[alt_lang]["prefix"]

        for idx, item in enumerate(flat_items):
            rel_path = item["path"]
            out_rel = meta["prefix"] + rel_path
            src_file = PAGES_DIR / lang / rel_path
            if not src_file.is_file():
                raise FileNotFoundError(f"Missing page content file: {src_file}")

            root = root_prefix(out_rel)
            body = src_file.read_text(encoding="utf-8")
            body = body.replace("{{ROOT}}", root)
            if item.get("embed_partitions"):
                body = body.replace("{{PARTITION_ROWS}}", render_partition_rows(data, lang))
                extra_scripts = (
                    f'  <script id="partition-data" type="application/json">'
                    f'{json.dumps(data, separators=(",", ":"))}</script>'
                )
            else:
                extra_scripts = ""

            grp = group_by_path[rel_path]
            home_href = rel_href(out_rel, meta["prefix"] + "index.html")
            if grp:
                crumbs = (
                    f'      <div class="crumbs"><a href="{home_href}">{html.escape(flat_items[0][lang][0])}</a>'
                    f'<span class="sep">/</span><span>{html.escape(grp[lang])}</span></div>'
                )
            else:
                crumbs = ""

            title, desc = item[lang]
            alt_href = rel_href(out_rel, alt_prefix + rel_path)
            rendered = (
                layout_tpl.replace("{{LANG}}", lang)
                .replace("{{TITLE}}", html.escape(title))
                .replace("{{DESCRIPTION}}", html.escape(desc))
                .replace("{{ROOT}}", root)
                .replace("{{HOME_HREF}}", home_href)
                .replace("{{BRAND_SUB}}", html.escape(meta["brand_sub"]))
                .replace("{{ALT_LANG_HREF}}", alt_href)
                .replace("{{ALT_LANG_CODE}}", alt_lang)
                .replace("{{ALT_LANG_LABEL}}", meta["alt_label"])
                .replace("{{MENU_LABEL}}", meta["menu_label"])
                .replace("{{NAV_ARIA}}", meta["nav_aria"])
                .replace("{{TOC_TITLE}}", meta["toc_title"])
                .replace("{{SIDEBAR_HTML}}", build_sidebar(lang, rel_path, out_rel))
                .replace("{{CRUMBS_HTML}}", crumbs)
                .replace("{{BODY_HTML}}", body)
                .replace("{{PAGER_HTML}}", build_pager(flat_items, idx, lang, out_rel))
                .replace("{{RIGHT_TOC_HTML}}", extract_right_toc(body))
                .replace("{{EXTRA_SCRIPTS}}", extra_scripts)
                .replace("{{GIT_SHA}}", html.escape(git_sha))
            )

            out_file = DIST_DIR / out_rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(rendered, encoding="utf-8")
            built_count += 1

    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")
    checked_links = verify_links(DIST_DIR)
    print(
        f"Built site/dist successfully: {built_count} pages (EN + DE), "
        f"{len(data['partitions'])} partition entries (128 MiB verified), "
        f"{checked_links} local links/anchors verified, git={git_sha}"
    )


if __name__ == "__main__":
    build()
