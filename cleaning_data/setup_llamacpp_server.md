# llama.cpp 部署 Qwen3.5-4B 完整指南

## 方案优势
- 不干扰现有 conda 环境
- GGUF 量化后推理更快
- 提供 OpenAI 兼容 API

## 步骤 1: 下载 llama.cpp

```bash
# Windows PowerShell
cd D:\AGeneral Workspace\cpa-agent
git clone https://github.com/ggerganov/llama.cpp.git
```

## 步骤 2: 编译 llama.cpp (Windows)

```bash
cd llama.cpp
# 使用 CMake 编译 (需要 Visual Studio 或 MinGW)
mkdir build
cd build
cmake ..
cmake --build . --config Release

# 编译完成后，server 在: build/bin/Release/llama-server.exe
```

## 步骤 3: 转换模型为 GGUF 格式

```bash
# 回到 llama.cpp 根目录
cd ..

# 安装转换依赖
pip install -r requirements.txt

# 转换为 FP16 GGUF (先转完整精度)
python convert_hf_to_gguf.py model/Qwen3.5-4B --outfile model/Qwen3.5-4B-f16.gguf

# 量化为 Q4_K_M (推荐，平衡质量和速度)
./build/bin/Release/llama-quantize model/Qwen3.5-4B-f16.gguf model/Qwen3.5-4B-Q4_K_M.gguf Q4_K_M
```

## 步骤 4: 启动 API Server

```bash
# 启动 OpenAI 兼容服务
./build/bin/Release/llama-server \
    -m model/Qwen3.5-4B-Q4_K_M.gguf \
    --host 127.0.0.1 \
    --port 8080 \
    -c 4096 \
    -n 512 \
    --slots 4

# 参数说明:
# -m: 模型路径
# --host/--port: 监听地址端口
# -c: 上下文长度
# -n: 最大生成token数
# --slots: 并发槽位数
```

## 步骤 5: 测试调用

```bash
# 新终端测试
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-4b",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.1
  }'
```

## 步骤 6: 运行统计脚本

```bash
# 修改 cleaning_data/count_cs_jobs_local.py 中的端口为 8080
# 然后运行
conda activate mkg
python cleaning_data/count_cs_jobs_local.py
```

---

## 快速启动脚本 (保存为 start_llamacpp_server.bat)

```batch
@echo off
cd /d "D:\AGeneral Workspace\cpa-agent\llama.cpp"
.\build\bin\Release\llama-server.exe -m "..\model\Qwen3.5-4B-Q4_K_M.gguf" --host 127.0.0.1 --port 8080 -c 4096 -n 512 --slots 4
```

## 常见问题

1. **编译失败**: 确保安装 Visual Studio Build Tools 或 MinGW
2. **内存不足**: 使用更小量化级别 (Q3_K_S 或 Q2_K)
3. **GPU 加速**: 编译时添加 -DLLAMA_CUDA=ON (需要 CUDA)
