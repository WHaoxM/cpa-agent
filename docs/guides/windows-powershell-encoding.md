# Windows / PowerShell 5.1 编码处理指南

> **适用读者**: 在本仓库 Windows 开发机上工作的 Agent 与开发者。
> **一句话结论**: 仓库内所有文本文件（`docs/`、`backend/`、`workspace/`、数据 JSON）都是 **UTF-8 编码且无损坏**。你在终端里看到的乱码是 PowerShell 5.1 工具链的解码假象，**不是文件问题**，不要"修复"文件字节。

---

## 1. 事实（已扫描核验）

- `docs/`、`backend/`、`workspace/` 下所有 `.md/.py/.yaml/.sql/.json/.txt` 均为合法 UTF-8。
- 无真实 mojibake 文件；唯一非 UTF-8 文本是归档数据 `archive/early-prototypes/.../batch_l0_log.txt`，属历史垃圾。
- 根 `.env`、`backend/.env` 中的中文注释同样为 UTF-8，直接 `Get-Content` 会显示乱码但内容无损。

## 2. 为什么会出现乱码

PowerShell 5.1（Windows PowerShell，非 PowerShell 7）有两处与 UTF-8 冲突的默认行为：

| 场景 | 默认行为 | 后果 |
|---|---|---|
| `Get-Content <file>` 读取文件 | 按系统 ANSI 代码页（中文系统 = GBK/936）解码 | UTF-8 的中文被当 GBK 解码 → 屏幕乱码 |
| 控制台输出 native 命令（`python` 等） | 输出代码页 CP936 | Python 打印的 UTF-8 stdout 显示为乱码 |

> 关键：`[Console]::OutputEncoding = UTF8` 只修**输出显示**，**不能**改变 `Get-Content` 的**读取**解码。读取必须显式 `-Encoding UTF8`。

## 3. 正确的读写姿势

### 3.1 读文件（按优先级）

1. **优先使用 Read / Edit / Grep 工具** — 这些工具按 UTF-8 解码，看到的即真实内容。
2. PowerShell 中读：
   ```powershell
   Get-Content -Encoding UTF8 <file>
   ```
3. 禁止：裸 `Get-Content <file>`、`type <file>`、`cat <file>` 读含中文的文件。

### 3.2 运行 Python / native 命令

每次先设置 UTF-8 前导：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
```

（后端 `backend/run.py` 已内置 `PYTHONUTF8=1` + `SetConsoleOutputCP(65001)`，启动后端无需额外设置。）

### 3.3 写文件

- 编辑文件用 Edit/Write 工具（写出的就是 UTF-8）。
- 若必须用 shell 写文件，显式指定编码：
  ```powershell
  Set-Content -Encoding UTF8 <file> -Value "..."
  ```
  不要用默认 `>` 重定向（PS 5.1 写 UTF-16，会造成真实编码问题）。

## 4. 如何判断文件是否真的损坏

不要凭"看起来乱码"就重编码文件。按下面顺序验证：

1. **是否合法 UTF-8**（PowerShell 5.1）：
   ```powershell
   $bytes = [System.IO.File]::ReadAllBytes('<file>')
   try { [System.Text.Encoding]::UTF8.GetString($bytes) | Out-Null; 'OK UTF-8' }
   catch { 'NOT UTF-8' }
   ```
2. **含替换字符 U+FFFD（`�`）** 是真实损坏信号（丢失字节），出现则应从 git 恢复或重新生成该文件，而不是手动补。
3. **用 Python 验证**：
   ```python
   open('<file>', encoding='utf-8').read()   # 抛 UnicodeDecodeError = 非 UTF-8
   ```

只有上述检查失败，才考虑该文件确实有问题；否则保持原样，改用 3.1 的正确方式读取。

## 5. 常见坑

- `git diff` / `git status` 输出的中文乱码 — 同样只是控制台解码问题，内容无损。
- 用 GBK 工具（如部分旧编辑器）打开过文件后**不要保存**，否则可能把合法 UTF-8 二次编码成真乱码。
- 看到 `锛`/`銆`/`鎺`/`瀹炵幇` 这类字：是 UTF-8 字节被当 GBK 解码再显示的特征，文件本身大概率正常。
