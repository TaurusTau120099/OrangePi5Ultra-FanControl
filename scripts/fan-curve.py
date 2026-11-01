#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fan-curve.py —— 安全地修改 Orange Pi (RK3588) 的 pwm-fan 温控曲线

设计目标：干掉"改了半天其实没生效 / 改错数值还不知道"这两类事故。

用法
----
先看当前曲线（只读，不做任何修改）：
    sudo ./fan-curve.py --show

试算新曲线（只打印，不写盘）：
    sudo ./fan-curve.py --set "0:0,65:160,68:180,71:200,74:220,77:240,80:255"

确认无误后真正写入：
    sudo ./fan-curve.py --set "0:0,65:160,68:180,71:200,74:220,77:240,80:255" --apply

顺手改 PWM 频率（可选）：
    sudo ./fan-curve.py --set "..." --freq 100 --apply

回滚：
    sudo ./fan-curve.py --restore            # 恢复最近一次备份
    ls /root/fan-curve-backup/               # 所有备份

`--set` 的格式是 `温度°C:PWM` 的逗号分隔列表：
    * 温度单位是摄氏度，可以是小数（0.5 会四舍五入到毫摄氏度）
    * PWM 是 0..255 的整数
    * 内部会自动排序，并保证 cooling-levels 的下标与 temp-trips 的 state 一一对应
      （这两者错位是手改 DTB 最常见的坑）

为什么默认改 /boot/dtb/rockchip/... 而不是 /boot/dtb-<版本>/...
-------------------------------------------------------------
u-boot 的 boot.scr 里写的是：
    load ${devtype} ${devnum} ${fdt_addr_r} ${prefix}dtb/${fdtfile}
其中 fdtfile 来自 /boot/orangepiEnv.txt。所以真正被加载的是
    <启动分区>/dtb/rockchip/<fdtfile>
当 /boot 是 vfat 时，"dtb" 是实体目录（vfat 不支持符号链接），
它和 dtb-<版本>/ 是两份互相独立的拷贝，改后者不会生效。
当 /boot 是 ext4 时，"dtb" 通常是指向 dtb-<版本>/ 的符号链接，改哪边都一样。
统一改 /boot/dtb/... 在两种情况下都正确。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_DTB = "/boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb"
BACKUP_DIR = "/root/fan-curve-backup"
MAX_PWM = 255


# ---------------------------------------------------------------- 基础工具

def die(msg):
    print("错误: " + msg, file=sys.stderr)
    sys.exit(1)


def run(cmd, **kw):
    """跑一条命令，失败就抛异常。"""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    if p.returncode != 0:
        raise RuntimeError(
            "命令失败 (%d): %s\n%s" % (p.returncode, " ".join(cmd),
                                     p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


def need_root():
    if os.geteuid() != 0:
        die("需要 root 权限，请用 sudo 运行")


# ---------------------------------------------------------------- 解析曲线

def parse_curve(spec):
    """'0:0,65:160' -> [(0, 0), (65, 160)]，并按温度排序。"""
    pts = []
    for item in spec.replace(" ", "").split(","):
        if not item:
            continue
        if ":" not in item:
            die("--set 格式错误，应为 温度:PWM，例如 65:160（出错片段: %r）" % item)
        t_s, p_s = item.split(":", 1)
        try:
            temp = float(t_s)
            pwm = int(p_s)
        except ValueError:
            die("无法解析 %r，温度和 PWM 都必须是数字" % item)
        if not 0 <= pwm <= MAX_PWM:
            die("PWM %d 超出范围 0..%d" % (pwm, MAX_PWM))
        if not -40 <= temp <= 200:
            die("温度 %.3f 明显不合理（应在 -40..200 °C）" % temp)
        pts.append((temp, pwm))

    if len(pts) < 2:
        die("至少需要 2 个温度点")

    pts.sort(key=lambda x: x[0])
    seen = set()
    for t, _ in pts:
        if t in seen:
            die("温度 %.3f 重复出现" % t)
        seen.add(t)
    return pts


def build_props(pts):
    """把 [(温度, PWM)] 展开成两个 DTB 属性。"""
    levels = [p for _, p in pts]
    trips = [(int(round(t * 1000)), i) for i, (t, _) in enumerate(pts)]

    lv = " ".join("0x%02x" % v for v in levels)
    tp = " ".join("0x%08x 0x%02x" % (mc, st) for mc, st in trips)
    return lv, tp, levels, trips


# ---------------------------------------------------------------- DTB 读写

def dtb_to_dts(dtb, dts):
    run(["dtc", "-I", "dtb", "-O", "dts", dtb, "-o", dts])


def dts_to_dtb(dts, dtb):
    run(["dtc", "-I", "dts", "-O", "dtb", dts, "-o", dtb])


def find_node_span(text, node):
    """
    在 dts 文本里找到 `node { ... };` 的字符区间。
    用花括号计数，避免被嵌套节点骗到。
    """
    m = re.search(r"(?m)^[ \t]*" + re.escape(node) + r"[ \t]*\{", text)
    if not m:
        return None
    start = m.start()
    i = text.index("{", m.start())
    depth = 0
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = text.find(";", i)
                if end == -1:
                    return None
                return start, end + 1
        i += 1
    return None


PROP_RE = {
    "cooling-levels": r"cooling-levels\s*=\s*<[^>]*>\s*;",
    "rockchip,temp-trips": r"rockchip,temp-trips\s*=\s*<[^>]*>\s*;",
    "pwms": r"pwms\s*=\s*<[^>]*>\s*;",
}


def read_current(spans_text):
    """从 pwm-fan 节点文本里取出当前值。"""
    out = {}
    for key, pat in PROP_RE.items():
        m = re.search(pat, spans_text)
        if m:
            nums = re.findall(r"0x[0-9a-fA-F]+|\b\d+\b", m.group(0))
            out[key] = [int(x, 16) if x.lower().startswith("0x") else int(x)
                        for x in nums]
    return out


def fmt_levels(levels):
    return " ".join("0x%02x" % v for v in levels)


def fmt_trips(trips):
    return " ".join("0x%08x 0x%02x" % (mc, st) for mc, st in trips)


def flatten(pairs):
    """[(a, b), (c, d)] -> [a, b, c, d]"""
    out = []
    for pair in pairs:
        out.extend(pair)
    return out


def fmt_curve(levels, trips):
    """把两个属性还原成人看得懂的表格。"""
    lines = ["  温度(°C)   level  PWM   占空比", "  " + "-" * 34]
    pairs = [(trips[i], trips[i + 1]) for i in range(0, len(trips) - 1, 2)]
    for mc, st in pairs:
        pwm = levels[st] if 0 <= st < len(levels) else None
        pct = ("%.0f%%" % (pwm * 100.0 / MAX_PWM)) if pwm is not None else "越界!"
        lines.append("  %8.3f   %5d  %s  %s" % (mc / 1000.0, st,
                                               ("%3d" % pwm) if pwm is not None else "  ?",
                                               pct))
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程

def load_node_text(dtb):
    """把 dtb 解到临时文件，返回 (临时目录, 完整dts文本, pwm-fan节点文本, dts路径)。"""
    tmpdir = tempfile.mkdtemp(prefix="fancurve-")
    dts = os.path.join(tmpdir, "tree.dts")
    dtb_to_dts(dtb, dts)
    text = open(dts, "r", encoding="utf-8", errors="replace").read()
    span = find_node_span(text, "pwm-fan")
    if not span:
        die("在 DTB 里找不到 pwm-fan 节点，这台设备的驱动模型可能不一样")
    node_text = text[span[0]:span[1]]
    return tmpdir, text, node_text, dts


def cmd_show(dtb):
    tmpdir, text, node_text, dts = load_node_text(dtb)
    try:
        cur = read_current(node_text)
        print("DTB: %s" % dtb)
        if "pwms" in cur and len(cur["pwms"]) >= 4:
            period = cur["pwms"][2]
            print("PWM 周期: %d ns  =>  %.2f Hz" %
                  (period, 1e9 / period if period else 0))
        if "cooling-levels" not in cur or "rockchip,temp-trips" not in cur:
            die("这个 pwm-fan 节点缺少 cooling-levels 或 rockchip,temp-trips")
        print("冷却级别: %s" % fmt_levels(cur["cooling-levels"]))
        print("温度触发: %s" % fmt_trips(
            list(zip(cur["rockchip,temp-trips"][0::2],
                     cur["rockchip,temp-trips"][1::2]))))
        print()
        print("当前生效曲线：")
        print(fmt_curve(cur["cooling-levels"],
                        flatten(zip(cur["rockchip,temp-trips"][0::2],
                                    cur["rockchip,temp-trips"][1::2]))))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def cmd_restore(dtb, stamp=None):
    need_root()
    if not os.path.isdir(BACKUP_DIR):
        die("没有找到备份目录 %s" % BACKUP_DIR)
    base = os.path.basename(dtb)
    cands = sorted((os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
                    if f.startswith(base + ".") and f.endswith(".dtb")),
                   key=os.path.getmtime, reverse=True)
    if not cands:
        die("备份目录里没有 %s 的备份" % base)
    if stamp:
        cands = [c for c in cands if stamp in c]
        if not cands:
            die("没有找到包含 %r 的备份" % stamp)
    src = cands[0]
    print("将从备份恢复: %s" % src)
    print("  创建时间: %s" % time.ctime(os.path.getmtime(src)))

    # 先备份"现在"的版本，避免恢复本身无法回退
    ts = time.strftime("%Y%m%d-%H%M%S")
    cur_bak = os.path.join(BACKUP_DIR, "%s.%s.before-restore.dtb" % (base, ts))
    shutil.copy2(dtb, cur_bak)
    print("已把当前版本另存 -> %s" % cur_bak)

    target_dir = os.path.dirname(dtb)
    tmp_target = os.path.join(target_dir, ".fan-curve-restore.tmp")
    shutil.copyfile(src, tmp_target)
    os.chmod(tmp_target, 0o755)
    os.replace(tmp_target, dtb)
    os.sync()
    print("已恢复 %s" % dtb)
    print("重启后生效: sudo reboot")


def cmd_apply(dtb, pts, freq):
    need_root()
    lv_str, tp_str, levels, trips = build_props(pts)

    tmpdir, text, node_text, dts = load_node_text(dtb)
    try:
        cur = read_current(node_text)

        # ---- 1. 现值
        print("=" * 60)
        print("当前曲线：")
        print(fmt_curve(cur["cooling-levels"],
                        flatten(zip(cur["rockchip,temp-trips"][0::2],
                                    cur["rockchip,temp-trips"][1::2]))))
        print()
        print("目标曲线：")
        print(fmt_curve(levels, flatten(trips)))

        # ---- 2. 替换，并断言每处只命中一次
        new_node = node_text
        old_cl = re.search(PROP_RE["cooling-levels"], new_node)
        old_tt = re.search(PROP_RE["rockchip,temp-trips"], new_node)
        if not old_cl or not old_tt:
            die("pwm-fan 节点里没找到目标属性，放弃（未做任何修改）")

        new_node = new_node[:old_cl.start()] + \
            "cooling-levels = <%s>;" % lv_str + new_node[old_cl.end():]
        # 重新定位，因为上一步已经改了长度
        old_tt = re.search(PROP_RE["rockchip,temp-trips"], new_node)
        new_node = new_node[:old_tt.start()] + \
            "rockchip,temp-trips = <%s>;" % tp_str + new_node[old_tt.end():]

        # ---- 3. 频率（可选）
        if freq:
            period = int(round(1e9 / float(freq)))
            m = re.search(PROP_RE["pwms"], new_node)
            if not m:
                die("没找到 pwms 属性")
            nums = re.findall(r"0x[0-9a-fA-F]+|\b\d+\b", m.group(0))
            vals = [int(x, 16) if x.lower().startswith("0x") else int(x) for x in nums]
            if len(vals) != 4:
                die("pwms 属性格式不是预期的 4 个数字")
            vals[2] = period
            repl = "pwms = <%s>;" % " ".join("0x%x" % v for v in vals)
            new_node = new_node[:m.start()] + repl + new_node[m.end():]
            print("\nPWM 周期改为 %d ns (%.2f Hz)" % (period, freq))

        # ---- 4. 写回整份 dts
        span = find_node_span(text, "pwm-fan")
        new_text = text[:span[0]] + new_node + text[span[1]:]
        open(dts, "w", encoding="utf-8").write(new_text)

        # ---- 5. 编译到临时文件，再解回来校验
        new_dtb = os.path.join(tmpdir, "new.dtb")
        dts_to_dtb(dts, new_dtb)
        check_dts = os.path.join(tmpdir, "check.dts")
        dtb_to_dts(new_dtb, check_dts)
        check_text = open(check_dts, "r", encoding="utf-8", errors="replace").read()
        span2 = find_node_span(check_text, "pwm-fan")
        got = read_current(check_text[span2[0]:span2[1]])

        if got.get("cooling-levels") != levels:
            die("校验失败：回读的 cooling-levels 与预期不符\n  预期 %s\n  实际 %s"
                % (levels, got.get("cooling-levels")))
        want_trips = sum([[mc, st] for mc, st in trips], [])
        if got.get("rockchip,temp-trips") != want_trips:
            die("校验失败：回读的 temp-trips 与预期不符\n  预期 %s\n  实际 %s"
                % (want_trips, got.get("rockchip,temp-trips")))
        print("\n编译 + 回读校验通过。")

        # ---- 6. 备份后原子替换
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = os.path.join(BACKUP_DIR, "%s.%s.dtb" %
                           (os.path.basename(dtb), stamp))
        shutil.copy2(dtb, bak)
        print("已备份原文件 -> %s" % bak)

        target_dir = os.path.dirname(dtb)
        tmp_target = os.path.join(target_dir, ".fan-curve.tmp")
        shutil.copyfile(new_dtb, tmp_target)
        os.chmod(tmp_target, 0o755)
        os.replace(tmp_target, dtb)
        os.sync()
        print("已写入 %s" % dtb)

        # ---- 7. 报告
        print("\n完成。重启后生效：sudo reboot")
        print("重启后可用下面的命令确认真的生效了（读的是运行中的 DTB）：")
        print("  od -A n -t x1 /proc/device-tree/pwm-fan/rockchip,temp-trips")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(
        description="安全修改 Orange Pi RK3588 的 pwm-fan 温控曲线",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dtb", default=DEFAULT_DTB,
                    help="DTB 路径（默认 %(default)s）")
    ap.add_argument("--set", dest="curve",
                    help='曲线，格式 "温度:PWM,温度:PWM,..."，例如 "0:0,65:160,80:255"')
    ap.add_argument("--freq", type=float,
                    help="PWM 频率（Hz），不改就留空")
    ap.add_argument("--show", action="store_true", help="只显示当前曲线")
    ap.add_argument("--restore", nargs="?", const="", default=None, metavar="时间戳片段",
                    help="从最近一次备份恢复；可选用时间戳片段挑选特定备份")
    ap.add_argument("--apply", action="store_true",
                    help="真正写入（不加则只做试算，不动文件）")
    args = ap.parse_args()

    if shutil.which("dtc") is None:
        die("找不到 dtc，请先 apt install device-tree-compiler")

    if args.show:
        if not os.path.exists(args.dtb):
            die("DTB 不存在: %s" % args.dtb)
        cmd_show(args.dtb)
        return

    if args.restore is not None:
        cmd_restore(args.dtb, args.restore or None)
        return

    if not args.curve:
        ap.print_help()
        return

    if not os.path.exists(args.dtb):
        die("DTB 不存在: %s\n可以用 --dtb 指定，或先确认 /boot 挂载正常" % args.dtb)

    pts = parse_curve(args.curve)

    if not args.apply:
        lv, tp, levels, trips = build_props(pts)
        print("试算模式（不会修改任何文件）。确认无误后加 --apply 真正写入。\n")
        print("DTB: %s" % args.dtb)
        print("将写入:")
        print("  cooling-levels = <%s>;" % lv)
        print("  rockchip,temp-trips = <%s>;" % tp)
        if args.freq:
            print("  pwms 周期 -> %d ns (%.2f Hz)" %
                  (int(round(1e9 / args.freq)), args.freq))
        print()
        print("曲线含义：")
        print(fmt_curve(levels, flatten(trips)))
        return

    cmd_apply(args.dtb, pts, args.freq)


if __name__ == "__main__":
    main()
