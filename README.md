# OrangePi 5 Ultra 风扇控制修改指南

Orange Pi 5 Ultra（RK3588）的风扇由设备树 `pwm-fan` 节点控制。本文介绍如何修改转速曲线和 PWM 频率。

- 测试环境：官方 Ubuntu 22.04 桌面版，内核 `6.1.43-rockchip-rk3588`
- 其他 RK3588/RK3588S 板子（Orange Pi 5 / 5B / 5 Pro / 5 Plus / 5 MAX）思路相同，但 DTB 路径和 `phandle` 需按实际调整
- ⚠️ 改设备树有导致无法开机的风险，先备份

---

## 一、要改的文件

```
/boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
```

**不是** `/boot/dtb-6.1.43-rockchip-rk3588/rockchip/...`。

u-boot 由 `boot.scr` 加载设备树：

```bash
load ${devtype} ${devnum} ${fdt_addr_r} ${prefix}dtb/${fdtfile}
```

`fdtfile` 取自 `/boot/orangepiEnv.txt`（`rockchip/rk3588-orangepi-5-ultra.dtb`），所以实际路径是 `<启动分区>/dtb/rockchip/...`，而 `/boot` 挂载的就是启动分区。又因为 `/boot` 是 **vfat**，不支持符号链接，`dtb/` 和 `dtb-<版本>/` 是两份互相独立的拷贝 —— 改后者不会生效。

自己确认一下：

```bash
findmnt -no FSTYPE,SOURCE,TARGET /boot     # vfat   /dev/nvme0n1p1 /boot
grep fdtfile /boot/orangepiEnv.txt          # fdtfile=rockchip/rk3588-orangepi-5-ultra.dtb
ls -la /boot/ | grep dtb                    # dtb 与 dtb-6.1.43-... 并列
md5sum /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb \
       /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb   # 内容相同
```

> `/boot` 若是 ext4，`dtb` 通常是指向 `dtb-<版本>/` 的符号链接，改哪个都行。**统一改 `/boot/dtb/` 在两种情况下都正确。**

---

## 二、查看当前配置

```bash
sudo dtc -I dtb -O dts /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb \
         -o /tmp/rk3588-orangepi-5-ultra.dts
grep -A 7 'pwm-fan {' /tmp/rk3588-orangepi-5-ultra.dts
```

官方默认值：

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

`dtc` 会输出几十条 `unit_address_vs_reg` 之类的警告，那是厂商 DTB 自带的，与本次修改无关，可忽略。该 DTB 的 dtb→dts→dtb 往返是无损的。

---

## 三、配置格式

### cooling-levels：PWM 档位

```dts
cooling-levels = <0x00 0x32 0x64 0x96 0xc8 0xff>;
```

**0 起始下标**，PWM 取值范围 0..255。

| 下标 | 十六进制 | 十进制 | 占空比 |
|------|---------|--------|--------|
| 0 | `0x00` | 0 | 0% |
| 1 | `0x32` | 50 | 20% |
| 2 | `0x64` | 100 | 39% |
| 3 | `0x96` | 150 | 59% |
| 4 | `0xc8` | 200 | 78% |
| 5 | `0xff` | 255 | 100% |

任一级大于 255 会导致驱动报错并跳过注册。

### rockchip,temp-trips：温度阈值

```dts
rockchip,temp-trips = <0xc350 0x01 0xd6d8 0x02 0xea60 0x03 0xfde8 0x04 0x11170 0x05>;
```

格式是 **(温度, 档位下标)** 成对排列，温度单位**毫摄氏度**（50°C = 50000 = `0xc350`）。

### 两者的配合规则

驱动 `drivers/hwmon/pwm-fan.c` 的选档逻辑：

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

由此得到三条约束：

1. **`temp-trips` 必须按温度升序** —— 取的是最后一个满足 `temp >= trips[i].temp` 的项，乱序结果不可预期。
2. **`state` 初值为 0** —— 温度低于所有阈值时用 `cooling-levels[0]`。所以 `0°C → 档位 0` 这一条可以省略。
3. **纯 `>=` 比较，无回滞** —— 温度在阈值附近抖动会导致风扇反复启停，见第八节。

默认配置的实际曲线：

| 温度 | 档位 | PWM | 占空比 |
|---|---|---|---|
| < 50°C | 0 | 0 | 停转 |
| ≥ 50°C | 1 | 50 | 20% |
| ≥ 55°C | 2 | 100 | 39% |
| ≥ 60°C | 3 | 150 | 59% |
| ≥ 65°C | 4 | 200 | 78% |
| ≥ 70°C | 5 | 255 | 100% |

### ⚠️ 档位下标不能越界

```c
state = pwm_fan_temp_to_state(ctx, event_data->temp);
if (state > ctx->pwm_fan_max_state)
	return NOTIFY_BAD;          // 放弃，不设置 PWM
```

`pwm_fan_max_state = cooling-levels 元素个数 - 1`。

**`temp-trips` 里用到的最大档位必须 ≤ `cooling-levels` 个数 - 1**，否则升到那一档时风扇会卡住不动。只改一边是最常见的翻车原因。

### 温度换算

十六进制值 = `温度 × 1000` 转十六进制。

| 温度(°C) | 毫摄氏度 | 十六进制 |
|---------|---------|---------|
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

```bash
printf '0x%x\n' $((65 * 1000))      # 0xfde8
```

---

## 四、修改转速曲线

目标示例：65°C 以下停转，65~80°C 每 3°C 加 20，80°C 以上满转。

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
sudo ./scripts/fan-curve.py --show                    # 查看当前曲线
sudo ./scripts/fan-curve.py --set "0:0,65:160,80:255" # 试算，不写盘

# 确认后写入
sudo ./scripts/fan-curve.py --set "0:0,65:160,68:180,71:200,74:220,77:240,80:255" --apply
```

`--set` 用 `温度:PWM` 描述曲线，脚本自动排序、生成两个属性并保证下标对齐，编译后反解校验数值，不通过就不写盘。原文件备份到 `/root/fan-curve-backup/`。

### 方法 B：手工改 DTS

**1. 备份**

```bash
sudo cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb \
        /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb.bak-$(date +%Y%m%d-%H%M%S)
sudo mkdir -p /root/dtb-backup
sudo cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb /root/dtb-backup/
```

**2. 替换两个属性**

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
print("匹配 cooling-levels: %d 处，temp-trips: %d 处" % (n1, n2))
if n1 != 1 or n2 != 1:
    sys.exit("中止：目标属性未唯一匹配，文件未修改")

text = re.sub(pat_levels, "cooling-levels = <%s>;" % NEW_LEVELS, text)
text = re.sub(pat_trips,  "rockchip,temp-trips = <%s>;" % NEW_TRIPS, text)

open(DTS, "w", encoding="utf-8").write(text)
print("已修改")
EOF

sudo python3 /tmp/fix_fan.py
```

**3. 编译并核对**

```bash
sudo dtc -I dts -O dtb /tmp/rk3588-orangepi-5-ultra.dts -o /tmp/new.dtb
sudo dtc -I dtb -O dts /tmp/new.dtb -o /tmp/check.dts
grep -E 'cooling-levels|rockchip,temp-trips' /tmp/check.dts
diff /tmp/rk3588-orangepi-5-ultra.dts /tmp/check.dts    # 应只有这 2 行不同

sudo cp /tmp/new.dtb /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
sync
```

先编译到 `/tmp` 再覆盖，可避免编译失败时在 `/boot` 留下半截文件。

**4. 重启**

```bash
sudo reboot
```

---

## 五、修改 PWM 频率

`pwms` 的第三个数字是周期（ns），`周期 = 1e9 / 频率(Hz)`。当前 `0x1312d00` = 20,000,000 ns = 50 Hz。

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

```bash
sudo ./scripts/fan-curve.py --set "0:0,65:160,80:255" --freq 100 --apply
```

或手工替换：

```bash
sudo sed -i 's/pwms = <0x203 0x00 0x1312d00 0x00>;/pwms = <0x203 0x00 0x989680 0x00>;/' \
        /tmp/rk3588-orangepi-5-ultra.dts
```

> 改频率收益有限。这块板子用 NMOS 通断控制风扇两端电压，只能粗略调速，与真正的三线（PWM）/四线（带测速）风扇不是一回事。

---

## 六、运行时控制与 `pwm1_enable`

先定位 sysfs 路径（hwmon 编号每次开机可能变化，不要写死）：

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
cat "$FAN/pwm1"          # 当前 PWM 0..255
cat "$FAN/pwm1_enable"   # 控制模式
```

驱动中的枚举：

```c
enum pwm_fan_enable_mode {
	pwm_off_reg_off,          /* 0 */
	pwm_disable_reg_enable,   /* 1 */
	pwm_enable_reg_enable,    /* 2 */
	pwm_disable_reg_disable,  /* 3 */
};
```

| 值 | 行为 |
|---|---|
| **0** | PWM 与风扇供电都关闭，且 `__set_pwm()` 对任何大于 0 的 PWM 直接返回，**后续写 `pwm1` 无效，温控也不会再启动风扇**（真正的禁用） |
| **1** | **驱动 probe 时的默认值**：PWM 输出关闭、供电保持 |
| **2** | PWM 与供电都使能 |
| **3** | 两者都关，驱动卸载时内部使用 |

### 没有「自动 / 手动」开关

温度控制由驱动注册的 notifier 完成，温度变化时**直接调用 `set_pwm()`**，不看 `pwm1_enable` 是 1 还是 2：

```c
ret = pwm_fan_register_thermal_notifier(dev, ctx);
```

所以在 1、2、3 模式下，手动 `echo 180 > pwm1` 会立即生效，但**下一次温度变化就会把值覆盖回曲线对应的档位**。

另外，只要 DTB 里有 `rockchip,temp-trips`，驱动注册完 notifier 就直接返回，不会再把 `pwm-fan` 注册成 thermal cooling device：

```c
if (IS_REACHABLE(CONFIG_ROCKCHIP_SYSTEM_MONITOR) &&
    of_find_property(dev->of_node, "rockchip,temp-trips", NULL)) {
	ret = pwm_fan_register_thermal_notifier(dev, ctx);
	...
	return 0;                       /* 跳过 cooling device 注册 */
}
```

因此 `ls /sys/class/thermal/cooling_device*` 里看不到 `pwm-fan`。

### 需要稳定的手动控制时

把 DTB 里的 `rockchip,temp-trips` 整个删掉。驱动会改走 cooling device 分支，而本机没有任何 thermal zone 的 `cooling-map` 引用 `pwm-fan`（`/pwm-fan` 的 `phandle` 是 `0x4c8`，`cooling-maps` 引用的只有 `0x6/0xa/0xc/0x6c`），温度变化不再影响转速：

```bash
FAN=$(dirname "$(find /sys/devices/platform/pwm-fan/hwmon -name pwm1 | head -1)")
sudo tee "$FAN/pwm1" <<< 180
```

### 临时关闭风扇

```bash
sudo tee "$FAN/pwm1_enable" <<< 0
```

重启后恢复。

---

## 七、验证是否生效

不要只看重启成功。**唯一可靠的判据是运行中的 DTB**：

```bash
od -A n -t x1 /proc/device-tree/pwm-fan/cooling-levels
od -A n -t x1 /proc/device-tree/pwm-fan/rockchip,temp-trips
```

`cooling-levels` 应为 7 个 32 位大端整数：

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

若 `/proc/device-tree` 里仍是旧值，说明改错了文件，回到第一节核对路径。

负载测试：

```bash
for i in $(seq $(nproc)); do timeout 180 yes > /dev/null & done
while :; do
    echo "$(cat /sys/class/thermal/thermal_zone0/temp) mC  pwm=$(cat $FAN/pwm1)"
    sleep 3
done
# Ctrl-C 结束，记得 pkill yes
```

---

## 八、已知现象与限制

**1. 阈值附近会周期性启停**

驱动无回滞。若第一档跨度较大（例如 0 → 160），且满载温度恰好稳定在阈值附近，就会出现「停约 10 秒 → 转约 35 秒」的反复。

缓解方式：

- 降低第一档 PWM，让温度不容易被一下压回阈值以下：
  ```bash
  sudo ./scripts/fan-curve.py --set "0:0,65:80,68:125,71:170,74:210,77:240,80:255" --apply
  ```
- 把第一个触发点抬高，让日常负载碰不到阈值。
- 保留一个低速常转档位，彻底消除启停，代价是不再完全静音。

**2. 开机瞬间满速一下**

驱动 probe 时先无条件 `set_pwm(MAX_PWM)`，要等第一次温度事件才被 notifier 纠正。属正常现象。

**3. 升级内核或 dtb 包可能覆盖改动**

`/boot/dtb-<版本>/` 归 `linux-dtb-current-rockchip-rk3588` 包管理，`apt upgrade` 会覆盖；`/boot/dtb/` 不归任何包，但内核升级时可能被重新同步。升级后检查 `/proc/device-tree` 确认。

**4. 50°C 以下不常转**

`state` 初值为 0，默认曲线在 50°C 以下即为停转。

---

## 九、回滚

```bash
sudo ./scripts/fan-curve.py --restore

# 或手工
sudo cp /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb.bak-<时间戳> \
        /boot/dtb/rockchip/rk3588-orangepi-5-ultra.dtb
sudo reboot
```

若改坏导致无法开机，只能从外部恢复：把 NVMe 接入 USB 硬盘盒或从 SD 卡启动，挂载启动分区（vfat）后把备份改回原名；或重新烧写镜像。**因此备份不要只存在 `/boot` 里。**

---

## 注意事项

1. 改前备份，且备份要放到 rootfs 或板子以外。
2. 认准 `/boot/dtb/`。
3. `temp-trips` 按温度升序，最大档位不得超过 `cooling-levels` 个数 - 1。
4. 频率不是越高越好；这块板子的调速方式本身较粗。
5. 共振与噪声通常需要机械方案（加电容、减震垫）解决。

---

变更历史见 [CHANGELOG.md](CHANGELOG.md)。
