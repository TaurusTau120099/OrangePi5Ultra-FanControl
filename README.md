# OrangePi 5 Ultra 风扇控制修改指南

通过修改设备树（DTB）里的 `pwm-fan` 节点，自定义风扇转速曲线与 PWM 频率。

**只想改配置、不关心原理？** 直接跳到文末 [完整实例](#完整实例)，改两行参数、整段粘贴执行即可。

下面的步骤 1–6 按顺序解释每一处改动在做什么、为什么这么做。

> 测试环境：官方 Ubuntu 22.04 桌面版，内核 `6.1.43-rockchip-rk3588`。
> 其他 RK3588/RK3588S 板子思路相同，但 DTB 路径与 `phandle` 需按实际调整。
> ⚠️ 设备树写错可能导致无法开机，动手前先备份。

---

## 步骤 1 · 确认要改哪个 DTB

**目的**：找到 u-boot 真正加载的那个 `.dtb`。改错文件不会有任何报错，但配置不会生效。

`/boot` 是 vfat 分区，**不支持符号链接**，所以里面同时存在两份内容相同的 DTB 目录：

| 路径 | 说明 |
|---|---|
| `/boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb` | ✅ 实际生效，改这个 |
| `/boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb` | ❌ 只是备份，改了没用 |

依据：u-boot 的 `boot.scr` 用 `load ... ${prefix}dtb/${fdtfile}` 加载设备树，`fdtfile` 取自 `/boot/orangepiEnv.txt`（值就是 `rockchip/rk3588-orangepi-5-ultra.dtb`）。

想确认的话：

```bash
findmnt -no FSTYPE,TARGET /boot      # vfat /boot
grep fdtfile /boot/orangepiEnv.txt
```

## 步骤 2 · 解包成可编辑的文本

**目的**：把二进制 `.dtb` 转成 `.dts` 文本，看清楚风扇节点现在的配置，后面才能改。

```bash
sudo dtc -I dtb -O dts /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb -o /tmp/fan.dts
grep -A 7 'pwm-fan {' /tmp/fan.dts
```

输出（出厂默认）：

```dts
	pwm-fan {
		compatible = "pwm-fan";
		#cooling-cells = <0x02>;
		pwms = <0x203 0x00 0x1312d00 0x00>;
		cooling-levels = <0x00 0x32 0x64 0x96 0xc8 0xff>;
		rockchip,temp-trips = <0xc350 0x01 0xd6d8 0x02 0xea60 0x03 0xfde8 0x04 0x11170 0x05>;
		status = "okay";
		phandle = <0x4c8>;
	};
```

`dtc` 会刷几十条 `Warning (unit_address_...)`，那是厂商 DTB 自带的，与本次修改无关，忽略即可。

## 步骤 3 · 认识要改的两个属性

**目的**：搞清数值含义和两者的关系。这两处不匹配是"改了没反应"的头号原因。

`cooling-levels` —— PWM 档位表，**下标从 0 开始**，取值 0–255：

```
0x00 0x32 0x64 0x96 0xc8 0xff
 ↓    ↓    ↓    ↓    ↓    ↓
0    50   100  150  200  255   (占空比 0% / 20% / 39% / 59% / 78% / 100%)
```

`rockchip,temp-trips` —— **（温度, 档位下标）** 成对排列，温度单位是**毫摄氏度**（50°C = 50000 = `0xc350`）：

```
0xc350 0x01   →  50°C 时使用 cooling-levels[1] = 50
0xd6d8 0x02   →  55°C 时使用 cooling-levels[2] = 100
...
```

三条必须遵守的规则：

1. **按温度升序排列**。驱动取的是"最后一个满足 `温度 ≥ 阈值` 的项"，乱序结果不可预期。
2. **最大档位 ≤ `cooling-levels` 元素个数 − 1**。越界时驱动直接放弃，风扇卡在原地不动。所以这两个数组要**成对修改**。
3. **低于所有阈值时用档位 0**，所以 `0°C → 档位 0` 这一条可写可不写。

温度换算：`十六进制 = 摄氏度 × 1000`。

```bash
printf '0x%x\n' $((65 * 1000))     # 0xfde8
```

| 温度 | 65 | 68 | 71 | 74 | 77 | 80 | 85 |
|---|---|---|---|---|---|---|---|
| hex | `fde8` | `109a0` | `11558` | `12110` | `12cc8` | `13880` | `14c08` |

## 步骤 4 · 把新曲线写进 DTS

**目的**：按你在步骤 3 里定下的曲线，重新生成两个数组并替换掉原来的。

下面这段脚本能保证两个数组下标对齐，且匹配失败时会中止（不会静默改错文件）。只需改 `PTS` 那一行：

```bash
sudo python3 - <<'EOF'
import re, sys

# ===== 改这里：(温度°C, PWM) 按温度升序 =====
PTS = [(0,0), (65,160), (68,180), (71,200), (74,220), (77,240), (80,255)]
# ===========================================

p = "/tmp/fan.dts"
t = open(p, encoding="utf-8").read()

lv = " ".join("0x%02x" % pwm for _, pwm in PTS)
tp = " ".join("0x%08x 0x%02x" % (int(temp * 1000), i)
              for i, (temp, _) in enumerate(PTS))

pat_l = r"cooling-levels\s*=\s*<[^>]*>\s*;"
pat_t = r"rockchip,temp-trips\s*=\s*<[^>]*>\s*;"
if len(re.findall(pat_l, t)) != 1 or len(re.findall(pat_t, t)) != 1:
    sys.exit("未找到唯一的目标属性，中止（文件未改动）")

t = re.sub(pat_l, "cooling-levels = <%s>;" % lv, t)
t = re.sub(pat_t, "rockchip,temp-trips = <%s>;" % tp, t)
open(p, "w", encoding="utf-8").write(t)

print("cooling-levels     = <%s>" % lv)
print("rockchip,temp-trips = <%s>" % tp)
EOF
```

## 步骤 5 · 编译回二进制并替换

**目的**：把改好的文本编译回 `.dtb`，覆盖到步骤 1 确认的位置。先编译到 `/tmp` 再覆盖，可避免编译失败时把 `/boot` 里的文件写坏。

```bash
sudo dtc -I dts -O dtb /tmp/fan.dts -o /tmp/fan_new.dtb 2>/dev/null \
    || { echo "编译失败，请检查 /tmp/fan.dts"; exit 1; }

# 核对：反编译回来，确认只有那两个属性变了
sudo dtc -I dtb -O dts /tmp/fan_new.dtb -o /tmp/fan_check.dts 2>/dev/null
grep -E 'cooling-levels|rockchip,temp-trips' /tmp/fan_check.dts

sudo cp /tmp/fan_new.dtb /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
sync
```

## 步骤 6 · 重启并验证

**目的**：确认新配置真的被内核用上了。**只看"重启成功"不算数**，要以运行中的设备树为准。

```bash
sudo reboot
```

重启后：

```bash
# 运行中的设备树，是唯一可靠的判据
od -A n -t x1 /proc/device-tree/pwm-fan/cooling-levels
od -A n -t x1 /proc/device-tree/pwm-fan/rockchip,temp-trips
```

`cooling-levels` 应该是一串大端 32 位整数，例如 7 档时：

```
00 00 00 00  00 00 00 a0  00 00 00 b4  00 00 00 c8
00 00 00 dc  00 00 00 f0  00 00 00 ff
```

如果还是旧值，说明步骤 1 的文件选错了。

再看实时状态：

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
echo "pwm1 = $(cat $FAN/pwm1)"
for z in /sys/class/thermal/thermal_zone*; do echo "$(cat $z/type) = $(cat $z/temp)"; done
```

---

## 完整实例

把风扇改成：**65°C 以下停转，65–80°C 每 3°C 加 20，80°C 以上全速，频率 50Hz**。

整段复制执行即可（已实测）。要改曲线只动 `PTS` 一行。

```bash
set -e
DTB=/boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb

# 1. 备份
cp "$DTB" "$DTB.bak-$(date +%Y%m%d-%H%M%S)"
mkdir -p /root/dtb-backup && cp "$DTB" /root/dtb-backup/

# 2. 解包
dtc -I dtb -O dts "$DTB" -o /tmp/fan.dts 2>/dev/null

# 3. 改曲线：只需改 PTS 这一行
python3 - <<'EOF'
import re, sys
PTS = [(0,0), (65,160), (68,180), (71,200), (74,220), (77,240), (80,255)]
p = "/tmp/fan.dts"
t = open(p, encoding="utf-8").read()
lv = " ".join("0x%02x" % pwm for _, pwm in PTS)
tp = " ".join("0x%08x 0x%02x" % (int(temp * 1000), i)
              for i, (temp, _) in enumerate(PTS))
pat_l = r"cooling-levels\s*=\s*<[^>]*>\s*;"
pat_t = r"rockchip,temp-trips\s*=\s*<[^>]*>\s*;"
if len(re.findall(pat_l, t)) != 1 or len(re.findall(pat_t, t)) != 1:
    sys.exit("未找到唯一的目标属性，中止")
t = re.sub(pat_l, "cooling-levels = <%s>;" % lv, t)
t = re.sub(pat_t, "rockchip,temp-trips = <%s>;" % tp, t)
open(p, "w", encoding="utf-8").write(t)
print("cooling-levels     = <%s>" % lv)
print("rockchip,temp-trips = <%s>" % tp)
EOF

# 4. 编译并替换
dtc -I dts -O dtb /tmp/fan.dts -o /tmp/fan_new.dtb 2>/dev/null \
    || { echo "编译失败，检查 /tmp/fan.dts"; exit 1; }
cp /tmp/fan_new.dtb "$DTB"
sync

# 5. 重启
reboot
```

执行后应看到类似：

```
cooling-levels     = <0x00 0xa0 0xb4 0xc8 0xdc 0xf0 0xff>
rockchip,temp-trips = <0x00000000 0x00 0x0000fde8 0x01 ... 0x00013880 0x06>
```

重启后用 `od -A n -t x1 /proc/device-tree/pwm-fan/cooling-levels` 确认已生效。

> 也可用仓库里的 `scripts/fan-curve.py`，省去手写 python：
> `sudo ./scripts/fan-curve.py --set "0:0,65:160,68:180,71:200,74:220,77:240,80:255" --apply`
> 它自带备份、编译校验和 `--restore`。

---

## 改 PWM 频率（可选）

**目的**：调整 PWM 周期。第三个数字是周期，单位 ns，`周期 = 1e9 ÷ 频率`。默认 `0x1312d00` = 20,000,000 ns = **50 Hz**。

| 频率 | 25 Hz | 50 Hz | 100 Hz | 200 Hz | 500 Hz | 1 kHz | 5 kHz |
|---|---|---|---|---|---|---|---|
| 周期 ns | 40000000 | 20000000 | 10000000 | 5000000 | 2000000 | 1000000 | 200000 |
| hex | `2625A00` | `1312d00` | `989680` | `4C4B40` | `1E8480` | `F4240` | `30D40` |

在步骤 4 之后、步骤 5 之前，对 `/tmp/fan.dts` 执行：

```bash
sudo sed -i 's/pwms = <0x203 0x00 0x1312d00 0x00>;/pwms = <0x203 0x00 0x989680 0x00>;/' /tmp/fan.dts
```

> 改频率收益有限。这块板子用 NMOS 通断控制风扇两端电压，只能粗略调速，和真正的三线（PWM）/四线（带测速）风扇不是一回事。

## 运行时查看与控制（可选）

sysfs 路径里的 hwmon 编号每次开机可能变化，**不要写死**：

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
cat "$FAN/pwm1"          # 当前 PWM 0..255
cat "$FAN/pwm1_enable"   # 控制模式
```

| `pwm1_enable` | 行为 |
|---|---|
| `0` | PWM 与风扇供电都关；**之后写 `pwm1` 无效，温控也不会再启动风扇**（真正禁用） |
| `1` | 驱动默认值：PWM 输出关、供电保持 |
| `2` | PWM 与供电都使能 |
| `3` | 两者都关（驱动卸载时内部使用） |

**没有"自动 / 手动"开关。** 温度控制由独立 notifier 驱动，温度一变就直接设 PWM，不看 `pwm1_enable`。所以在 `1`/`2`/`3` 下你写 `pwm1` 会立即生效，但**下一次温度变化就会把它覆盖回曲线值**。

要稳定的手动控制，得把 DTB 里的 `rockchip,temp-trips` 删掉（等于放弃自动温控）；此时本机的 thermal zone 并没有引用这个风扇，温度就不再影响转速了。

## 已知现象

**1. 阈值附近周期性启停** —— 驱动无回滞（纯 `>=` 比较）。若第一档跨度大（如 0 → 160）且满载温度恰好稳定在阈值附近，会看到「停约 10 秒 → 转约 35 秒」的反复。缓解办法：降低第一档 PWM、抬高第一个触发点，或保留一个低速常转档位。

**2. 开机瞬间满速一下** —— 驱动初始化时先无条件设为满 PWM，等第一次温度事件才纠正，属正常。

**3. 升级内核或 dtb 包会覆盖改动** —— `/boot/dtb-<版本>/` 归 `linux-dtb-current-rockchip-rk3588` 包管理；`/boot/dtb/` 虽不归包管，内核升级时也可能被重新同步。升级后用步骤 6 的方法复查。

## 回滚

```bash
cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb.bak-<时间戳> \
   /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
reboot
```

若改坏导致无法开机，只能从外部恢复：拆下 NVMe 接 USB 硬盘盒、或从 SD 卡启动，挂载启动分区（vfat）后把备份改回原名；或重新烧写镜像。**所以备份别只留在 `/boot` 里。**

---

版本变更见 [CHANGELOG.md](CHANGELOG.md)。
