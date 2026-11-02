# 第 1 天：工程基线

## 今天的目标

让项目从“我电脑上能运行”变成“别人可以检查、复现和追踪”。今天不增加业务功能。

完成今天的学习后，你应该能够回答：

1. 为什么 `.env`、日志和向量数据库不能提交到 Git？
2. Git 提交和网盘压缩包有什么区别？
3. CI 自动执行了哪些检查？
4. 为什么测试不依赖真实模型 Key 也是有效测试？
5. 面试官如何通过提交记录判断项目是不是一次性生成的？

## 核心概念

### Git 是思考过程

一个只有最终版本的项目，无法说明你做过哪些取舍。一个好的提交应该表达一个完整意图：

- `chore: establish project baseline`
- `feat: add deterministic knowledge routing`
- `test: cover cross-user access`
- `fix: keep quick prompts after first answer`

不要把几十个文件一次性提交成 `update` 或 `final`。

### CI 是自动验收

CI 的英文是 Continuous Integration，中文常叫持续集成。它会在你推送代码后自动完成：

1. 安装声明过的依赖。
2. 检查依赖是否冲突。
3. 编译 Python 文件。
4. 运行所有测试。

如果 CI 失败，说明项目在干净环境中不能复现。面试官不需要相信你的口头描述，可以直接看 CI 结果。

### `.gitignore` 是安全边界

当前项目不应提交：

- `.env`：包含真实 API Key。
- `logs/*.log`：可能包含用户问题、工具参数和内部错误。
- `md5.text`：本机知识库处理状态。
- `rag/chroma_db/`：可以从原始知识文件重新生成的索引。
- `eval/results/`：每次运行可能变化的评测结果。

向量库不是原始数据，而是派生数据。提交原始文档和构建脚本，比提交一个几 MB 的数据库更可靠。

### 测试为什么要隔离外部依赖

普通 CI 不调用真实大模型，原因包括：

- 会产生费用。
- 网络和模型输出不稳定。
- CI 不应该依赖个人 Key。

因此把纯逻辑测试与真实模型评测分开：

- pytest 验证权限、数据解析、消息过滤和页面状态。
- RAG 评测验证真实检索质量。
- Playwright 回归验证真实浏览器操作。

## 今天的动手任务

### 任务 1：检查敏感文件

```powershell
git check-ignore -v .env logs\agent_20261102.log md5.text rag\chroma_db\chroma.sqlite3
```

你应该看到每个文件命中的忽略规则。如果 `.env` 没有被忽略，立即停止提交。

### 任务 2：运行与 CI 相同的检查

```powershell
python -m pip check
python -m compileall -q app.py agent config model rag services ui utils scripts tests
python -m pytest
```

当前基线应全部通过。测试输出中的弃用警告不是失败，但需要记录为后续迁移任务。

### 任务 3：检查将要提交的文件

```powershell
git add -n .
git status --short
```

确认列表中没有 `.env`、日志、向量数据库和评测结果。

### 任务 4：建立初始提交

配置自己的 Git 身份后执行：

```powershell
git switch -c codex/day01-engineering-baseline
git add .
git commit -m "chore: establish project baseline"
```

不要使用别人的姓名和邮箱，也不要把真实 Key 写入提交信息。

## 面试表达

### 面试官可能问：你为什么把 Chroma 数据目录忽略了？

推荐回答：

“Chroma 是可重建的派生数据，真正的源数据是知识文件、切分参数和索引脚本。把数据库提交到 Git 会导致仓库膨胀，也无法保证索引和源文件版本一致。我通过 MD5 和后续的索引清单控制重建。”

### 面试官可能问：没有 API Key 的测试有什么意义？

推荐回答：

“普通测试覆盖确定性业务边界，例如数据解析、权限、消息过滤和事件转换。真实模型质量放在独立评测中，避免把网络波动、费用和随机输出引入 CI。”

### 面试官可能问：这个 CI 能证明什么？

推荐回答：

“它能证明项目可以从干净环境安装依赖、通过编译并运行基础测试。它不能证明 RAG 答案正确，所以我还单独保留检索评测和后续的端到端评测。”

## HR 视角

你的简历条目不要写“熟悉 Git”，而要写你实际做过的事：

> 为 Agent 项目建立可复现工程基线，隔离密钥与派生索引，配置 CI 执行依赖检查、Python 编译和自动化测试。

这句话能引出“你如何保证作品能被别人运行”的追问，比单纯罗列工具更有说服力。

## 今天结束前的自测

- 我能解释 Git 和备份压缩包的区别。
- 我能解释 CI 的四个检查步骤。
- 我能指出三个绝不能提交的文件。
- 我能说明为什么普通测试不调用真实模型。
- 我能说出一项 CI 无法证明的事情。

全部能回答，再进行第 2 天的登录身份与权限边界。
