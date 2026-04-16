# OrangePi 5 Ultra 风扇控制修改指南

## 概述
本人使用板子上的可调速风扇时，发现其风扇控制逻辑较为简单，不符合实际风扇使用需求，遂自行（使用AI工具）探究如何控制风扇

本文档介绍如何修改 OrangePi 5 Ultra (Rockchip RK3588) 的风扇控制逻辑，包括转速曲线（PWM值与温度阈值）和控制频率。

本教程可能同样适用于OrangePi 5、OrangePi 5B、OrangePi 5 Pro、OrangePi 5 Plus 和OrangePi 5 MAX等使用RK3588、RK3588S处理器的开发板，有待广大网友验证

我的环境为OrangePi 5 Ultra，搭配香橙派官方的Ubuntu22桌面版（6.1.43内核，官方名称为Orangepi5ultra 1.0.0 ubuntu jammy_desktop xfce linux6.1.43.7z）镜像

本文几乎全部由Minimax生成，仅部分内容为人为添加或修改，我已验证下述代码可以有效修改转速曲线，使用其他开发板或其它系统请按自身环境进行修改

注意：修改设备树文件可能造成系统损坏，请备份好重要文件和原设备树文件

---

## 准备工作

### 工具
- `dtc` (Device Tree Compiler) - 通常已预装
- `sudo` 权限

### 文件位置
- **DTB文件**: `/boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb`
- **备份文件**: `/boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb.bak`

---

## 修改过程（若您刚刚接触Linux，可参照完整修改流程示例）

### 1. 备份原始DTB
```bash
sudo cp /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb.bak
```

### 2. 解包 DTB 为 DTS
```bash
sudo dtc -I dtb -O dts /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb -o /tmp/rk3588-orangepi-5-ultra.dts
```

### 3.修改风扇转速曲线或控制频率（详见下一小节）

### 4. 编译 DTS 为 DTB
```bash
sudo dtc -I dts -O dtb /tmp/rk3588-orangepi-5-ultra.dts -o /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb
```

### 5. 重启生效
```bash
sudo reboot
```

### 6*. 关于恢复备份
```bash
sudo cp /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb.bak /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb && sudo reboot
```

---

## 修改风扇转速曲线

### 查看当前配置
```bash
grep -A 6 "pwm-fan" /tmp/rk3588-orangepi-5-ultra.dts | head -8
```
执行上述代码后，您会看到如下输出（下面只贴出部分有用信息）
```bash
orangepi@orangepi5ultra:~$ grep -A 20 "pwm-fan" /tmp/rk3588-orangepi-5-ultra.dts
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

### 配置格式说明

**cooling-levels** - PWM值数组：
```
cooling-levels = <0x00 0x32 0x64 0x96 0xc8 0xff>;
```
| Level | 十六进制 | 十进制 | PWM占比 |
|-------|---------|--------|--------|
| 1 | 0x00 | 0 | 0% |
| 2 | 0x32 | 50 | 20% |
| 3 | 0x64 | 100 | 40% |
| 4 | 0x96 | 150 | 60% |
| 5 | 0xc8 | 200 | 80% |
| 6 | 0xff | 255 | 100% |

**rockchip,temp-trips** - 温度阈值数组（毫氏度）：
```
rockchip,temp-trips = <0xc350 0x01 0xd6d8 0x02 0xea60 0x03 0xfde8 0x04 0x11170 0x05>;
```
| 温度(°C) | 十六进制 | Level |
|---------|---------|-------|
| 50 | 0xc350 | 1 |
| 55 | 0xd6d8 | 2 |
| 60 | 0xea60 | 3 |
| 65 | 0xfde8 | 4 |
| 70 | 0x11170 | 5 |

我想让风扇在较低负载下不转动，在高负载时进行有效散热，因此下面给出的控制逻辑是，风扇在65℃以下不转，65℃时以160（十进制，约满转的62%）进行散热，然后每3℃速度加20，直到80或高于80℃，风扇满转（255）

### 示例：修改为用户配置（65°C起转，每3°C递增20）

```python
sudo tee /tmp/fix_fan.py << 'EOF'
import re
f=open("/tmp/rk3588-orangepi-5-ultra.dts","r");c=f.read();f.close()
# 修改cooling-levels: Level 0=0, Level 1=160, Level 2=180...
c=re.sub(r"cooling-levels = <0x00 0x32 0x64 0x96 0xc8 0xff>;","cooling-levels = <0x00 0xa0 0xb4 0xc8 0xdc 0xf0 0xff>;",c)
# 修改rockchip,temp-trips: 0°C=Level0, 65°C=Level1, 68°C=Level2...
c=re.sub(r"rockchip,temp-trips = <0xc350 0x01 0xd6d8 0x02 0xea60 0x03 0xfde8 0x04 0x11170 0x05>;","rockchip,temp-trips = <0x0000 0x00 0xfe18 0x01 0x10a38 0x02 0x11578 0x03 0x120b8 0x04 0x12bf8 0x05 0x13880 0x06>;",c)
f=open("/tmp/rk3588-orangepi-5-ultra.dts","w");f.write(c);f.close();print("Done")
EOF

sudo python3 /tmp/fix_fan.py
```
执行上述程序并返回Done后，可编译设备树文件并启用，重启即可

若上述指令执行失败，可用nano等工具创建python文件后手动执行

### 常用温度阈值换算

| 温度(°C) | 十六进制 (m°C) |
|---------|---------------|
| 50 | 0xc350 |
| 55 | 0xd6d8 |
| 60 | 0xea60 |
| 65 | 0xfe18 |
| 68 | 0x10a38 |
| 70 | 0x11170 |
| 71 | 0x11578 |
| 74 | 0x120b8 |
| 75 | 0x124f8 |
| 77 | 0x12bf8 |
| 80 | 0x13880 |
| 85 | 0x14a38 |

---

## 修改控制频率

### 查看当前频率

```bash
grep -n "pwms" /tmp/rk3588-orangepi-5-ultra.dts | head -10
```

当前配置：`pwms = <0x203 0x00 0x1312d00 0x00>;`

周期值 `0x1312d00` = 20,000,000 ns = **50 Hz**

### 频率换算

| 频率 | 周期(ns) | 十六进制 |
|------|---------|---------|
| 25 Hz | 40,000,000 | 0x2625A00 |
| 50 Hz | 20,000,000 | 0x1312d00 |
| 100 Hz | 10,000,000 | 0x989680 |
| 200 Hz | 5,000,000 | 0x4C4B40 |
| 500 Hz | 2,000,000 | 0x1E8480 |
| 1 kHz | 1,000,000 | 0xF4240 |
| 5 kHz | 200,000 | 0x30D40 |
| 10 kHz | 100,000 | 0x186A0 |

### 示例：修改为100Hz

```python
sudo tee /tmp/fix_freq.py << 'EOF'
import re
f=open("/tmp/rk3588-orangepi-5-ultra.dts","r");c=f.read();f.close()
c=re.sub(r"pwms = <0x203 0x00 0x1312d00 0x00>;","pwms = <0x203 0x00 0x989680 0x00>;",c)
f=open("/tmp/rk3588-orangepi-5-ultra.dts","w");f.write(c);f.close();print("Done")
EOF
sudo python3 /tmp/fix_freq.py
```

注，修改控制频率对风扇控制几乎没有影响，因为OrangePi采用的是控制放电NMos管开关实现控制风扇两端电压的方式，这种方式只能粗略控制双线风扇，完全无法与三线（带PWM控制）和四线（带转速反馈）的风扇相媲美

---

## 临时控制风扇（重启后失效）

### 切换模式
```bash
# 手动模式
sudo tee /sys/devices/platform/pwm-fan/hwmon/hwmon7/pwm1_enable <<< 1

# 自动温控模式
sudo tee /sys/devices/platform/pwm-fan/hwmon/hwmon7/pwm1_enable <<< 2

# 禁用风扇
sudo tee /sys/devices/platform/pwm-fan/hwmon/hwmon7/pwm1_enable <<< 0
```

### 设置转速（PWM 0-255）
```bash
sudo tee /sys/devices/platform/pwm-fan/hwmon/hwmon7/pwm1 <<< 180
```

---

## 完整修改流程示例

将风扇配置修改为：
- < 65°C: 停止
- 65-80°C: 每3°C递增20 PWM
- ≥ 80°C: 全速
- 频率: 50Hz

```bash
# 1. 解包
sudo dtc -I dtb -O dts /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb -o /tmp/rk3588-orangepi-5-ultra.dts

# 2. 修改转速曲线
sudo tee /tmp/fix_fan.py << 'EOF'
import re
f=open("/tmp/rk3588-orangepi-5-ultra.dts","r");c=f.read();f.close()
c=re.sub(r"cooling-levels = <0x00 0x32 0x64 0x96 0xc8 0xff>;","cooling-levels = <0x00 0xa0 0xb4 0xc8 0xdc 0xf0 0xff>;",c)
c=re.sub(r"rockchip,temp-trips = <0xc350 0x01 0xd6d8 0x02 0xea60 0x03 0xfde8 0x04 0x11170 0x05>;","rockchip,temp-trips = <0x0000 0x00 0xfe18 0x01 0x10a38 0x02 0x11578 0x03 0x120b8 0x04 0x12bf8 0x05 0x13880 0x06>;",c)
f=open("/tmp/rk3588-orangepi-5-ultra.dts","w");f.write(c);f.close();print("Done")
EOF
sudo python3 /tmp/fix_fan.py

# 3. 编译安装
sudo dtc -I dts -O dtb /tmp/rk3588-orangepi-5-ultra.dts -o /boot/dtb-6.1.43-rockchip-rk3588/rockchip/rk3588-orangepi-5-ultra.dtb

# 4. 重启
sudo reboot
```

---

## 注意事项

1. **修改前先备份**：避免配置错误导致系统无法启动
2. **频率不是越高越好**：低占空比时高频率可能导致风扇驱动响应变差
3. **共振问题**：风扇在特定转速可能与外壳产生共振，通常需要机械方案（如加电容）解决
4. **测试建议**：先用临时命令测试不同PWM值，找到最佳配置后再修改DTB
