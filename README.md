# H3C_MAGIC_RT3000

Updated firmware for the H3C Magic RT3000 HW-B from China Mobile to OpenWRT 21.02.7 QSDK 11.5

## OpenWRT 21.02.7 based on QSDK 11.5 with Hardware acceleration

Go here: 

- [https://github.com/FlyFish-go/H3C-RT3000-Product-R1](https://github.com/FlyFish-go/H3C-RT3000-Product-R1)
- [https://github.com/FlyFish-go/H3C-NX30-Uboot](https://github.com/FlyFish-go/H3C-NX30-Uboot)

The steps are:

### 1. Activate telnet

Download the current firmware by login into the router, on the bottom you have two bottons:

- 简化版 (Basic Mode) — currently selected (grayed out)
- 专业版 (Pro/Advanced Mode) — select this one

On the left side in the menu, click the lowest button 设备管理 — Device Management. On the right side you have four tabs, the left one is active with 配置管理 — Configuration Management. Below you have 3 separate areas:

- 备份系统设置信息 — Backup System Settings
  - Button: 备份 (Backup) — This will download the plaintext configuration file RT3000.cfg
- 从文件中恢复设置信息 — Restore Settings from File
   - Button: 恢复 (Restore) — You will have to upload the fixed configuration here later
- 恢复到出厂设置 — Restore Factory Defaults
  - Button: 复原 (Factory Reset)

Download the RT3000.cfg and change line 31 to **enable** telnet, so it reads:

```cfg
@telnet
#
	telnetenable=enable
```

You can then login into the router over telnet to the IP of the router on port 23 with `telnet 192.168.10.1 23`. You get the message:

```
Connected to 192.168.10.1.
Escape character is '^]'.

 === IMPORTANT ============================
  Use 'passwd' to set your login password!
 ------------------------------------------

**************************************************************************
*    Copyright (c) 2004-2022 New H3C Technologies Co.,Ltd.               *
*    All rights Reserved.                                                *
*    Without the owner's prior written consent,                          *
*    no decompiling or reverse-engineering shall be allowed.             *
**************************************************************************

<H3C_RT3000>debugshell
```
You have to enter `debugshell` to enter the shell. You will be greeted by the old QSDK 4.4:

```
<H3C_RT3000>debugshell


BusyBox v1.30.1 () built-in shell (ash)

     MM           NM                    MMMMMMM          M       M
   $MMMMM        MMMMM                MMMMMMMMMMM      MMM     MMM
  MMMMMMMM     MM MMMMM.              MMMMM:MMMMMM:   MMMM   MMMMM
MMMM= MMMMMM  MMM   MMMM       MMMMM   MMMM  MMMMMM   MMMM  MMMMM'
MMMM=  MMMMM MMMM    MM       MMMMM    MMMM    MMMM   MMMMNMMMMM
MMMM=   MMMM  MMMMM          MMMMM     MMMM    MMMM   MMMMMMMM
MMMM=   MMMM   MMMMMM       MMMMM      MMMM    MMMM   MMMMMMMMM
MMMM=   MMMM     MMMMM,    NMMMMMMMM   MMMM    MMMM   MMMMMMMMMMM
MMMM=   MMMM      MMMMMM   MMMMMMMM    MMMM    MMMM   MMMM  MMMMMM
MMMM=   MMMM   MM    MMMM    MMMM      MMMM    MMMM   MMMM    MMMM
MMMM$ ,MMMMM  MMMMM  MMMM    MMM       MMMM   MMMMM   MMMM    MMMM
  MMMMMMM:      MMMMMMM     M         MMMMMMMMMMMM  MMMMMMM MMMMMMM
    MMMMMM       MMMMN     M           MMMMMMMMM      MMMM    MMMM
     MMMM          M                    MMMMMMM        M       M
       M
 ---------------------------------------------------------------
   For those about to rock... (Chaos Calmer, unknown)
 ---------------------------------------------------------------
root@OpenWrt:/#
```

### Determine your hardware version and flash type

Next you get more information of the specifics of your machine. Enter

```
cat /proc/cpuinfo
dmesg | grep -iE "ipq|nand|spi|switch|qca|rtl"
cat /proc/mtd
fw_printenv
```

You need the correct firmware and flashchip to later write the correct image.

To be continued ...

## Original Firmware

![screenshot](docs/landing_page.jpg)

### 基本信息
```
序列号：		219801A2W37229K52S89
产品型号：		H3C Magic RT3000
软件版本：		RT3000V100R005L01 （点击进入软件升级页面）
Bootrom版本：		100
硬件版本：		VER.A
产品CMEI：		111060810936737
系统资源：		CPU使用：15.0%   内存使用：58.7%
运行时间：		0 天 2 小时 48 分钟 13 秒
系统时间：		2026 年 11 月 26 日 星期四 20:57:31  [网络获取时间]
```

### 端口状态

```
LAN1	LAN2	LAN3	WAN	
未连接	未连接	100M 全双工	1000M 全双工
```

### WAN网口状态

```
WAN网口：		WAN
连接方式：		
DHCP		
链路状态：		已连接
IP地址：		192.168.18.8
子网掩码：		255.255.255.0
网关地址：		192.168.18.1
主DNS服务器：		1.1.1.1
辅DNS服务器：		8.8.8.8
DHCP剩余时间：		00:43:18
MAC地址：		10:9F:4F:5C:A0:99
```

### IPv6

```
IPv6 已关闭		
    自动刷新：
```

## Firmware

If you login via telnet you will be surprized that both the firmware is based on OpenWRT and on the other hand 11 years old!

```
<H3C_RT3000>debugshell

BusyBox v1.30.1 () built-in shell (ash)

     MM           NM                    MMMMMMM          M       M
   $MMMMM        MMMMM                MMMMMMMMMMM      MMM     MMM
  MMMMMMMM     MM MMMMM.              MMMMM:MMMMMM:   MMMM   MMMMM
MMMM= MMMMMM  MMM   MMMM       MMMMM   MMMM  MMMMMM   MMMM  MMMMM'
MMMM=  MMMMM MMMM    MM       MMMMM    MMMM    MMMM   MMMMNMMMMM
MMMM=   MMMM  MMMMM          MMMMM     MMMM    MMMM   MMMMMMMM
MMMM=   MMMM   MMMMMM       MMMMM      MMMM    MMMM   MMMMMMMMM
MMMM=   MMMM     MMMMM,    NMMMMMMMM   MMMM    MMMM   MMMMMMMMMMM
MMMM=   MMMM      MMMMMM   MMMMMMMM    MMMM    MMMM   MMMM  MMMMMM
MMMM=   MMMM   MM    MMMM    MMMM      MMMM    MMMM   MMMM    MMMM
MMMM$ ,MMMMM  MMMMM  MMMM    MMM       MMMM   MMMMM   MMMM    MMMM
  MMMMMMM:      MMMMMMM     M         MMMMMMMMMMMM  MMMMMMM MMMMMMM
    MMMMMM       MMMMN     M           MMMMMMMMM      MMMM    MMMM
     MMMM          M                    MMMMMMM        M       M
       M
 ---------------------------------------------------------------
   For those about to rock... (Chaos Calmer, unknown)
 ---------------------------------------------------------------
root@OpenWrt:/tmp/backup# uname -a
Linux OpenWrt 4.4.60 #1 SMP PREEMPT Sat Aug 20 13:36:05 CST 2022 armv7l GNU/Linux
```

Yes, Chaos Calmer is from Septemebr 2015 for OpenWRT 15.05. At least QSDK 4.4 with Kernel 4.460 is a little younger. 
