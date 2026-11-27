# Shanling Q2 hardware

This is the reference for the Q2's chip, board wiring and power figures. The
boot ROM, loader, flash map and watchdog are in [boot-chain.md](boot-chain.md).
What the stock firmware does with the hardware is in
[stock-firmware.md](stock-firmware.md).

## Sources and levels

Every fact carries one source and one level.

| Level | Meaning |
| --- | --- |
| device-verified | Read or measured on a Q2 |
| emulator | Shown only in the QEMU model |
| datasheet | From a manufacturer's datasheet, manual or application note |
| vendor source | From Ingenic SDK, Realtek or Linux driver source, not checked on a Q2 |
| stock firmware | From static analysis of the stock firmware (kernel, device tree, modules, `demo`), not run on a Q2 |
| host | Shown by a host test with generated fixtures, not run on a Q2 |
| unverified | Stated by a source that was not checked |

Sources named in the tables:

- **Stock V1.32**: Shanling's V1.32 firmware package. Its kernel image carries
  the device tree and symbol table; its root holds `/module_driver/*.ko`, the
  loader scripts and the `demo` player.
- **Rockbox**: the Q2 Rockbox port (`firmware/target/hosted/shanling`,
  `tools/shanlingq2/README`), which runs hosted on the stock kernel.
- **Q2 Pod**: the Q2 Pod project's `docs/`.
- **q2-librespot**: `contrib/shanlingq2` of q2-librespot.
- **Ingenic SDK**: Ingenic's X2000 Linux 5.10 SDK kernel, including
  `ingenic_sfc_v2`, `soc-x2000/include/soc/sfc.h`, `cpm.h` and the stock
  V9.1S device tree published with it.
- **Linux**: the upstream Linux 7.3 tree (`drivers/clk/ingenic/x2000-cgu.c`,
  `arch/mips/boot/dts/ingenic/x2000.dtsi`).
- Datasheets: Cirrus Logic CS43131 DS1155F2; Dosilicon DS35X1GAXXX Rev.03;
  X-Powers AXP2101 V1.4; Ingenic X2000 product brief; Ingenic XBurst2 CPU
  core manual; Ingenic X1000 programming manual (for the UART, whose register
  map the X2000 driver matches); Ingenic application notes "The X2000's Low
  Power Potentials" and "X2000's Three CPUs".

Power figures are from a USB-input meter that has no calibration record.
Treat absolute values as uncalibrated; differences measured in one session
are more reliable than absolute levels.

## SoC and CPU

| Fact | Source | Level |
| --- | --- | --- |
| Ingenic X2000; Linux reports `ingenic,x2000` and two `Ingenic XBurst II V0.0 FPU V0.0` CPUs, both with MSA | `/proc/cpuinfo` under the stock kernel | device-verified |
| The two Linux CPUs are SMT threads of one XBurst2 core; taking CPU1 offline does not change power | X2000 product brief and "Three CPUs" note; power comparison | datasheet |
| A third core, XBurst0, runs at 240 MHz with 32 KiB of shared memory. Linux exposes only CPUs 0 and 1 | "Three CPUs" note; CPU inventory | datasheet |
| L1 instruction and data caches: 32 KiB each, 8 ways, 32-byte lines | XBurst2 core manual | datasheet |
| L1D line is 32 bytes: one miss per 32-byte stride | PMU event 4 | device-verified |
| L2: Config2 SS=3 (512 sets), SL=5 (64-byte lines), SA=15. MIPS leaves SA=15 reserved; 16 ways gives the 512 KiB that the SDK cache sequence flushes | CP0 read; Ingenic SDK | device-verified (Config2), vendor source (ways) |
| User-space ABI: MIPS32r2, FP64, NaN-2008. The stock root uses glibc 2.28 through `/lib/ld-linux-mipsn8.so.1` | q2-librespot; stock `demo` ELF flags | stock firmware |
| A load that misses L1 and L2 costs about 18 to 21 cycles with sequential prefetch; independent ALU operations dual-issue (11 instructions in 5 cycles); a dependent `addu` chain runs one per cycle | PMU counts | device-verified |

CP0 words on CPU0, read under the stock kernel with a read-only probe module
(device-verified):

| Register | Value |
| --- | --- |
| PRId | `0x00132000` |
| Config0 | `0x80000603` |
| Config1 | `0xbe6733db` |
| Config2 | `0x8000235f` |
| Config3 | `0x90003020` |
| Config4 | `0xe0fcc126` |
| Config5, Config7 | `0` |

### Memory

| Fact | Source | Level |
| --- | --- | --- |
| 128 MiB LPDDR3; the loader names the part `W63AH6NKB_BI` | SPL strings; kernel command line `mem=128M@0x0` | device-verified |
| Linux reports `MemTotal` 117,884 kB under the stock kernel | `/proc/meminfo` | device-verified |
| DDR runs at 750 MHz from MPLL | `clk_summary`, DDRCDR `0xa0000001` | device-verified |

### Clock tree as booted

| Fact | Source | Level |
| --- | --- | --- |
| APLL 1200 MHz (CPAPCR `0x03100869`: M 50, N 1, OD 2) feeds only the CPU (1200 MHz) and the L2 cache (600 MHz) | `clk_summary`; SPL PLL words; Linux `x2000-cgu.c` parents | device-verified |
| MPLL 1500 MHz (CPMPCR `0x07c04849`: M 125, N 2, OD 2) feeds DDR 750 MHz, AHB0 and AHB2 300 MHz, PCLK 150 MHz, MSC1 and MSC2, SFC, PWM and the LCD pixel clock | `clk_summary`; CPCCR `0x9a494410`, DDRCDR `0xa0000001` | device-verified |
| EPLL 300 MHz feeds only the I2S1 master clock (22,579,195 Hz), on while a stream is open | `clk_summary` | device-verified |
| UART baud clock, OST, TCU, watchdog, USB OTG and PHY run from the 24 MHz crystal (EXCLK); I²C runs from PCLK | Linux `x2000-cgu.c` | vendor source |
| CPCCR fields: CDIV 3:0 = 0, L2CDIV 7:4 = 1, H0DIV 11:8 = 4, H2DIV 15:12 = 4, PDIV 19:16 = 9, change-enable bits 22, 21, 20, sources 31:24. The SPL writes `0x9a094410` (bit 22 clear); after a resume the word reads `0x9a494410` | Linux `x2000-cgu.c`; SPL; register read | device-verified |
| CPU divider change: clear CPCCR bit 22, write CDIV, set bit 22, wait for CPCSR (`0xd4`) bit 0 to clear | Linux `pm-ingenic-x2000.c` | vendor source |
| CLKGR0 bit 29 gates AHB0, the shared system fabric. Gating it while the DSI clock drops its last reference stalls the system; the AHB0 gate must stay on | Ingenic SDK `clk-x2000.c` (separate rate and gate clocks); display trials | device-verified |
| Stock operating points 1200, 750, 600, 500, 400, 375, 300 and 200 MHz, each listed at 0.9 V; the core rail (AXP2101 DCDC4) is fixed at 0.95 V | stock device tree (`ingenic,x2000-cpufreq`, `axp2101-dcdc4`) | stock firmware |

Not known: CPCSR bits 31:28 as mux-settled flags (the SPL polls them); the
APLL lock time; CPCCR bit 22 on a fresh boot; the values of MSC0CDR, CIMCDR,
RSACDR, LPCDR, PWMCDR, SFCCDR, MSC1CDR, MSC2CDR and I2S1CDR; CPAPCR bits 10:0
(`0x069` on APLL, `0x049` on MPLL); any rate above 1200 MHz; the datasheet's
frequency and core-voltage limits; which temperature channels exist.

### Performance counters

Each CPU has two 32-bit counters; the overflow interrupt is CPU interrupt
line 6 (IntCtl.IPPCI). Event numbers follow the Ingenic SDK; the meanings
below were measured with known loops in user mode (device-verified).

| Event | Counter | Counts |
| --- | --- | --- |
| 0 | either | Core cycles (1.2 GHz) |
| 1 | either | Instructions, excluding branches and their delay slots |
| 2 | even / odd | Conditional branches / mispredicted branches |
| 4 | even / odd | L1D loads / misses |
| 5 | even / odd | L1D stores / misses |
| 9 | even / odd | dTLB loads / misses |
| 7, 8 | both | Not per fetch (66 K "accesses" for 50 M loop iterations); meaning unknown |

Raw events are 7 bits; bit 7 selects the odd counter. Instructions retired
are about event 1 + 2 × event 2 for code without filled delay slots; event 1
+ event 2 is a lower bound. A per-CPU event stops when its CPU goes offline.
Kernel call-chain sampling (`perf record -g`) in the PMU interrupt resets the
Q2 through the watchdog within seconds; flat sampling runs for hours without
a fault (device-verified).

## Storage

### SPI NAND

| Fact | Source | Level |
| --- | --- | --- |
| Dosilicon DS35Q1GA. READ ID returns E5h 71h (the buffer repeats `e5:71:e5:71`) | read-only MTD preflight; DS35X1GA Table 3.2 | device-verified |
| (2048 + 64)-byte pages, 64 pages per block, 1024 blocks: 128 MiB, 128 KiB eraseblocks, one LUN, one plane. The plane-select address bit applies only to the 2 Gb parts | MTD master size `0x08000000`; DS35X1GA Features, Table 3.3, Figure 3.8 | device-verified |
| 3.3 V part (2.7 to 3.6 V) | DS35X1GA §1.1 | datasheet; the board supply is not checked |
| Commands: 0Fh, 1Fh, 06h, 04h, 13h, 03h/0Bh, 3Bh, 6Bh, 02h, 32h, 84h, 34h, 10h, D8h, 9Fh, FFh. No cache read (30h, 31h, 3Fh), no continuous read, no dual or quad I/O read (BBh, EBh) | DS35X1GA Table 1.3, §3.3 | datasheet |
| Page read: PAGE READ 13h (8 dummy bits and a 16-bit row), GET FEATURE C0h until OIP = 0, then READ FROM CACHE | DS35X1GA §3.3 | datasheet |
| READ FROM CACHE x4 (6Bh): command on SI, 16 address clocks (3 dummy bits, plane bit, 12-bit column), 8 dummy clocks, data on four lines; needs QE (B0h bit 0). Figure 3.8 labels the opcode 8Bh; the table and text say 6Bh | DS35X1GA Table 1.3, Figure 3.8 | device-verified (1-1-4 reads) |
| tR with internal ECC 60 µs min, 70 µs max; without ECC 25 µs max | DS35X1GA Table 4.6, Table 3.3 | datasheet |
| fC 104 MHz; tCS (CS# deselect) 100 ns min; tWH, tWL 4 ns; tSLCH, tCHSH 5 ns; tSUDAT, tHDDAT 3.5 ns; tV 8 ns max; tHO 1 ns min; 30 pF load. Inputs latch on the rising edge, outputs change after the falling edge (modes 0 and 3) | DS35X1GA Tables 4.4 and 4.7, §2.1 | datasheet |
| Feature registers: A0h block lock (BRWD, BP2 to BP0, INV, CMP); B0h OTP_PRT, OTP_EN, ECC enable (bit 4), QE (bit 0); C0h status (ECC_S1:0 bits 5:4, P_Fail, E_Fail, WEL, OIP); D0h drive strength DS_IO1:0 (bits 6:5, encoding not given) | DS35X1GA Tables 3.1 and 3.6 | datasheet |
| ECC status: 00b none, 01b 1 to 4 bits corrected, 10b uncorrectable | DS35X1GA Table 3.6 | datasheet |

A 6Bh read of a 2,048-byte page takes 4,128 SPI clocks: 165 µs at 25 MHz,
44 µs at 93.75 MHz. A page read cannot overlap the transfer of the previous
page.

### SFC (SPI flash controller)

| Fact | Source | Level |
| --- | --- | --- |
| Six pins, PE16 to PE21 (clock, chip select, four data lines); interrupt 7 | stock V9.1S device tree `sfc` node and `sfc-pe` pins | stock firmware |
| Registers: `SFC_DEV0_STA_RT` 0x0c, `SFC_MEM_ADDR` 0x60, `SFC_STA_ADDR0` 0x88, `SFC_DES_ADDR` 0x90 | Ingenic SDK `sfc.h` | vendor source |
| GLB0: POLL_TIME 31:16, DES_EN 15, CDT_EN 14, THRESHOLD 12:7, OP_MODE 6 (1 = DMA), BURST_MD 1:0 | Ingenic SDK `sfc.h` | device-verified (CDT_EN, THRESHOLD); vendor source (others) |
| DMA descriptor `{ next_des_addr, mem_addr, tran_len, link }` with physical addresses; `link` 0 ends the chain. A DMA transfer sets OP_MODE 1, MEM_ADDR 0 and DES_ADDR; the vendor init sets DES_EN, burst mode 2 and poll time 0x10. The vendor DMAs whole 32-bit words and reads the remainder by PIO | Ingenic SDK `sfc_desc`, `sfc_sync_cdt_raw`, `ingenic_sfcnand_read` | vendor source |
| Command-table word 1 bit 25 POLL_EN repeats the command until `(status & word3) == word2` | Ingenic SDK `sfc.h` MK_ST, `params_to_cdt` | vendor source |
| NAND cache reads use 6Bh, 1-1-4 (CDT mode 5), 8 dummy bits | Ingenic SDK `nand_common.h` | device-verified (mode 5) |
| DEV_CONF SMP_DELAY 20:16: 0 to 7 = 0 to 315 degrees in 45-degree steps; THOLD 12:11, TSETUP 10:9, TSH 8:5 in half SPI clocks | Ingenic SDK `sfc.h`, `set_flash_timing` | vendor source |
| SFC clock: mux SCLKA, MPLL or EPLL (SFCCDR 31:30), 8-bit divider. From MPLL the SPI rate is 1500/(4n) MHz: 93.75, 75, 62.5, 53.6, 46.9 … 25 (n = 15). Stock init is 200 MHz CGU, maximum 400 MHz (50 and 100 MHz SPI), sample delay 180 degrees at 200 MHz and above | Linux `x2000-cgu.c`; stock V9.1S device tree; `sfc_clk_set_init` | vendor source; the parent on the device is not read |
| Sample delay: at 75 MHz SPI, delays 1 to 7 read a 29.98 MB image correctly and 0 fails; at 93.75 MHz only 3 to 8 read correctly | read-and-hash sweep | device-verified |
| With TSH at its maximum (15, 8 SPI clocks) CS# stays high 107 ns at 75 MHz and 85 ns at 93.75 MHz, against tCS 100 ns. Reads at or below 80 MHz meet tCS | arithmetic from DEV_CONF and DS35X1GA Table 4.7 | datasheet; not measured on the wire |

### MSC (SD and SDIO)

| Fact | Source | Level |
| --- | --- | --- |
| MSC1 at 0x13460000, interrupt 36, is the Wi-Fi SDIO slot: non-removable, 4-bit, keeps power in suspend, stock maximum 100 MHz | stock `soc_msc.sh` | stock firmware |
| MSC2 is the microSD slot; stock maximum 200 MHz (`msc2_speed=sd_card`). It has no 1.8 V switching GPIO (`msc2_sdr=-1`), so UHS modes may be unreachable | stock `soc_msc.sh` | stock firmware |
| Stock enables SDHCI v4 mode on both controllers | stock device tree | stock firmware |
| MSC2 pins are PE00 to PE05 (function 0); card power is PA12, active high | stock device tree (`msc2-4bit`, `msc2_pwr=PA12`) | device-verified |
| GPIO port A runs in 1.8 V mode: CPM `EXCLK_DS` (+0xe0) bit 30 set, from the stock `ingenic,gpa_voltage = <1>`. With the bit clear, port A pins read wrong (PA12 latched high reads low) and the card does not initialize | stock `ingenic_pinctrl_probe`; pin-level reads | device-verified |
| In SLEEP, switching the MSC2 pins from function 0 with pulls to GPIO inputs without pulls lowers USB-input power from 111.994 and 113.623 mW to 77.856 mW (34 to 36 mW) | matched sleep comparison, three arms | device-verified |

## Display and backlight

| Fact | Source | Level |
| --- | --- | --- |
| Panel 320 × 375, 32 bpp; landscape (x, y) is panel (319 − y, x) | Rockbox (`/dev/fb0`) | device-verified |
| Backlight is PWM channel 0 on PC00. PC00 in its PWM function lights the panel; PC00 as a GPIO output low darkens it | Rockbox; register writes under stock | device-verified |
| With the screen off, PC00 driven high gives no light: the backlight supply is also off | register writes under stock | device-verified |
| Gating the PWM clock (CLKGR1 bit 5) with the channel enabled at zero duty keeps the output low and lowers power by 1.68 mW | eight 60 s gate cycles | device-verified (power); the light was not observed |
| AXP2101 ALDO2 and ALDO4 supply the panel | PMIC rail state across display off and on | device-verified |
| No ambient-light sensor is exposed (no IIO device; empty `/sys/class/backlight` under stock) | read-only inventory | device-verified for exposed devices only |

## Input

| Fact | Source | Level |
| --- | --- | --- |
| Keys (`md-gpio-keys` under stock): 116 centre, 103 Return, 108 Play/Pause, 165 and 163 side keys | Rockbox | device-verified |
| The Previous key is PB16 (GPIO 48), active low; the SPL reads it to select recovery | SPL disassembly; emulator | stock firmware |
| Wheel: Hynitron CST66xx (`hyn_sliderbar`) on I²C2 at 0x15; reset PB20. `ABS_X` runs 1 to 200 round the ring and falls clockwise; `ABS_PRESSURE` 0 on lift; 20 units per tick | Rockbox; stock driver | device-verified (events) |
| Reading the wheel driver's `hynselftest` or `hyndumpfw` sysfs nodes runs driver routines | stock driver | stock firmware |
| Touchscreen: Hynitron CST8xxT (`hyn_ts`) registered on I²C3 at 0x5a; the driver uses 0x6a for its boot protocol and 0x15 for reports; interrupt PA15 (falling); reset PA14; supply AXP2101 ALDO3 at 2.8 V | stock `hyn_touch.ko` and device tree | stock firmware |
| With ALDO3 off, releasing PA14 alone gives no touchscreen response | I²C probe | device-verified |
| Headphone jacks: PA07 is 3.5 mm, PA08 is 4.4 mm; low means plugged | Rockbox (`/dev/gpio` ioctl `0x2000477a`) | device-verified |

## Audio

| Fact | Source | Level |
| --- | --- | --- |
| Four Cirrus CS43131 DACs at 0x30, 0x31, 0x32 and 0x33 on I²C5; the stock ASoC client is 0x31 and the module writes all four directly | stock `codec_cs43131.ko` | stock firmware |
| All four identify as CS43131 revision A1 | codec ID read | device-verified |
| I²C5 uses PD04 and PD05 at 400 kHz; interrupt 64 (INTC line 56) | stock device tree and `i2c` module | stock firmware |
| Board controls: PB01 DAC power enable, PB14 reset, PB10 output mute, PB09 mode, PB02 named `dac_vcpen_gpio` | stock `codec_cs43131.ko` loader | stock firmware |
| Stock probe levels: PB01 low, PB14 high, PB10 high, PB09 low, PB02 low. `dac_pwr(1)` asserts mute, waits 200 ms, raises PB01, waits 50 ms. Init pulses PB14 0, 1, 0 with 20 ms steps | stock `cs43131_probe`, `dac_pwr`, `cs43131_dac_init.part.0` | stock firmware |
| PB02 is raised only in the high-volume branch of `dac_set_volume` (volume 90 and above), not as a codec supply enable; the analog rail it drives is unknown | stock `dac_set_volume.part.0` | stock firmware |
| PCM path control 2 is 5 on 0x30 and 0x31 and 7 on 0x32 and 0x33: copy A to B with B inverted, with A/B swap on the second pair | stock init tables; DS1155F2 §7.5.6 | stock firmware |
| Probe order decides codec power-up. With the codec driver enabling its supplies before the machine driver has set the board lines, the first codec's supply enable drops USB while AXP2101 DCDC5 is on (0x80 = `0x1f`), and clearing DCDC5 (`0x0f`) avoids it. With the machine driver bound first (stock idle levels, 200 ms mute settle), all four codecs probe from a cold start with DCDC5 on. The electrical reason is unknown | PMIC register change and codec probe; ordered bring-up ([PLAYER.md](../os/hardware/PLAYER.md)) | device-verified |
| The analog output stage (four codecs and the board stage) costs about 114 mW at digital silence | power contrast | device-verified |
| The playback stream path (stream, I²S and DACs) costs about 133 mW | power contrast | device-verified |
| HP_OUT_CTL_1 (0x080000) bits 5:4 `OUT_FS`: 00 0.5 Vrms, 01 1 Vrms, 10 1.41 Vrms, 11 1.73 Vrms (reset); bit 0 `+1dB_EN` gives 2 Vrms with `OUT_FS` 11 and `HV_EN` 1. Change only with PDN_HP set (§7.5.1) | DS1155F2 | datasheet |
| CLASS_H_CTL (0x0B0000) bit 1 `HV_EN`: 1 VP_LDO 3.0 V (reset, needs VP ≥ 3.3 V, loads of 600 Ω and above), 0 2.6 V (every load, full scale up to 1.41 Vrms). Bits 4:2 `ADPT_PWR`: 111 adapt (reset), 010 fixed ±VCP, 001 fixed ±VP_LDO. Adapt uses ±VCP (1.66 to 1.94 V) below about −11 dB of full scale and switches in about 20 µs | DS1155F2 §4.3, §7.5.10, Table 4-2 | datasheet |
| Quiescent playback power 26.07 mW per codec (VA = VCP = VD = VL = 1.8 V, VP = 3.6 V, 32 Ω). Idle noise 0.55 µV at every full scale. Click at a PDN_HP change ±50 µV typical. No ADC, level meter or loopback; HP_LOAD measures load impedance | DS1155F2 Table 3-19 | datasheet |
| The board has no headphone-detect line to the codecs | stock device tree | stock firmware |

The player's Headphone Power levels map onto these registers as described in
q2-player [docs/audio.md](https://github.com/q2-nova/q2-player/blob/main/docs/audio.md).

## Wi-Fi and Bluetooth

The combo chip is a Realtek RTL8733BS (8723F family). Driver details are in
[kernel/rtl8733bs](../kernel/rtl8733bs/README.md); the Bluetooth attachment and
its kernel support are in [bluetooth.md](bluetooth.md).

| Fact | Source | Level |
| --- | --- | --- |
| SDIO IDs 024c:b733 and 024c:b73a on MSC1 | stock `rtl8733bs.ko` | stock firmware |
| WL_DIS_N is PD01 and BT_DIS_N is PD20, both active high; no power-enable GPIO | stock `rtl8733bs.sh`, `rtk_wlan_power_ctrl`, `rtk_bt_power_ctrl` | stock firmware |
| Power-on: optional regulator, 200 ms, BT_DIS_N high, 10 ms, WL_DIS_N high, then MSC1 detect. Wi-Fi power-off drops WL_DIS_N and waits 100 ms, leaving BT_DIS_N high | stock `rtk_wlan_power_on`, `rtk_wlan_power_off` | stock firmware |
| AXP2101 ALDO1 at 1.8 V is required for SDIO responses; BLDO1 and BLDO2 are in the same supply chain | rail enable and MSC1 rebind | device-verified (ALDO1) |
| The stock codec module also requests ALDO1 at 1.8 V, in a separate ioctl branch | stock `codec_cs43131.ko` | stock firmware |
| MSC1 interrupts follow Wi-Fi packets, about 8.5 per frame | interrupt counts | device-verified |
| Bluetooth is HCI three-wire (H5, even parity) on UART0: PD23 RXD, PD24 TXD, PD25 CTS, PD26 RTS | stock `rtk_hciattach` command; Linux pin tables | device-verified (RTS/CTS works) |
| UART0 (`serial@0x10030000`) is clocked from the 24 MHz crystal with no divider; 16 × divisor gives 1.5 Mbaud exactly and cannot reach 3 Mbaud | stock device tree; Linux `x2000.dtsi`, `x2000-cgu.c` | vendor source |
| UMR (0x24, 5 bits) is the number of UART clocks per bit; UACR (0x28, 12 bits) adds one clock to each bit whose flag is set. 3 Mbaud is divisor 1, UMR 8, UACR 0 | X1000 programming manual ch. 23; stock `serial_ingenic_set_termios` table | datasheet |
| 3 Mbaud with RTS/CTS works when UMR and UACR are written after LCR clears DLAB, as the stock driver does; written while DLAB is set the controller does not answer at 2 or 3 Mbaud. At a termios rate of 3,000,000 the measured line rate is 2.70 Mbaud | HCI probes | device-verified |
| BT_DIS_N also enables the Wi-Fi slot's supply chain, so dropping it resets the Bluetooth controller only while the Wi-Fi slot is powered down | controller resets between probes with Wi-Fi off | device-verified |

Not known: the MSC1 bus I/O voltage; whether the boot loader leaves the
UART0 pins muxed; the RF power path.

## USB

| Fact | Source | Level |
| --- | --- | --- |
| DWC2 `snpsid` 0x4f54300a (3.00a), 16 host channels, total FIFO 3,576 words, architecture 2 (internal DMA), descriptor DMA and dynamic FIFO capable, 8 device endpoints | driver's cached `hw_params` | device-verified |
| The gadget runs with `g_dma=1`, `g_dma_desc=1` | driver's cached `params` | device-verified |
| The stock device tree sets `dr_mode="otg"` and has none of the PHY's optional GPIOs (`drvvbus`, `vbus-dete`, `id-dete`, `usb-switch`, `usb-wakeup`) | stock embedded device tree; `ingenic_usb_phy_probe` | stock firmware |
| PD17 is the USB detect input: the stock player's `usb_detect` returns true when it reads 0 | stock `demo` | stock firmware |
| PD18 is the OTG status input. When it changes to 0 the stock player sets PC20 then PE22 to 1; when it changes to nonzero it sets both to 0 | stock `demo` `check_otg_status` | stock firmware |

What PC20 and PE22 drive (boost, isolation or a USB switch) and the
electrical origin of PD18 are not known. No pin has been driven to test them.

## Power

### PMIC (X-Powers AXP2101)

| Fact | Source | Level |
| --- | --- | --- |
| AXP2101 at 0x34 on a GPIO I²C bus (PC23, PC24; bus 6 under the stock kernel) | `i2cget` reads; stock device tree | device-verified |
| ADC enable 0x30 = 0x03 (VBAT and TS only); VBAT in 0x34 and 0x35, mV, 14 bits; 0x01 bits 6:5 give only the current direction (10 = discharging); gauge SOC 0xA4. There is no battery current sensor | register reads; AXP2101 datasheet | device-verified |
| Power-key timing register 0x27 = 0x1d: 512 ms power-on press, 1.5 s long-press interrupt, 10 s forced off. 0x22 bits 1:0 enable button power-off and choose off, not restart | stock kernel `axp20x_pek_probe` AXP2101 branch; register read | device-verified |
| 0x21 is the power-off status, kept until power-on reset; bit 0 power key, bit 5 DC-DC undervoltage | AXP2101 datasheet (register 0x21); register reads | device-verified |
| 0x12 bit 3 applies to the battery-only power-off state, not to running operation | AXP2101 datasheet (register 0x12) | datasheet |
| Charge voltage register 0x64: selector 3 is 4.2 V, selector 4 is 4.35 V. At 4.2 V a charged cell raises the battery-overvoltage interrupt and the device restarts when USB is removed; at 4.35 V it does not | stock `axp2101_charger_probe`; AXP2101 datasheet (register 0x64); register change and USB removal | device-verified |
| Stock charge settings: 1,000 mA charge current (running, suspended and off), 100 mA termination, 4.35 V target, 2,000 mA USB input limit | stock device tree charger node | stock firmware |
| DCDC4 is the core rail at 0.95 V. DCDC5 is on at 1.4 V after a cold start (0x80 = 0x1f); see Audio for its effect on codec power-up | stock device tree; register reads | device-verified |
| ALDO1 1.8 V: Wi-Fi SDIO. ALDO2 and ALDO4: panel. ALDO3 2.8 V: touchscreen. BLDO1 and BLDO2: Wi-Fi supply chain. DCDC1 to DCDC4, ALDO1, ALDO3, BLDO1 and BLDO2 read on at the stock voltages | register reads; stock drivers | device-verified |
| Rail-control registers 0x80 to 0x87 and 0x90 to 0x9a do not change across system suspend | read-only snapshot at the regulator suspend boundary | device-verified |

### Fuel gauge (CW2015)

| Fact | Source | Level |
| --- | --- | --- |
| CW2015 at 0x62 on I²C0 (`/sys/devices/i2c-0/0-0062` under stock) | Rockbox; register reads | device-verified |
| VCELL 0x02 and 0x03 (305 µV/LSB); SOC 0x04 (%) and 0x05 (1/256 %); RRT_ALERT 0x06 and 0x07 (bits 12:0, remaining run time in minutes); MODE 0x0A = 0x00 (normal). SOC updates about every 5 s | register reads | device-verified |
| The CW2015 and AXP2101 SOC values differ (62 % against 72 % at the same moment) | register reads | device-verified |
| Cell rating 1,850 mAh | not documented | unverified |

### Power figures

| Fact | Source | Level |
| --- | --- | --- |
| Ingenic quotes, with 128 MB LPDDR3: NORMAL < 380 mW, IDLE (XBurst2 clock off) < 200 mW, IDLE-Power Down (XBurst2 off, < 0.5 ms to resume) < 180 mW, SLEEP (suspend to RAM, < 10 ms) < 4 mW, DEEP-SLEEP < 1 mW, HIBERNATE < 20 µW | "The X2000's Low Power Potentials" | datasheet |
| Clocks enabled at idle under stock: OTG controller and USB PHY, rotator, SADC, UART0/1/2/3/4/9, PWM, PDMA, AUDIO, MSC1, MSC2, SFC. HELIX, FELIX and ISP are gated; `power_isp0/1` off | debugfs `clk_summary` | device-verified |
| Clock-framework enable counts do not show physical clock state: DDR and CPU report zero enables while running | debugfs `clk_summary` | device-verified |
| Display path, panel on with the backlight dark against panel off: about 74 mW | power contrast | device-verified |
| Wi-Fi associated against down during playback: 69 to 81 mW; quiet paused contrast about 20 mW | power contrast | device-verified |
| Wi-Fi fetch energy 0.131 J/MB (interval 0.095 to 0.167) | controlled fetch series | device-verified |
| Analog output stage at silence about 114 mW; stream path about 133 mW (see Audio) | power contrast | device-verified |
| SD pins in SLEEP: 34 to 36 mW (see MSC) | sleep comparison | device-verified |

## Accelerators and auxiliary blocks

| Fact | Source | Level |
| --- | --- | --- |
| HELIX (H.264 encoder and JPEG) and FELIX drivers are registered under stock but unbound, and their device-tree nodes are disabled; no V4L2 device exists | read-only inventory | device-verified |
| AES, hash and RSA blocks have enabled device-tree nodes and no bound drivers; `/proc/crypto` lists only software algorithms; `/dev/hwrng` has no generator | read-only inventory | device-verified |
| The stock `soc_mcu` module provides `/dev/mcu`; which core it drives and whether it runs is not established | read-only inventory | device-verified (module present) |
| The click buzzer is PWM channel 1 on PC01; stock MCU firmware drives it | stock `libmcu-bare.bin` | stock firmware |
| SADC (stock `soc_adc.ko adc_vref=1800`) and the efuse (`ingenic,x2000-efuse`) are enabled under stock; no Q2 consumer of the SADC is known | stock device tree and modules | stock firmware |

## Pin map

| Pin | Function | Level |
| --- | --- | --- |
| PA07, PA08 | 3.5 mm and 4.4 mm jack detect, active low | device-verified |
| PA12 | microSD card power, active high | device-verified |
| PA14, PA15 | Touchscreen reset, interrupt | stock firmware |
| PB01, PB02, PB09, PB10, PB14 | DAC power, high-volume control, mode, mute, reset | stock firmware |
| PB16 | Previous key, active low, read by the SPL | stock firmware |
| PB20 | Wheel reset | stock firmware |
| PC00 | Backlight PWM0 | device-verified |
| PC01 | Buzzer PWM1 | stock firmware |
| PC20, PE22 | Driven with OTG transitions and low at power-off; consumer unknown | stock firmware |
| PC23, PC24 | PMIC GPIO I²C | stock firmware |
| PC25, PC26 | UART3, the console; pads not exposed outside the case | stock firmware |
| PD01, PD20 | WL_DIS_N, BT_DIS_N, active high | stock firmware |
| PD04, PD05 | I²C5 (DACs) | stock firmware |
| PD17, PD18 | USB detect, OTG status | stock firmware |
| PD23 to PD26 | UART0 to the Bluetooth controller (RXD, TXD, CTS, RTS) | device-verified |
| PE00 to PE05 | MSC2 (microSD) | device-verified |
| PE16 to PE21 | SFC (NAND) | stock firmware |
| PE25 to PE27 | `boot_sel[2:0]` | datasheet |
