#!/usr/bin/env python3
"""
H3C Magic RT3000 helper for a Linux host on the same LAN (e.g. 192.168.10.3).

Automates safe, repetitive steps over Telnet (192.168.10.1:23) and HTTP:
  - probe          : Read-only hardware, CDT machid, ART 5G FEM, NAND ID/ECC, and BOOTCONFIG probe.
                     Outputs exact H3C-NX30-Uboot v0.1.0, IPQ50XX-Uboot v0.2.0, and OpenWrt image names.
  - inspect-backup : Offline analysis of an existing MTD .7z backup (e.g. firmware/old_firmware.7z).
  - backup         : Stream mtd0..mtd15 from router to local machine via nc, verify MD5/sizes, optional .7z.
  - serve          : Extract .7z/.zip (optional), compute SHA-256, serve over HTTP on 192.168.10.3,
                     and print exact router-side wget/verify/flash commands.

Uses Python 3 standard library only (no pip dependencies).
"""

import argparse
import functools
import hashlib
import http.server
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

# Complete 21-entry NAND table from FlyFish-go/H3C-NX30-Uboot uboot-v0.1.0 CANDIDATES.json
# Key: (mfr_hex, chip_hex, ecc_bits) -> (model, folder_suffix, expected_oob)
NAND_CANDIDATES = {
    ("2c", "15", 8): ("MT29F1G01ABBFDWB-IT", "MT29F1G01ABBFDWB-IT-ECC8", 128),
    ("98", "d2", 8): ("TC58CYG0S3HRAIG", "TC58CYG0S3HRAIG-ECC8", 128),
    ("98", "db", 4): ("TC58CYG1S3HRAIJ", "TC58CYG1S3HRAIJ-ECC4", 64),
    ("a1", "a5", 4): ("FM25LS01", "FM25LS01-ECC4", 64),
    ("a1", "a5", 8): ("FM25LS01", "FM25LS01-ECC8", 128),
    ("c2", "92", 4): ("MX35UF1GE4AC", "MX35UF1GE4AC-ECC4", 64),
    ("c8", "11", 4): ("F50D1G41LB(2M)", "F50D1G41LB-2M-ECC4", 64),
    ("c8", "21", 4): ("GD5F1GQ5REYIH", "GD5F1GQ5REYIH-ECC4", 64),
    ("c8", "22", 4): ("GD5F2GQ5REYIH", "GD5F2GQ5REYIH-ECC4", 64),
    ("c8", "41", 4): ("GD5F1GQ5REYIG", "GD5F1GQ5REYIG-ECC4", 128),
    ("c8", "41", 8): ("GD5F1GQ5REYIG", "GD5F1GQ5REYIG-ECC8", 128),
    ("c8", "45", 4): ("GD5F4GQ6REYIGY", "GD5F4GQ6REYIGY-ECC4", 128),
    ("c8", "81", 8): ("GD5F1GM7REYIG", "GD5F1GM7REYIG-ECC8", 128),
    ("c8", "c1", 8): ("GD5F1GQ4RE9IG", "GD5F1GQ4RE9IG-ECC8", 128),
    ("c8", "c9", 4): ("GD5F1GQ4RE9IH", "GD5F1GQ4RE9IH-ECC4", 64),
    ("e5", "21", 4): ("DS35M1GA-IB", "DS35M1GA-IB-ECC4", 128),
    ("e5", "72", 4): ("DS35Q2GA", "DS35Q2GA-ECC4", 64),
    ("e5", "a4", 8): ("DS35M4GM", "DS35M4GM-ECC8", 128),
    ("ef", "ba", 4): ("W25N01GWZEIG", "W25N01GWZEIG-ECC4", 64),
    ("ef", "bc", 4): ("W25N01JW", "W25N01JW-ECC4", 64),
    ("ef", "bf", 4): ("W25N02JWZEIF", "W25N02JWZEIF-ECC4", 64),
}


class RawTelnet:
    """Minimal pure-Python Telnet client (works on Python 3.13+ where telnetlib was removed)."""

    IAC = 255
    DONT, DO, WONT, WILL, SB, SE = 254, 253, 252, 251, 250, 240

    def __init__(self, host: str, port: int = 23, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setblocking(False)
        self.buf = bytearray()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _strip_iac(self, data: bytes) -> bytes:
        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if b == self.IAC and i + 1 < n:
                cmd = data[i + 1]
                if cmd in (self.DO, self.DONT, self.WILL, self.WONT) and i + 2 < n:
                    opt = data[i + 2]
                    # Politely refuse all telnet options
                    if cmd == self.DO:
                        self.sock.sendall(bytes([self.IAC, self.WONT, opt]))
                    elif cmd == self.WILL:
                        self.sock.sendall(bytes([self.IAC, self.DONT, opt]))
                    i += 3
                    continue
                if cmd == self.SB:
                    end = data.find(bytes([self.IAC, self.SE]), i + 2)
                    i = (end + 2) if end != -1 else n
                    continue
                i += 2
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def read_until(self, markers, timeout: float = 10.0) -> str:
        if isinstance(markers, (bytes, str)):
            markers = [markers]
        bmarkers = [m.encode() if isinstance(m, str) else m for m in markers]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for m in bmarkers:
                idx = self.buf.find(m)
                if idx != -1:
                    end = idx + len(m)
                    chunk = bytes(self.buf[:end])
                    del self.buf[:end]
                    return chunk.decode("utf-8", errors="replace")
            wait = max(0.05, min(0.5, deadline - time.monotonic()))
            r, _, _ = select.select([self.sock], [], [], wait)
            if r:
                data = self.sock.recv(4096)
                if not data:
                    break
                self.buf.extend(self._strip_iac(data))
        chunk = bytes(self.buf)
        self.buf.clear()
        return chunk.decode("utf-8", errors="replace")

    def write(self, text: str) -> None:
        self.sock.sendall(text.encode("utf-8"))

    def enter_debugshell(self) -> None:
        banner = self.read_until([">", "#", "login:"], timeout=8.0)
        if "#" in banner:
            return
        if ">" in banner:
            self.write("debugshell\n")
            out = self.read_until(["#", ">"], timeout=8.0)
            if "#" not in out:
                raise RuntimeError(f"Failed to enter debugshell. Output:\n{out}")
            return
        raise RuntimeError(f"Unexpected telnet prompt:\n{banner}")

    def run(self, cmd: str, timeout: float = 20.0) -> str:
        marker = f"__RT3000_END_{int(time.time() * 1000) % 100000}__"
        self.write(f"{cmd}; echo {marker}\n")
        raw = self.read_until(marker, timeout=timeout)
        # Drain trailing prompt
        self.read_until("#", timeout=1.5)
        lines = raw.replace("\r", "").splitlines()
        # Strip echoed command and marker line
        if lines and marker in lines[-1]:
            lines = lines[:-1]
        if lines and cmd.split()[0] in lines[0]:
            lines = lines[1:]
        return "\n".join(lines).strip()


def parse_cdt_machid(cdt: bytes):
    if len(cdt) < 0x1F or cdt[:4] != b"CDT\x00":
        return None
    off = cdt[0x0E] | (cdt[0x0F] << 8)
    size = cdt[0x10] | (cdt[0x11] << 8)
    if size != 5 or off + size > len(cdt) or cdt[off] != 2:
        return None
    machid = (cdt[off + 1] << 24) | (cdt[off + 2] << 16) | (cdt[off + 3] << 8) | cdt[off + 4]
    return machid, list(cdt[off : off + 5])


def parse_art_5g_fem(art_slice: bytes):
    """art_slice must start at ART offset 0x26800 and be at least 0x46 (70) bytes."""
    if len(art_slice) < 0x46:
        return None, "slice too short"
    hdr = art_slice[0:4]
    b3a = art_slice[0x3A]
    b45 = art_slice[0x45]
    detail = f"hdr={hdr.hex()} +0x3a=0x{b3a:02x} +0x45=0x{b45:02x}"
    if hdr == b"\x01\x00\x04\x04" and b3a == 0x50 and b45 == 0x01:
        return "HW-B", f"no external 5 GHz FEM ({detail})"
    if hdr == b"\x01\x00\x04\x04" and b3a == 0x60 and b45 == 0x09:
        return "HW-D", f"external 5 GHz FEM present ({detail})"
    return None, f"unrecognized 5 GHz ART block ({detail})"


def parse_bootconfig(bc: bytes):
    """Parse Qualcomm SMEM/BOOTCONFIG partition selectors (primary & backup)."""
    if len(bc) < 12:
        return {}
    magic, num_alt, num_parts = struct.unpack_from("<III", bc, 0)
    if magic != 0xA3A2A1A0 or num_parts > 32:
        return {}
    result = {}
    off = 12
    for _ in range(num_parts):
        if off + 20 > len(bc):
            break
        name = bc[off : off + 16].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        prim = struct.unpack_from("<I", bc, off + 16)[0]
        result[name] = prim
        off += 20
    return result


def classify_hw_group(machid, art_group):
    if machid == 0x08040001:
        return "HW-A", "IPQ5018 + QCA8337 (MP03.1)"
    if machid == 0x08040101:
        return "HW-C", "IPQ5018 + RTL8367S (MP03.1-C2)"
    if machid == 0x08040000:
        if art_group in ("HW-B", "HW-D"):
            desc = "IPQ5000 + QCA8337 (MP02.1, " + ("no 5G FEM)" if art_group == "HW-B" else "with 5G FEM)")
            return art_group, desc
        return None, "MP02.1 (0x08040000) — could not distinguish HW-B vs HW-D from ART"
    return None, f"Unknown machid {hex(machid) if machid is not None else 'None'}"


def print_recommendations(hw_group, nand_mfr=None, nand_chip=None, ecc=None, oob=None):
    print("\n=== Recommended Images ===")
    if not hw_group:
        print("  [!] Hardware group could NOT be determined unambiguously. Do NOT flash until confirmed.")
        return

    group_lower = hw_group.lower()
    print(f"  Detected Hardware Group : {hw_group}")
    print(f"  OpenWrt 21.02.7 (r14)   : H3C-Magic-r14-{hw_group}-nand-factory.ubi  (first install via rescue page)")
    print(f"                            H3C-Magic-r14-{hw_group}-sysupgrade.bin    (later LuCI upgrades)")
    print(f"  IPQ50XX-Uboot v0.2.0    : h3c-{group_lower}.mbn  (auto-detects NAND/ECC at boot)")

    if nand_mfr and nand_chip and ecc:
        key = (nand_mfr.lower(), nand_chip.lower(), int(ecc))
        match = NAND_CANDIDATES.get(key)
        if match:
            model, folder_suffix, expected_oob = match
            print(f"  H3C-NX30-Uboot v0.1.0   : uboot-v0.1.0-{hw_group}.zip -> {hw_group}/{folder_suffix}/appsbl.mbn")
            if oob and int(oob) != expected_oob:
                print(f"  [!] WARNING: sysfs OOB={oob} differs from candidate expected OOB={expected_oob}! Double-check before flashing.")
        else:
            print(f"  [!] H3C-NX30-Uboot v0.1.0: No exact match for ID {nand_mfr}:{nand_chip} with ECC{ecc}!")
    else:
        print("  H3C-NX30-Uboot v0.1.0   : Run `probe` on the live router to read NAND ID and ECC from dmesg/sysfs.")


def cmd_probe(args):
    print(f"[*] Connecting to {args.host}:{args.port} via Telnet (read-only)...")
    tn = RawTelnet(args.host, args.port, timeout=10.0)
    try:
        tn.enter_debugshell()
        print("[*] Entered debugshell.")

        uname = tn.run("uname -a")
        dt_model = tn.run("cat /proc/device-tree/model 2>/dev/null; echo")
        mtd_table = tn.run("cat /proc/mtd")
        dmesg_nand = tn.run('dmesg | grep -iE "nand:|spi|switch|qca|rtl"')
        sys_mtd0 = tn.run(
            'for f in name writesize erasesize oobsize ecc_strength; do '
            'echo "$f=$(cat /sys/class/mtd/mtd0/$f 2>/dev/null)"; done'
        )
        bad_appsbl = tn.run(
            'echo "mtd11_bad=$(cat /sys/class/mtd/mtd11/bad_blocks 2>/dev/null) '
            'mtd12_bad=$(cat /sys/class/mtd/mtd12/bad_blocks 2>/dev/null)"'
        )
        tools_avail = tn.run("for t in mtd flash_erase nandwrite ubiformat wget nc md5sum sha256sum; do which $t 2>/dev/null; done")

        # Hex dumps for CDT (mtd8), ART 5G block (mtd13 @ 0x26800), and BOOTCONFIG (mtd2)
        cdt_hex = tn.run("dd if=/dev/mtd8 bs=64 count=1 2>/dev/null | hexdump -ve '1/1 \"%02x\"'")
        art_hex = tn.run("dd if=/dev/mtd13 bs=1 skip=$((0x26800)) count=80 2>/dev/null | hexdump -ve '1/1 \"%02x\"'")
        bc_hex = tn.run("dd if=/dev/mtd2 bs=256 count=1 2>/dev/null | hexdump -ve '1/1 \"%02x\"'")
    finally:
        tn.close()

    print("\n=== System Info ===")
    print(f"  uname        : {uname}")
    print(f"  DT model     : {dt_model.strip()}")
    print(f"  Tools found  : {', '.join(tools_avail.splitlines())}")
    print(f"  APPSBL bad   : {bad_appsbl}")

    print("\n=== MTD Partitions ===")
    print(mtd_table)

    # Parse CDT & ART
    cdt_bytes = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", cdt_hex)) if cdt_hex else b""
    art_bytes = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", art_hex)) if art_hex else b""
    bc_bytes = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", bc_hex)) if bc_hex else b""

    cdt_info = parse_cdt_machid(cdt_bytes)
    machid = cdt_info[0] if cdt_info else None
    art_group, art_detail = parse_art_5g_fem(art_bytes)
    hw_group, hw_desc = classify_hw_group(machid, art_group)

    print("\n=== Hardware Classification ===")
    if cdt_info:
        print(f"  0:CDT machid : 0x{machid:08x} (tuple {cdt_info[1]})")
    else:
        print("  0:CDT machid : [could not parse]")
    print(f"  0:ART 5G FEM : {art_detail}")
    print(f"  Board Group  : {hw_group or 'UNKNOWN'} ({hw_desc})")

    # Parse BOOTCONFIG
    bc = parse_bootconfig(bc_bytes)
    if bc:
        print("\n=== BOOTCONFIG Selectors (0 = primary/low offset, 1 = backup/high offset) ===")
        for part in ("0:APPSBL", "rootfs"):
            if part in bc:
                print(f"  {part:12s} : {bc[part]}")

    # Parse NAND from dmesg + sysfs
    sys_kv = dict(line.split("=", 1) for line in sys_mtd0.splitlines() if "=" in line)
    m_id = re.search(r"Manufacturer ID:\s*0x([0-9a-fA-F]+),\s*Chip ID:\s*0x([0-9a-fA-F]+)", dmesg_nand)
    m_geo = re.search(r"nand:\s*(\d+\s*MiB.*?OOB size:\s*\d+)", dmesg_nand)
    m_mod = re.search(r"nand:\s*([^\n]*SPI NAND[^\n]*)", dmesg_nand)

    nand_mfr = f"{int(m_id.group(1), 16):02x}" if m_id else None
    nand_chip = f"{int(m_id.group(2), 16):02x}" if m_id else None
    ecc = sys_kv.get("ecc_strength", "").strip()
    oob = sys_kv.get("oobsize", "").strip()

    print("\n=== NAND Flash ===")
    print(f"  ID (Mfr:Chip): {f'{nand_mfr}:{nand_chip}' if nand_mfr else 'not found in dmesg'}")
    if m_mod:
        print(f"  Model line   : {m_mod.group(1).strip()}")
    if m_geo:
        print(f"  Geometry     : {m_geo.group(1).strip()}")
    print(
        f"  sysfs mtd0   : writesize={sys_kv.get('writesize')} erasesize={sys_kv.get('erasesize')} "
        f"oobsize={oob} ecc_strength={ecc}"
    )

    print_recommendations(hw_group, nand_mfr, nand_chip, ecc or None, oob or None)


def cmd_inspect_backup(args):
    archive = args.archive
    if not os.path.isfile(archive):
        sys.exit(f"File not found: {archive}")

    def read_member(name):
        try:
            return subprocess.check_output(["7z", "e", "-so", archive, name], stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return b""

    cdt = read_member("mtd8_0_CDT.bin")
    art = read_member("mtd13_0_ART.bin")
    bc0 = read_member("mtd2_0_BOOTCONFIG.bin")
    bc1 = read_member("mtd3_0_BOOTCONFIG1.bin")
    env = read_member("mtd10_0_APPSBLENV.bin")

    cdt_info = parse_cdt_machid(cdt)
    machid = cdt_info[0] if cdt_info else None
    art_group, art_detail = parse_art_5g_fem(art[0x26800 : 0x26800 + 80]) if len(art) >= 0x26850 else (None, "ART missing")
    hw_group, hw_desc = classify_hw_group(machid, art_group)

    print(f"=== Offline Backup Inspection: {archive} ===")
    if cdt_info:
        print(f"  0:CDT machid : 0x{machid:08x} (tuple {cdt_info[1]})")
    print(f"  0:ART 5G FEM : {art_detail}")
    print(f"  Board Group  : {hw_group or 'UNKNOWN'} ({hw_desc})")

    p0 = parse_bootconfig(bc0)
    p1 = parse_bootconfig(bc1)
    if p0 or p1:
        print("\n=== BOOTCONFIG ===")
        for part in ("0:APPSBL", "rootfs"):
            print(f"  {part:12s} : BOOTCONFIG={p0.get(part)}  BOOTCONFIG1={p1.get(part)}")

    if len(env) > 4:
        print("\n=== Non-sensitive APPSBLENV ===")
        for raw in env[4:8192].split(b"\x00"):
            if not raw or raw.startswith(b"\xff"):
                break
            s = raw.decode("ascii", errors="ignore")
            if any(k in s.lower() for k in ("mac", "ethaddr", "manucode", "sn", "serial", "cmei", "pass", "key")):
                continue
            print(f"  {s}")

    print_recommendations(hw_group)


def cmd_backup(args):
    os.makedirs(args.out, exist_ok=True)
    print(f"[*] Connecting to {args.host}:{args.port} to back up MTD partitions to {args.out}...")
    tn = RawTelnet(args.host, args.port, timeout=10.0)
    try:
        tn.enter_debugshell()
        mtd_txt = tn.run("cat /proc/mtd")
        parts = []
        for line in mtd_txt.splitlines():
            m = re.match(r'^(mtd\d+):\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+"([^"]+)"', line.strip())
            if m:
                dev, size_hex, name = m.group(1), m.group(2), m.group(3)
                idx = int(dev[3:])
                if idx <= args.max_mtd:
                    parts.append((dev, int(size_hex, 16), name))

        if not parts:
            sys.exit("Could not parse /proc/mtd.")

        md5_lines = []
        for dev, expected_size, name in parts:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
            filename = f"{dev}_{safe_name}.bin"
            dest = os.path.join(args.out, filename)
            print(f"[*] Streaming /dev/{dev} ({name}, {expected_size:,} bytes) -> {filename} ...")

            # Bind an ephemeral TCP listener on local_ip
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((args.local_ip, 0))
            srv.listen(1)
            srv.settimeout(60.0)
            listen_port = srv.getsockname()[1]

            received = {"bytes": 0, "md5": None, "err": None}

            def receiver():
                try:
                    conn, _ = srv.accept()
                    conn.settimeout(60.0)
                    h = hashlib.md5()
                    total = 0
                    with open(dest, "wb") as fp:
                        while True:
                            chunk = conn.recv(65536)
                            if not chunk:
                                break
                            fp.write(chunk)
                            h.update(chunk)
                            total += len(chunk)
                    conn.close()
                    received["bytes"] = total
                    received["md5"] = h.hexdigest()
                except Exception as exc:
                    received["err"] = exc
                finally:
                    srv.close()

            t = threading.Thread(target=receiver, daemon=True)
            t.start()

            tn.run(f"dd if=/dev/{dev} bs=4096 2>/dev/null | nc {args.local_ip} {listen_port}", timeout=180.0)
            t.join(timeout=30.0)

            if received["err"]:
                raise RuntimeError(f"Failed receiving {dev}: {received['err']}")
            if received["bytes"] != expected_size:
                raise RuntimeError(f"Size mismatch for {dev}: got {received['bytes']}, expected {expected_size}")

            remote_md5 = tn.run(f"dd if=/dev/{dev} bs=4096 2>/dev/null | md5sum", timeout=120.0).split()[0]
            if remote_md5 != received["md5"]:
                raise RuntimeError(f"MD5 mismatch for {dev}: local={received['md5']} remote={remote_md5}")

            print(f"    OK  {received['bytes']:,} bytes  md5={received['md5']}")
            md5_lines.append(f"{received['md5']}  {filename}")

        md5_path = os.path.join(args.out, "md5sums.txt")
        with open(md5_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(md5_lines) + "\n")
        print(f"[*] Saved checksums to {md5_path}")
    finally:
        tn.close()

    if args.sevenzip:
        archive_path = os.path.abspath(args.out).rstrip("/") + ".7z"
        print(f"[*] Compressing backup to {archive_path} ...")
        subprocess.check_call(["7z", "a", "-t7z", "-mx=9", archive_path, "."], cwd=args.out)
        print(f"[*] Done: {archive_path}")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_serve(args):
    src = os.path.abspath(args.path)
    if not os.path.exists(src):
        sys.exit(f"Path not found: {src}")

    tmpdir = None
    try:
        if os.path.isdir(src):
            serve_dir = src
        else:
            tmpdir = tempfile.mkdtemp(prefix="rt3000_serve_")
            lower = src.lower()
            if lower.endswith(".7z"):
                print(f"[*] Extracting {src} into temporary staging dir {tmpdir} ...")
                subprocess.check_call(["7z", "x", "-y", f"-o{tmpdir}", src], stdout=subprocess.DEVNULL)
            elif lower.endswith(".zip"):
                print(f"[*] Extracting {src} into temporary staging dir {tmpdir} ...")
                with zipfile.ZipFile(src) as zf:
                    zf.extractall(tmpdir)
            else:
                shutil.copy2(src, os.path.join(tmpdir, os.path.basename(src)))
            serve_dir = tmpdir

        files = []
        for root, _, names in os.walk(serve_dir):
            for name in sorted(names):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, serve_dir).replace(os.sep, "/")
                files.append((rel, os.path.getsize(full), sha256_file(full)))

        print(f"\n=== Serving {len(files)} file(s) from http://{args.local_ip}:{args.port}/ ===")
        uboot_candidates = []
        for rel, size, digest in files:
            print(f"  {digest}  {size:>10d}  {rel}")
            if rel.endswith(".mbn"):
                uboot_candidates.append((rel, size, digest))

        if uboot_candidates:
            rel, size, digest = uboot_candidates[0]
            print("\n=== Example Router Commands (run in debugshell on 192.168.10.1) ===")
            print(f"# (Adjust the URL path if you have multiple .mbn files above)")
            print(f"wget http://{args.local_ip}:{args.port}/{rel} -O /tmp/appsbl.mbn")
            print(f"sha256sum /tmp/appsbl.mbn  # MUST equal: {digest}")
            print("cat /proc/mtd | grep -E 'mtd11|mtd12'")
            print("cat /sys/class/mtd/mtd11/bad_blocks /sys/class/mtd/mtd12/bad_blocks  # MUST be 0 0")
            print("# Write backup APPSBL slot (mtd12) FIRST, then verify readback:")
            print(f"mtd write /tmp/appsbl.mbn /dev/mtd12 && dd if=/dev/mtd12 bs=1 count={size} 2>/dev/null | sha256sum")
            print("# ONLY if SHA-256 above matches, write active APPSBL slot (mtd11) and verify:")
            print(f"mtd write /tmp/appsbl.mbn /dev/mtd11 && dd if=/dev/mtd11 bs=1 count={size} 2>/dev/null | sha256sum")
            print("# If both match, run: sync && reboot")

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=serve_dir)
        httpd = http.server.ThreadingHTTPServer((args.local_ip, args.port), handler)
        print(f"\n[*] HTTP server listening on http://{args.local_ip}:{args.port}/ (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[*] Server stopped.")
        finally:
            httpd.server_close()
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="H3C Magic RT3000 Linux helper (run on 192.168.10.3)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="Read-only Telnet probe of board group, NAND ID, ECC, and BOOTCONFIG")
    p_probe.add_argument("--host", default="192.168.10.1", help="Router IP (default: 192.168.10.1)")
    p_probe.add_argument("--port", type=int, default=23, help="Telnet port (default: 23)")
    p_probe.set_defaults(func=cmd_probe)

    p_insp = sub.add_parser("inspect-backup", help="Offline inspection of a .7z MTD backup archive")
    p_insp.add_argument("archive", help="Path to .7z archive (e.g. firmware/old_firmware.7z)")
    p_insp.set_defaults(func=cmd_inspect_backup)

    p_bak = sub.add_parser("backup", help="Stream mtd0..mtd15 from router over LAN and verify MD5")
    p_bak.add_argument("--host", default="192.168.10.1", help="Router IP (default: 192.168.10.1)")
    p_bak.add_argument("--port", type=int, default=23, help="Telnet port (default: 23)")
    p_bak.add_argument("--local-ip", default="192.168.10.3", help="This Linux machine's LAN IP (default: 192.168.10.3)")
    p_bak.add_argument("--out", default="./backup_rt3000", help="Output directory")
    p_bak.add_argument("--max-mtd", type=int, default=15, help="Highest mtd index to back up (default: 15)")
    p_bak.add_argument("--7z", dest="sevenzip", action="store_true", help="Also compress into a .7z archive")
    p_bak.set_defaults(func=cmd_backup)

    p_srv = sub.add_parser("serve", help="Stage a file, directory, .zip, or .7z and serve over HTTP")
    p_srv.add_argument("path", help="File, directory, .7z, or .zip to stage and serve")
    p_srv.add_argument("--local-ip", default="192.168.10.3", help="Bind IP on this Linux machine (default: 192.168.10.3)")
    p_srv.add_argument("--port", type=int, default=8899, help="HTTP port (default: 8899)")
    p_srv.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
