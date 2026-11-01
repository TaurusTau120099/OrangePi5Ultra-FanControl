# OrangePi 5 Ultra 风扇控制修改指南

## 概述

本人使用板子上的可调速风扇时，发现其风扇控制逻辑较为简单，不符合实际使用需求，遂自行（使用 AI 工具）探究如何控制风扇。

本文档介绍如何修改 Orange Pi 5 Ultra (Rockchip RK3588) 的风扇控制逻辑，包括转速曲线（PWM 值与温度阈值）和控制频率。

本教程可能同样适用于 Orange Pi 5、Orange Pi 5B、Orange Pi 5 Pro、Orange Pi 5 Plus 和 Orange Pi 5 MAX 等使用 RK3588/RK3588S 处理器的开发板，**但 DTb 路径、`phandle` 数值需要按自己板子调整**，有待网友验证。

我的环境为 Orange Pi 5 Ultra，搭配香橙派官方的 Ubuntu 22.04 桌面版（6.1.43 内核，官方镜像名 `Orangepi5ultra_1.0.0_ubuntu_jammy_desktop_xfce_linux6.1.43`）。

> **关于本文的可信度**：初版几乎全部由 AI 生成，其中夹带了若干错误（详见文末「勘误记录」）。当前版本已在实际板子上逐条复现验证，关键结论附有驱动源码依据。

⚠️ **修改设备树有导致系统无法启动的风险，请先备份重要文件和原设备树文件。**

---

## 一、先搞清楚要改哪个文件（初版最大的错误）

很多教程（包括本文初版）直接让你改：

```
/boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb
```

**在 Orange Pi 官方 Ubuntu 22.04 镜像上，改这个文件不会生效。**

### 原因

u-boot 的 `boot.scr`（由 `/boot/boot.cmd` 编译而来）第 45 行是：

```bash
load ${devtype} ${devnum} ${fdt_addr_r} ${prefix}dtb/${fdtfile}
```

其中 `fdtfile` 来自 `/boot/orangepiEnv.txt`：

```
fdtfile=rockchip/rk3588-orangepi-5-ultra.dtb
```

所以真正被加载的是：

```
<启动分区>/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
```

而 `/boot` 挂载的就是那个启动分区（`/dev/nvme0n1p1`，vfat）。所以实际路径是：

```
/boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb      ← 这个才是生效的
```

关键点：**`/boot` 是 vfat 分区，vfat 不支持符号链接**。所以 `dtb/` 和 `dtb-6.1.43-rockchip-rk3588/` 是**两份互相独立的拷贝**（md5 相同），改后者完全是改了个没人读的文件。

### 自己验证一遍

```bash
# 1. /boot 是什么文件系统
findmnt -no FSTYPE,SOURCE,TARGET /boot
# 例：vfat   /dev/nvme0n1p1 /boot

# 2. u-boot 要加载哪个 dtb
grep fdtfile /boot/orangepiEnv.txt
grep 'dtb/' /boot/boot.cmd

# 3. dtb 是符号链接还是实体目录
ls -la /boot/ | grep dtb
readlink -f /boot/dtb

# 4. 两个目录里的文件是不是同一份
md5sum /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb \
       /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb

# 5. 谁归 dpkg 管
dpkg -S /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb
# -> linux-dtb-current-rockchip-rk3588: ...
dpkg -S /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
# -> 不属于任何包（这是镜像构建时额外拷的一份）
```

### 结论

**统一改 `/boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb`。**

这样在两种情况下都正确：

- `/boot` 是 **vfat**（官方 Ubuntu 镜像）：`dtb` 是实体目录，改的就是 u-boot 读的那份；
- `/boot` 是 **ext4**（部分其他发行版）：`dtb` 通常是指向 `dtb-<版本>/` 的符号链接，改它会穿透到同一个文件。

> 顺带一提：`/boot/dtb-<版本>/` 归 `linux-dtb-current-rockchip-rk3588` 包管，`apt upgrade` 会覆盖它；`/boot/dtb/` 不归任何包，但升级内核时可能被重新同步。改完后记得留备份。

---

## 二、准备工作

### 工具

- `dtc` (Device Tree Compiler) —— 官方镜像已预装（版本 1.6.1）
- `python3` —— 已预装（3.10.12）
- `sudo` 权限

缺少 `dtc` 时：

```bash
sudo apt install -y device-tree-compiler
```

### 用本仓库的工具（推荐）

仓库里的 `scripts/fan-curve.py` 封装了下面所有手工步骤，带完整的校验和备份：

```bash
sudo ./scripts/fan-curve.py --show                                  # 看当前曲线（只读）
sudo ./scripts/fan-curve.py --set "0:0,65:160,80:255"               # 试算，不写盘
sudo ./scripts/fan-curve.py --set "0:0,65:160,80:255" --apply       # 真正写入
sudo ./scripts/fan-curve.py --restore                               # 回滚
```

`--set` 的格式是 `温度°C:PWM` 逗号分隔。脚本会自动排序、自动生成 `cooling-levels` 和 `rockchip,temp-trips` 两个属性并保证下标对齐，编译后会重新解包校验数值，校验不过就不写盘。

**这是推荐做法**，因为它从根上避免了手工改 DTB 最容易踩的三个坑（路径错、下标错位、`re.sub` 静默失败）。

下面的手工流程用于理解原理，或你不想用脚本时参考。

---

## 三、查看当前配置

```bash
sudo dtc -I dtb -O dts /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb \
         -o /tmp/rk3588-orangepi-5-ultra.dts

grep -A 7 'pwm-fan {' /tmp/rk3588-orangepi-5-ultra.dts
```

输出（官方镜像默认值）：

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

> `dtc` 解包时会报几十条 `Warning (unit_address_vs_reg)` 之类的警告，那些是 Rockchip 厂商 DTB 自带的，与本次修改无关，可以忽略。
>
> 实测 dtc 1.6.1 对这个 DTB 的 **dtb→dts→dtb round-trip 是无损的**（解包再编译，文件大小和反编译出的文本完全一致），所以放心改。

---

## 四、配置格式说明

### cooling-levels：PWM 值数组

```dts
cooling-levels = <0x00 0x32 0x64 0x96 0xc8 0xff>;
```

**这组数字是 0 起始下标的**（初版表格写成 Level 1..6，与 `temp-trips` 里的 level 编号对不上，是错的）：

| 下标（level） | 十六进制 | 十进制 | 占空比 |
|-----------|---------|--------|--------|
| 0 | `0x00` | 0 | 0% |
| 1 | `0x32` | 50 | 20% |
| 2 | `0x64` | 100 | 39% |
| 3 | `0x96` | 150 | 59% |
| 4 | `0xc8` | 200 | 78% |
| 5 | `0xff` | 255 | 100% |

PWM 是 0..255。驱动里有校验：任何一级大于 255 会直接报错且不注册，风扇行为回到未定义状态。

### rockchip,temp-trips：温度阈值数组

```dts
rockchip,temp-trips = <0xc350 0x01 0xd6d8 0x02 0xea60 0x03 0xfde8 0x04 0x11170 0x05>;
```

格式是 **(温度, level下标)** 成对出现，温度单位是**毫摄氏度**（50°C = 50000 = `0xc350`）。

### 两者如何配合（这是最容易搞错的地方）

驱动源码（`drivers/hwmon/pwm-fan.c`）里是这样选档的：

```c
static int pwm_fan_temp_to_state(struct pwm_fan_ctx *ctx, int temp)
{
	struct thermal_trips *trips = ctx->thermal_trips;
	int i, state = 0;

	for (i = 0; trips[i].state != INT_MAX; i++) {
		if (temp >= trips[i].temp)
			state = trips[i].state;
	}
	return state;
}
```

读懂了就明白三件事：

1. **取"最后一个满足 `temp >= trips[i].temp` 的项"** → `temp-trips` 必须按温度**升序**排列，乱序结果会莫名其妙。
2. **`state` 初值是 0** → 温度低于所有阈值时用 `cooling-levels[0]`。所以第一个 `0°C → level 0` 的条目其实是**可以省略的**（写了也无害，更直观）。
3. **纯 `>=` 比较，没有任何回滞（hysteresis）** → 温度在阈值上下抖动时风扇会反复启停，详见第七节。

默认配置对应的实际曲线：

| 温度 | level | PWM | 占空比 |
|---|---|---|---|
| < 50°C | 0 | 0 | 停转 |
| ≥ 50°C | 1 | 50 | 20% |
| ≥ 55°C | 2 | 100 | 39% |
| ≥ 60°C | 3 | 150 | 59% |
| ≥ 65°C | 4 | 200 | 78% |
| ≥ 70°C | 5 | 255 | 100% |

### ⚠️ 下标越界会让风扇完全不动

```c
static int pwm_fan_thermal_notifier_call(...)
{
	state = pwm_fan_temp_to_state(ctx, event_data->temp);
	if (state > ctx->pwm_fan_max_state)
		return NOTIFY_BAD;          // 直接放弃，不设 PWM
	...
}
```

`pwm_fan_max_state = cooling-levels 的元素个数 - 1`。

**所以 `temp-trips` 里出现的 level 编号必须 ≤ `cooling-levels` 个数 - 1。**
比如 `cooling-levels` 只有 6 个元素（下标 0..5），却写了 `0x02`（=level 2）以外的 `0x06`，那么温度一旦到那一档，风扇就卡在原地不动。

初版示例之所以能跑，是因为它同时把 `cooling-levels` 扩到了 7 个元素、`temp-trips` 用到下标 0..6，两边是对齐的。**但一旦你只改一边，就会出问题。** 「改了没反应」十有八九是这个原因。

### 常用温度阈值换算

温度 → 十六进制就是 `温度 × 1000` 转十六进制。下表已逐条核算过（初版有 6 个值是错的）：

| 温度(°C) | 毫摄氏度 | 十六进制 |
|---------|---------|---------|
| 40 | 40000 | `0x9c40` |
| 45 | 45000 | `0xafc8` |
| 50 | 50000 | `0xc350` |
| 55 | 55000 | `0xd6d8` |
| 60 | 60000 | `0xea60` |
| 65 | 65000 | `0xfde8` |
| 68 | 68000 | `0x109a0` |
| 70 | 70000 | `0x11170` |
| 71 | 71000 | `0x11558` |
| 74 | 74000 | `0x12110` |
| 75 | 75000 | `0x124f8` |
| 77 | 77000 | `0x12cc8` |
| 80 | 80000 | `0x13880` |
| 85 | 85000 | `0x14c08` |

自己算的话：

```bash
$ printf '0x%x\n' $((65 * 1000))      # 0xfde8
$ printf '0x%x\n' $((68 * 1000))      # 0x109a0
```

---

## 五、修改风扇转速曲线

### 目标曲线示例

让风扇：

- **< 65°C 停转**
- **65 ~ 80°C 之间每 3°C 加 20**
- **≥ 80°C 满转**
- 频率保持 **50 Hz**

对应的曲线点：

| 温度 | PWM | 占空比 |
|---|---|---|
| 0°C | 0 | 停转 |
| 65°C | 160 | 63% |
| 68°C | 180 | 71% |
| 71°C | 200 | 78% |
| 74°C | 220 | 86% |
| 77°C | 240 | 94% |
| 80°C | 255 | 100% |

### 方法 A：用脚本（推荐）

```bash
sudo ./scripts/fan-curve.py --set "0:0,65:160,68:180,71:200,74:220,77:240,80:255"
```

先看试算结果，确认无误再：

```bash
sudo ./scripts/fan-curve.py --set "0:0,65:160,68:180,71:200,74:220,77:240,80:255" --apply
```

脚本会自动备份到 `/root/fan-curve-backup/`。

### 方法 B：手工改 DTS

#### 1. 备份

```bash
sudo cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb \
        /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb.bak-$(date +%Y%m%d-%H%M%S)
```

建议再往 rootfs 里存一份（`/boot` 是 vfat，万一改坏了还能从系统里找回）：

```bash
sudo mkdir -p /root/dtb-backup
sudo cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb /root/dtb-backup/
```

#### 2. 替换两个属性

⚠️ **初版的做法是直接 `re.sub`，匹配不到也不会报错，照样打印 "Done"，这是最坑的一点。** 下面这版会**先断言每处只命中一次**，匹配失败直接中止且不碰任何文件。

```bash
sudo tee /tmp/fix_fan.py > /dev/null << 'EOF'
import re, sys

DTS = "/tmp/rk3588-orangepi-5-ultra.dts"

NEW_LEVELS = "0x00 0xa0 0xb4 0xc8 0xdc 0xf0 0xff"
NEW_TRIPS  = ("0x0000 0x00 0xfde8 0x01 0x109a0 0x02 "
              "0x11558 0x03 0x12110 0x04 0x12cc8 0x05 0x13880 0x06")

text = open(DTS, encoding="utf-8").read()

pat_levels = r"cooling-levels\s*=\s*<[^>]*>\s*;"
pat_trips  = r"rockchip,temp-trips\s*=\s*<[^>]*>\s*;"

n1 = len(re.findall(pat_levels, text))
n2 = len(re.findall(pat_trips, text))
print("匹配 cooling-levels: %d 处" % n1)
print("匹配 temp-trips    : %d 处" % n2)
if n1 != 1 or n2 != 1:
    sys.exit("中止：目标属性未唯一匹配，文件未做任何修改")

text = re.sub(pat_levels, "cooling-levels = <%s>;" % NEW_LEVELS, text)
text = re.sub(pat_trips,  "rockchip,temp-trips = <%s>;" % NEW_TRIPS, text)

open(DTS, "w", encoding="utf-8").write(text)
print("已修改")
EOF

sudo python3 /tmp/fix_fan.py
```

#### 3. 编译回去，并检查只改了该改的

```bash
sudo dtc -I dts -O dtb /tmp/rk3588-orangepi-5-ultra.dts \
         -o /tmp/new.dtb

# 反编译回来，确认数值
sudo dtc -I dtb -O dts /tmp/new.dtb -o /tmp/check.dts
grep -E 'cooling-levels|rockchip,temp-trips' /tmp/check.dts

# 和原始 DTS 对比，应该只有这 2 行长这样
diff /tmp/rk3588-orangepi-5-ultra.dts /tmp/check.dts
```

确认无误后再覆盖进去：

```bash
sudo cp /tmp/new.dtb /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
sync
```

> 先编译到 `/tmp` 再 `cp` 覆盖，而不是让 `dtc` 直接 `-o` 写到 `/boot`：万一编译失败，`/boot` 里不会留下半截文件。

#### 4. 重启

```bash
sudo reboot
```

---

## 六、修改控制频率

### 查看当前频率

```bash
grep -n 'pwms' /tmp/rk3588-orangepi-5-ultra.dts | head
```

当前配置：`pwms = <0x203 0x00 0x1312d00 0x00>;`

第二个数是通道号，**第三个数是周期（单位 ns）**：`0x1312d00` = 20,000,000 ns = 20 ms = **50 Hz**。

### 频率换算

`周期(ns) = 1e9 / 频率(Hz)`

| 频率 | 周期(ns) | 十六进制 |
|------|---------|---------|
| 25 Hz | 40,000,000 | `0x2625A00` |
| 50 Hz | 20,000,000 | `0x1312d00` |
| 100 Hz | 10,000,000 | `0x989680` |
| 200 Hz | 5,000,000 | `0x4C4B40` |
| 500 Hz | 2,000,000 | `0x1E8480` |
| 1 kHz | 1,000,000 | `0xF4240` |
| 5 kHz | 200,000 | `0x30D40` |
| 10 kHz | 100,000 | `0x186A0` |

（这张表初版就是对的，已复核。）

### 示例：改为 100 Hz

用脚本最省事：

```bash
sudo ./scripts/fan-curve.py --set "0:0,65:160,80:255" --freq 100 --apply
```

手工的话把 `pwms` 的第三个数换掉：

```bash
sudo sed -i 's/pwms = <0x203 0x00 0x1312d00 0x00>;/pwms = <0x203 0x00 0x989680 0x00>;/' \
        /tmp/rk3588-orangepi-5-ultra.dts
```

> **改频率基本没有实际意义。** Orange Pi 这块板子是用 NMOS 管通断来控制风扇两端电压的（就是粗暴地斩波），只能粗略调速；这和真正的三线（PWM 控制）/四线（带转速反馈）风扇完全不是一回事。改频率主要影响的是噪声特性，对转速曲线没有帮助。

---

## 七、风扇的运行时控制（`pwm1_enable` 的真实含义）

### 先找到正确的 sysfs 路径

初版写死了 `hwmon7`，**这个编号不保证每次开机都一样**。用下面这种方式定位：

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
echo "$FAN"
# 例：/sys/devices/platform/pwm-fan/hwmon/hwmon7

cat "$FAN/pwm1"          # 当前 PWM 值 0..255
cat "$FAN/pwm1_enable"   # 控制模式
```

hwmon 的设备名是 `pwmfan`（不是 `pwm-fan`）：

```bash
grep -l pwmfan /sys/class/hwmon/hwmon*/name
```

### `pwm1_enable` 的真实取值

驱动里的枚举（`drivers/hwmon/pwm-fan.c`）：

```c
enum pwm_fan_enable_mode {
	pwm_off_reg_off,          /* 0 */
	pwm_disable_reg_enable,   /* 1 */
	pwm_enable_reg_enable,    /* 2 */
	pwm_disable_reg_disable,  /* 3 */
};
```

| 值 | 枚举 | 实际行为 |
|---|---|---|
| **0** | `pwm_off_reg_off` | PWM 与风扇供电都关闭。且 `__set_pwm()` 对任何大于 0 的 PWM 直接 `return 0`，**后续写 `pwm1` 全部无效，温控也不会再把风扇启动起来** —— 这才是真正的「禁用风扇」 |
| **1** | `pwm_disable_reg_enable` | **驱动 probe 时写死的默认值**。PWM 输出关闭、风扇供电保持 |
| **2** | `pwm_enable_reg_enable` | PWM 与供电都使能 |
| **3** | `pwm_disable_reg_disable` | 两者都关，驱动卸载（`pwm_fan_cleanup`）时内部使用 |

### ⚠️ 初版在这里说错了

初版把 1 说成「手动模式」、2 说成「自动温控模式」，**这是错的**。

真实情况是：**根本不存在「自动 / 手动」这个开关。**

温度控制不是由 `pwm1_enable` 驱动的，而是驱动注册的一个 notifier：

```c
ret = pwm_fan_register_thermal_notifier(dev, ctx);
```

它在温度变化时**直接调用 `set_pwm()`**，完全无视 `pwm1_enable` 是 1 还是 2。所以在 1、2、3 三种模式下：

- 你写 `echo 180 > pwm1` 确实会立刻生效；
- 但**下一次温度变化事件会立刻把它改回曲线对应的值**。

也就是说**手动写 `pwm1` 只是暂时的**。

实测佐证：板子重启后 `pwm1_enable` 就是 `1`，而此时风扇完全跟随温度曲线变化（50.8°C 时 `pwm1=0`，满载升到 65.6°C 后 `pwm1` 跳到 `160`）。

另外，只要 DTB 里有 `rockchip,temp-trips`，驱动会在注册 notifier 后**直接 return**，不会再把 `pwm-fan` 注册成 thermal cooling device：

```c
if (IS_REACHABLE(CONFIG_ROCKCHIP_SYSTEM_MONITOR) &&
    of_find_property(dev->of_node, "rockchip,temp-trips", NULL)) {
	ret = pwm_fan_register_thermal_notifier(dev, ctx);
	...
	return 0;                       /* ← 提前返回，跳过 cooling device 注册 */
}
```

这也能从侧面印证 —— 板子上 `ls /sys/class/thermal/cooling_device*` 只看得到 5 个（3 个 cpufreq + dmc + gpu），**没有 pwm-fan**。而 DTB 里 `/pwm-fan` 的 `phandle` 是 `0x4c8`，所有 `cooling-maps` 引用的却是 `0x6 / 0xa / 0xc / 0x6c`，确实没有它。

### 想要真正稳定的手动控制

把 DTB 里的 `rockchip,temp-trips` **整个删掉**。

这样驱动会走另一条分支（注册成 cooling device），而由于没有任何 thermal zone 的 `cooling-map` 引用 `pwm-fan`，温度变化不会改转速，此时写 `pwm1` 才稳定可控：

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
sudo tee "$FAN/pwm1" <<< 180
```

（注意：这条路径需要你自己改 DTB 删属性，属于「放弃自动温控」，慎用。）

### 临时把风扇关掉

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
sudo tee "$FAN/pwm1_enable" <<< 0     # 立即停转，且温控无法再启动它
```

重启后恢复。

---

## 八、确认改动是否真的生效（重要，初版没有这一节）

不要只看 "重启成功" 就以为改上了。**唯一可靠的判据是运行中的 DTB**：

```bash
# 运行中的 DTB 反映的是内核实际使用的设备树
od -A n -t x1 /proc/device-tree/pwm-fan/cooling-levels
od -A n -t x1 /proc/device-tree/pwm-fan/rockchip,temp-trips
```

对照你写进去的值。比如 `cooling-levels` 应该是 7 个 32 位大端整数：

```
00 00 00 00  00 00 00 a0  00 00 00 b4  00 00 00 c8
00 00 00 dc  00 00 00 f0  00 00 00 ff
```

再看实时状态：

```bash
for z in /sys/class/thermal/thermal_zone*; do
    echo "$(cat $z/type) = $(cat $z/temp)"
done

FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
echo "pwm1 = $(cat $FAN/pwm1)"
```

如果 `/proc/device-tree` 里还是旧值，说明**你改的文件不是 u-boot 加载的那个** —— 回到第一节重新核对路径。

### 做一次负载测试

```bash
# 8 核跑满，观察温度和 pwm 的变化
for i in $(seq $(nproc)); do timeout 180 yes > /dev/null & done
while :; do
    echo "$(cat /sys/class/thermal/thermal_zone0/temp) mC  pwm=$(cat $FAN/pwm1)"
    sleep 3
done
# Ctrl-C 结束；记得 pkill yes
```

---

## 九、已知现象与限制

### 1. 65°C 附近会周期性启停（无回滞导致）

用本文的示例曲线（65°C 从 0 直接跳到 160）实测，8 核满载时温度稳定在 **63.7 ~ 65.6°C**，正好骑在阈值上，于是：

```
pwm 0（约 10 秒）→ pwm 160（约 35 秒）→ pwm 0 → pwm 160 …
```

周期约 45 秒，风扇反复启停，挺吵。

根本原因是驱动**没有任何回滞**（见第四节源码），温度一低于阈值就立刻回 0 档。这是设计使然，不是配置错误。

想缓解，几种思路（按改动从小到大）：

- **降低第一档的 PWM**，让 65°C 时的风量更平缓，不至于一开就把温度压回阈值以下：

  ```bash
  sudo ./scripts/fan-curve.py --set "0:0,65:80,68:125,71:170,74:210,77:240,80:255" --apply
  ```

- **把第一个触发点抬高**到 70°C，让日常负载不容易碰到阈值。
- **保留一个低速常转基线**（第一档 PWM 不设 0），彻底消除启停，代价是不再「完全静音」。
- **接受它**，或者换带 PWM 控制的三线/四线风扇（但板子的接口和驱动未必支持）。

### 2. 开机瞬间会满速一下

驱动 probe 时先无条件 `set_pwm(ctx, MAX_PWM)`，并把 `pwm_fan_state` 置为最大档，要等第一次温度事件才会被 notifier 纠正。所以开机瞬间风扇会全速一下，属正常现象。

### 3. 升级内核 / dtb 包可能覆盖你的改动

- `/boot/dtb-<版本>/` 归 `linux-dtb-current-rockchip-rk3588` 包管，`apt upgrade` 会覆盖；
- `/boot/dtb/` 不归包管，但内核升级时可能被重新同步。

改完记得把 `cp` 出来的备份留着，升级后重新应用一遍。

### 4. 频率改变收益有限

见第六节末尾说明。

### 5. 低于 50°C 不再强制常转

官方默认曲线在 50°C 以下也是 0（`state` 初值为 0 → `cooling-levels[0]` = 0）。初版没写清楚这点，容易被误解成「50°C 以下保持 50」。

---

## 十、回滚

```bash
# 用脚本
sudo ./scripts/fan-curve.py --restore

# 或手工
sudo cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb.bak-<时间戳> \
        /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
sync
sudo reboot
```

如果改坏了导致**开不了机**，只能从外部救：

1. 把 NVMe 拆下来接 USB 硬盘盒，或用 SD 卡启动一个系统；
2. 挂载启动分区（vfat），把 `.bak` 文件改回原名；
3. 或者干脆重新烧写系统镜像。

**所以改之前务必备份到板子以外的地方，或者至少备份到 rootfs（ext4）里，别只存在 `/boot` 里。**

---

## 十一、勘误记录

### v1.1（2026-11）

以下问题均已在真机（Orange Pi 5 Ultra，官方 Ubuntu 22.04，内核 6.1.43-rockchip-rk3588）上复现验证：

1. **DTB 路径整体错误**（影响：改动完全不生效）
   `/boot/dtb-6.1.43-rockchip-rk3588/...` → 应为 `/boot/dtb/...`。
   原因：`boot.cmd` 加载的是 `${prefix}dtb/${fdtfile}`，且 `/boot` 为 vfat、不支持符号链接，两个目录是独立拷贝。

2. **温度换算表 6 个值错误**

   | 温度 | 初版 | 正确 | 初版实际是 |
   |---|---|---|---|
   | 65°C | `0xfe18` | `0xfde8` | 65.048°C |
   | 68°C | `0x10a38` | `0x109a0` | 68.152°C |
   | 71°C | `0x11578` | `0x11558` | 71.032°C |
   | 74°C | `0x120b8` | `0x12110` | 73.912°C |
   | 77°C | `0x12bf8` | `0x12cc8` | 76.792°C |
   | 85°C | `0x14a38` | `0x14c08` | 84.536°C |

   偏差最大 0.46°C，功能上影响很小，但既然要精确就该用正确值。

3. **`cooling-levels` 表格下标基准错误**
   写成 Level 1..6（1 起始），与 `temp-trips` 里的 level 编号对不上，正确的应是 0..5。

4. **`pwm1_enable` 语义错误**
   初版：「1 = 手动模式，2 = 自动温控模式，0 = 禁用」。
   实际：1 是驱动默认值，**1/2/3 模式下温控 notifier 都在工作**，手动写 `pwm1` 会被温度事件覆盖；0 才是真禁用。

5. **`re.sub` 静默失败**
   匹配不到也照样 `print("Done")`，用户以为改成功了。现改为先断言唯一匹配，否则中止。

6. **`hwmon7` 路径写死**
   hwmon 编号每次开机可能变，现改为用 `find` 定位。

7. **缺少「如何验证真的生效」**
   新增第八节，用 `/proc/device-tree` 作为判据。

8. **缺少回滞/启停现象说明**
   新增第九节，附实测数据。

9. **补充**：`temp-trips` 的 level 下标必须 ≤ `cooling-levels` 个数 - 1，否则风扇完全不动作；`0°C → level 0` 条目可省略；开机瞬间会满速一下；`/boot/dtb-<版本>/` 会被 apt 覆盖。

10. **新增** `scripts/fan-curve.py`：带校验与备份的曲线修改工具，避免上述手工操作的坑。

---

## 十二、注意事项

1. **改前先备份**，并且最好备份到 rootfs 或板子以外，避免系统起不来时无法找回。
2. **认准 `/boot/dtb/`**，不要改 `dtb-<版本>/`（在 vfat 的 `/boot` 上）。
3. **`temp-trips` 按温度升序**，level 下标不得超过 `cooling-levels` 个数 - 1。
4. **频率不是越高越好**：低占空比时高频率可能导致风扇驱动响应变差；而且这块板子的调速方式本来就很粗。
5. **共振问题**：风扇在特定转速可能与外壳产生共振，通常需要机械方案（如加电容、加减震垫）解决。
6. **测试建议**：改 DTB 前先用 sysfs 临时试不同 PWM 值，找到合适的曲线再固化到 DTB（注意第七节：手动写会被温控覆盖，测的时候尽快观察）。
