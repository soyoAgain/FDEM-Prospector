# FDEM Prospector

FDEM 上位机软件，界面和远程采集流程参考同级 `TEM_app`，保留项目/测点管理、PXI SSH 控制、双通道采集和时域显示，发射部分改为严格零偏置的有限周期正弦波。

## 硬件通道

| 通道 | 接口 | 用途 |
| --- | --- | --- |
| `PXI2Slot3/ao1` | 功放 `Start` | `1 ms` 的 `0 V`，`500 ms` 的 `4 V`，最后回到 `0 V` |
| `PXI2Slot3/ao0` | 功放 `Signal in` | 输出 `n` 个频率为 `f` 的完整正弦周期 |
| `PXI2Slot2/ai29` | 电流监测 | 同步记录发射电流监测电压 |
| `PXI2Slot2/ai31` | 接收线圈 | 同步记录接收电压 |

完整接口和上电安全要求见 [`FDEM_HARDWARE_INTERFACES.md`](FDEM_HARDWARE_INTERFACES.md)。

## 安全警告

FDEM 功放 IGBT 没有任何保护措施。`Signal in` 出现直流信号或带 offset 的正弦波会立即烧坏 IGBT。

- 禁止把 TEM 的 `0 V -> 4 V` 发射阶跃送入 `ao0/Signal in`。
- 首次连接和每次修改波形、采样参数、接线或 PXI 配置后，必须断开功放并使用 DC 耦合示波器验证 `ao0`。
- 必须验证 PXI 启动、程序启动、任务创建、正常结束、异常退出、SSH 中断、设备复位及重启状态。
- GUI 的软件检查和确认框不能替代硬件保护或示波器测量。
- `3.3 V` 必须由操作者明确选择为 `Vpp` 或 `Vpk`，软件不会默认猜测。

## 安装与启动

```bash
python -m pip install -r requirements.txt
python main.py
```

macOS 也可执行：

```bash
chmod +x run.sh
./run.sh
```

软件会自动为 Qt 和 Matplotlib 选择可用中文字体，优先顺序包括 `PingFang SC`、`Hiragino Sans GB`、`Microsoft YaHei`、`SimHei` 和 `Noto Sans CJK SC`。远程 PXI Python 输出使用 UTF-8，同时保留对 Windows GBK 输出的兼容解码。

PXI 端需要 Python、NumPy、NI-DAQmx 驱动和 `nidaqmx`。GUI 会将 `fdem_acquisition.py` 和 `config.py` 上传到 `C:\Users\sjtu\FDEM_app`。

## 操作流程

1. 设置项目、测点、频率 `f`、周期数 `n` 和采样参数。
   可设置频率步进 `df`，点击“`f + df`”将当前频率增加一个步进；达到系统频率上限时不会继续增加。
2. 明确选择标称 `3.3 V` 的定义：`Vpp` 或 `Vpk`。
3. 在功放断电且断开 `Signal in` 时，用 DC 耦合示波器验证 `ao0` 所有状态。
4. 勾选示波器确认项。
5. 点击“① 充电”，发送与 TEM 一致的 `Start` 波形。
6. 确认功放已进入准备状态。
7. 点击“② 正弦发射 + 同步采集”，再次核对确认对话框。
8. 采集数据自动保存到 `data/<项目>/<测点>/`。
9. 使用“幅相”查看目标频率下的接收、电流监测和 `Rx/Current` 结果。

`ai29` 连接衰减探头。界面中的“ai29 衰减倍数”用于换算：

```text
发射电流 [A] = ai29 原始采集电压 [V] x 衰减倍数
```

默认衰减倍数为 `500`。时域电流图和幅相窗口均使用换算后的安培值，`Rx/Current` 的幅值单位为 `V/A`。每次测量会在 `*_info.json` 中保存当时使用的衰减倍数；已有记录若包含其他倍数，加载时优先使用记录值。

界面显示的接收和电流监测绝对相位尚未经过模拟链路延迟校准，只能作为参考。`Rx/Current` 相对相位更适合当前系统；精确相位测量仍需完成 AO 回采、通道延迟和硬件时钟路由的现场标定。

## 数据文件

每次测量保存：

- `<测点>_<编号>_<频率>Hz_t.npy`：时间轴，单位秒；
- `<测点>_<编号>_<频率>Hz_rx.npy`：`ai31` 原始接收电压；
- `<测点>_<编号>_<频率>Hz_current.npy`：`ai29` 原始电流监测电压，单位 `V`；显示和分析时再乘以记录的衰减倍数换算为 `A`；
- `<测点>_<编号>_<频率>Hz_info.json`：发射、采样和波形参数。

例如 `1000 Hz` 的第 3 次测量保存为 `测点1_003_1000Hz_rx.npy`。小数频率使用 `p` 代替文件名中的小数点，例如 `12.5 Hz` 保存为 `12p5Hz`。软件仍兼容加载不含频率的旧文件名。

软件不对原始数据执行 TEM 工频陷波或均值消除，避免改变 FDEM 幅值和相位。

## 测试

纯软件测试不需要 PXI：

```bash
python -m unittest discover -s tests -v
```

这些测试覆盖正弦波周期数、零均值、首尾回零、幅值定义、非法参数拒绝及合成数据幅相恢复。硬件上线前仍必须执行接口文档规定的示波器测试。
