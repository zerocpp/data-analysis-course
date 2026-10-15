# CLAUDE.md — CodeSetArena 邮箱收取 Stage 1 学生题目包实现规则

本文件是 Claude Code / VibeCoding 的项目级约束文档。实现或修改“从网易 163 邮箱收取学生 Stage 1 题目包”的功能时，必须严格遵守本文规则。

## 0. 目标与不可变规则

目标：实现教师/助教侧 CLI，从网易邮箱 `@163.com` 通过 IMAP 收取学生提交的 Stage 1 题目包。

学生邮箱名不是可靠学号，不能从发件人邮箱名、显示名、正文签名中推断学号。学号只允许从邮件主题中通过正则表达式提取。

只允许下载文件名严格符合以下格式的附件：

```text
{学号}-student-stage1-problems.tar.gz
```

默认学号规则为 11 位数字，例如：

```text
20260000001-student-stage1-problems.tar.gz
```

除上述严格格式之外，其余任何附件格式一律不允许下载。

禁止下载的示例包括但不限于：

```text
20260000001_student_stage1_problems.tar.gz
20260000001-student-stage1-problems.zip
20260000001-student-stage1-problems.tar
20260000001-student-stage1-problems.gz
20260000001-student-stage1-problems.tar.gz.zip
20260000001-student-stage1-problems (1).tar.gz
张三-20260000001-student-stage1-problems.tar.gz
20260000001-student-stage1-problems.tar.gz.bak
../20260000001-student-stage1-problems.tar.gz
```

这些规则是安全边界，不是建议。实现时不得为了“兼容学生误操作”放宽匹配。

## 1. CLI 设计要求

建议新增教师/助教侧命令：

```bash
code-set-arena teacher stage1 collect-email \
  --provider 163 \
  --imap-host imap.163.com \
  --imap-port 993 \
  --username teacher_account@163.com \
  --password-env CODESETARENA_163_AUTH_CODE \
  --mailbox INBOX \
  --output-dir submissions/stage1 \
  --student-id-core-regex '\d{11}' \
  --since 2026-06-01 \
  --dry-run
```

参数要求：

- `--provider` 默认支持 `163`。
- `--imap-host` 默认 `imap.163.com`。
- `--imap-port` 默认 `993`。
- `--username` 是教师/助教邮箱地址。
- `--password-env` 只读取环境变量，不允许在 CLI 参数中直接输入密码或授权码。
- `--mailbox` 默认 `INBOX`。
- `--output-dir` 是下载根目录。
- `--student-id-core-regex` 默认 `\d{11}`，允许课程方覆盖。
- `--since` 用于缩小扫描范围。
- `--dry-run` 默认开启，只扫描和生成报告，不下载。
- 只有显式提供 `--download` 时，才允许执行附件下载。

## 2. IMAP 连接策略

必须通过 SSL IMAP 连接网易邮箱。

实现要求：

```python
import imaplib

imap = imaplib.IMAP4_SSL(host=imap_host, port=imap_port, timeout=timeout)
imap.login(username, password_from_env)
imap.select(mailbox, readonly=True)
```

硬性要求：

- 使用 `imaplib.IMAP4_SSL`。
- 默认端口使用 993。
- 默认以 `readonly=True` 选择邮箱，收包过程不应修改用户邮箱状态。
- 不要把邮箱授权码打印到日志、异常、报告或调试输出中。
- 只允许从 `--password-env` 指定的环境变量读取授权码。
- 所有邮件操作必须优先使用 UID，不使用普通 message sequence number 作为持久标识。
- 报告中可以记录邮件 UID、Message-ID、Date、From、Subject，但不得记录授权码。

推荐接口拆分：

```python
def connect_imap(config) -> imaplib.IMAP4_SSL: ...
def search_candidate_uids(imap, config) -> list[str]: ...
def fetch_message_metadata(imap, uid: str) -> MessageMeta: ...
def fetch_message_structure(imap, uid: str) -> MessageStructure: ...
def fetch_attachment_part(imap, uid: str, part_id: str) -> bytes: ...
```

## 3. 关键安全约束：不要整封邮件直接下载

严禁为了筛选附件而直接下载整封邮件。

禁止实现方式：

```python
# 禁止：这会把整封邮件和所有附件都下载到本地
imap.uid("FETCH", uid, "(RFC822)")
imap.fetch(seq, "(RFC822)")
```

禁止原因：

- 整封邮件下载会把所有附件一起取回，包括不合规附件。
- 这违反“其余格式一律不允许下载”的要求。
- 如果邮件中混有 `.zip`、`.rar`、恶意文件或大附件，整封下载会扩大攻击面。

必须采用两阶段策略：

### 第一阶段：只读取元数据和 MIME 结构

允许获取：

- UID
- Message-ID
- Date
- From
- Subject
- BODYSTRUCTURE
- 必要的 MIME part headers，例如 `Content-Disposition`、`Content-Type`、`filename`、`name`

第一阶段不得下载任何附件正文。

推荐 IMAP FETCH 形式：

```text
UID FETCH <uid> (UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)] BODYSTRUCTURE)
```

### 第二阶段：只下载已通过校验的附件 part

只有同时满足以下条件时，才允许下载某个附件 part：

1. 邮件主题能提取出唯一学号。
2. 附件文件名严格匹配 `{学号}-student-stage1-problems.tar.gz`。
3. 附件文件名中的学号与主题中的学号完全一致。
4. 同一封邮件中不存在冲突的合规附件。
5. 当前运行不是 `--dry-run`。

下载时必须只 fetch 合规附件对应的 MIME part：

```text
UID FETCH <uid> (BODY.PEEK[<part_id>])
```

不得使用会下载整封邮件或所有附件的命令作为捷径。

## 4. 正则匹配规则：从主题中提取学号

默认学号核心正则：

```python
STUDENT_ID_CORE_RE = r"\d{11}"
```

主题提取正则：

```python
SUBJECT_STUDENT_ID_RE = re.compile(r"(?<!\d)(?P<student_id>\d{11})(?!\d)")
```

实现要求：

```python
def extract_student_id_from_subject(subject: str, student_id_core_regex: str = r"\d{11}") -> str:
    """
    只从邮件主题中提取学号。
    0 个学号：拒绝。
    1 个学号：返回该学号。
    多个不同学号：拒绝并进入人工复核。
    """
```

规则：

- 主题中没有学号：拒绝该邮件。
- 主题中出现多个不同学号：拒绝该邮件，进入人工复核。
- 主题中只有一个学号：作为 `subject_student_id`。
- 禁止从发件人邮箱名、发件人显示名、邮件正文、附件内容中推断学号。

允许主题示例：

```text
20260000001 Stage1 提交
学号：20260000001 第一阶段题目包
CodeSetArena Stage1 20260000001
```

拒绝主题示例：

```text
Stage1 提交
张三题目包
20260000001 和 20260000002 提交
```

## 5. 附件文件名严格匹配规则

附件文件名必须用完整匹配，不允许 contains、startswith、endswith 等宽松判断。

默认附件正则：

```python
ATTACHMENT_FILENAME_RE = re.compile(
    r"^(?P<student_id>\d{11})-student-stage1-problems\.tar\.gz$"
)
```

如果课程方通过 `--student-id-core-regex` 覆盖学号规则，附件正则必须由该核心规则安全拼接生成：

```python
attachment_re = re.compile(
    rf"^(?P<student_id>{student_id_core_regex})-student-stage1-problems\.tar\.gz$"
)
```

实现要求：

```python
def match_stage1_attachment_filename(filename: str, student_id_core_regex: str = r"\d{11}") -> str | None:
    """
    返回附件文件名中的 student_id。
    不匹配时返回 None。
    必须 fullmatch。
    """
```

硬性要求：

- 必须使用 `fullmatch()` 或带 `^...$` 的正则。
- 文件名必须是 basename，不允许包含 `/`、`\`、`..`、空字节或路径片段。
- 不允许对错误文件名做“修复后接受”，例如去掉空格、去掉 `(1)`、替换下划线、改扩展名等。
- 大小写必须严格一致。
- 只接受 `.tar.gz`，不接受 `.zip`、`.rar`、`.7z`、`.tar`、单独 `.gz` 或双重扩展。

允许：

```text
20260000001-student-stage1-problems.tar.gz
```

拒绝：

```text
20260000001-student-stage1-problems.TAR.GZ
20260000001-student-stage1-problems.tar.gz.zip
20260000001-student-stage1-problems (1).tar.gz
./20260000001-student-stage1-problems.tar.gz
../20260000001-student-stage1-problems.tar.gz
folder/20260000001-student-stage1-problems.tar.gz
```

## 6. 学号一致性规则

邮件主题中的学号和附件文件名中的学号必须完全一致。

核心判断：

```python
subject_student_id == attachment_student_id
```

决策表：

| 邮件主题 | 附件名 | 结果 |
|---|---|---|
| `学号：20260000001 Stage1` | `20260000001-student-stage1-problems.tar.gz` | 允许下载 |
| `学号：20260000001 Stage1` | `20260000002-student-stage1-problems.tar.gz` | 拒绝 |
| `Stage1 提交` | `20260000001-student-stage1-problems.tar.gz` | 拒绝 |
| `学号：20260000001 Stage1` | `stage1.zip` | 拒绝 |

多附件规则：

- 一封邮件中只有一个合规附件，且学号一致：允许下载。
- 一封邮件中没有合规附件：不下载，报告记录 `no_valid_attachment`。
- 一封邮件中出现多个合规附件且学号相同：默认拒绝自动下载，报告记录 `duplicate_valid_attachment`，进入人工复核。
- 一封邮件中出现多个合规附件且学号不同：整封邮件拒绝，报告记录 `conflicting_valid_attachments`。
- 同一封邮件中的非合规附件永远不下载；如果同时存在一个合规附件和若干非合规附件，只允许下载合规附件对应 part。

推荐决策函数：

```python
def decide_stage1_download(subject_student_id: str, attachments: list[AttachmentMeta]) -> DownloadDecision:
    """
    输出 downloaded / rejected / skipped / duplicate / manual_review 等决策。
    必须包含拒绝原因。
    """
```

## 7. 保存策略

保存目录：

```text
{output_dir}/{student_id}/
```

保存路径：

```text
{output_dir}/{student_id}/{student_id}-student-stage1-problems.tar.gz
```

示例：

```text
submissions/stage1/20260000001/20260000001-student-stage1-problems.tar.gz
```

保存要求：

- 保存路径只能由 `output_dir`、主题/附件一致确认后的 `student_id` 和严格匹配后的标准文件名拼接生成。
- 不得直接信任邮件附件中的路径信息。
- 文件已存在时，默认不覆盖，报告记录 `already_exists`。
- 支持显式重复策略：

```bash
--duplicate-policy manual
--duplicate-policy skip
--duplicate-policy overwrite
--duplicate-policy keep-newest
--duplicate-policy preserve-review
```

默认：

```bash
--duplicate-policy preserve-review
```

### preserve-review 保留式人工复核策略

当 `--duplicate-policy preserve-review` 时：

1. 输入附件名仍必须严格匹配 `{student_id}-student-stage1-problems.tar.gz`。
2. 第一次出现且标准目标不存在：
   保存为 `{output_dir}/{student_id}/{student_id}-student-stage1-problems.tar.gz`。
3. 标准目标已经存在时：
   - 绝不覆盖、删除或修改标准文件。
   - 下载并完成 MIME 解码、大小检查、tar.gz 校验和 SHA256。
   - 保存到 `{output_dir}/{student_id}/duplicates/`。
   - 文件名：`{student_id}-student-stage1-problems__dup-{UTC_TIMESTAMP}__uid-{UID}.tar.gz`。
   - `action=manual_review`，`reason=duplicate_preserved`，`overwritten=false`，`requires_manual_review=true`。
4. 同一轮出现同一 `student_id` 的多封合规邮件：
   - 按 IMAP `INTERNALDATE`、`UID` 做稳定排序。
   - 如果不存在标准文件，最早到达的一份保存为标准文件。
   - 其余全部保存到 `duplicates/`。
   - 不使用邮件 `Date` 头决定顺序。
   - 不自动选最新版替换标准文件。
5. 如果重复文件名仍发生碰撞：
   - 使用排他创建 `Path.open("xb")`。
   - 捕获 `FileExistsError`。
   - 增加 `__n-2`、`__n-3` 后缀后重试。
   - 不得使用 `os.replace` 覆盖任何已有文件。

禁止行为：

- 禁止把附件保存到临时目录后再判断格式。
- 禁止先下载所有附件再删除不合规附件。
- 禁止自动重命名不合规附件使其变成合规格式。
- 禁止覆盖已有合规提交，除非用户显式选择覆盖策略。

## 8. 下载后校验

合规附件下载完成后必须校验：

1. 计算 SHA256。
2. 检查文件大小大于 0。
3. 检查文件可作为 gzip tar 包打开。
4. 不要直接解压到工作目录。
5. 如果需要检查 tar 内容，必须使用安全解包逻辑，禁止路径穿越。

推荐接口：

```python
def sha256_file(path: Path) -> str: ...
def validate_tar_gz(path: Path) -> ValidationResult: ...
def reject_tar_path_traversal(members: list[tarfile.TarInfo]) -> None: ...
```

## 9. 报告要求

每次运行都必须生成报告，即使是 `--dry-run` 或没有下载任何文件。

建议输出：

```text
collect-report.json
collect-report.csv
```

报告字段至少包括：

```json
{
  "uid": "...",
  "message_id": "...",
  "from": "...",
  "date": "...",
  "subject": "...",
  "subject_student_id": "20260000001",
  "attachment_filename": "20260000001-student-stage1-problems.tar.gz",
  "attachment_student_id": "20260000001",
  "part_id": "2",
  "action": "downloaded | rejected | skipped | duplicate | manual_review | error",
  "reason": "...",
  "saved_path": "submissions/stage1/20260000001/20260000001-student-stage1-problems.tar.gz",
  "canonical_path": "submissions/stage1/20260000001/20260000001-student-stage1-problems.tar.gz",
  "preserved_path": "",
  "received_at_utc": "2026-07-01T12:00:00Z",
  "duplicate_group_id": "",
  "overwritten": false,
  "requires_manual_review": false,
  "sha256": "...",
  "size_bytes": 12345,
}
```

报告要求：

- 所有拒绝必须有明确 `reason`。
- 所有跳过的非合规附件也应记录原因。
- 报告中不得出现邮箱授权码、API key、环境变量值。

## 10. 测试要求

必须添加单元测试，至少覆盖：

1. 正确主题 + 正确附件：允许。
2. 正确主题 + 错误扩展名：拒绝。
3. 正确主题 + 附件学号不一致：拒绝。
4. 无学号主题：拒绝。
5. 多学号主题：拒绝。
6. 附件名带路径：拒绝。
7. 附件名带 `(1)`：拒绝。
8. `.tar.gz.zip`：拒绝。
9. 多个合规附件：人工复核。
10. 目标文件已存在：默认拒绝覆盖。
11. `--dry-run`：不下载任何附件。
12. 存在合规附件和非合规附件混合：只允许下载合规 part，非合规 part 不得 fetch。
13. 实现中不得调用 `FETCH RFC822` 下载整封邮件。
14. `preserve-review` 策略下，已有标准文件的内容、mtime、大小和 SHA256 完全不变。
15. `preserve-review` 策略下，新重复文件成功写入 `duplicates/`。
16. `preserve-review` 策略下，同轮三个同学号附件：一个标准文件、两个重复版本。
17. `preserve-review` 策略下，时间戳相同时 UID 仍能避免冲突。
18. `preserve-review` 策略下，并发创建时不得覆盖。
19. `preserve-review` 策略下，非法附件不得保存到 `duplicates/`。
20. `preserve-review` 策略下，`dry-run` 只统计，不获取附件正文。

## 11. 实现自检清单

提交代码前逐项确认：

- [ ] 是否只从主题提取学号？
- [ ] 是否没有从邮箱名、正文、附件内容推断学号？
- [ ] 是否使用 UID 操作邮件？
- [ ] 是否默认 readonly 选择邮箱？
- [ ] 是否没有使用 `FETCH RFC822` 下载整封邮件？
- [ ] 是否只 fetch 已验证合规附件的 MIME part？
- [ ] 是否用严格正则匹配附件文件名？
- [ ] 是否拒绝所有非 `{学号}-student-stage1-problems.tar.gz` 格式？
- [ ] 是否要求主题学号和附件学号完全一致？
- [ ] 是否默认 dry-run？
- [ ] 是否默认不覆盖已有文件？
- [ ] 是否使用 `preserve-review` 作为默认重复策略？
- [ ] 是否用排他创建（`Path.open("xb")`）保存重复文件，不得覆盖？
- [ ] 是否生成 JSON/CSV 报告？
- [ ] 是否不输出、不保存邮箱授权码？

## 12. 给 Claude Code 的行为指令

当用户要求实现此功能时：

1. 先阅读本文件。
2. 先进入 plan mode，列出将要修改的文件和安全边界。
3. 不要在未确认前放宽任何正则或下载策略。
4. 如果现有代码中已经存在整封邮件下载逻辑，必须将其标记为安全问题并改为两阶段 MIME part 下载。
5. 如果 IMAP 服务器能力限制导致无法只下载指定 part，必须停止并向用户说明，不能退化为整封邮件下载。
6. 如果遇到主题学号不唯一、附件学号冲突、多合规附件或重复提交，默认进入人工复核，不自动下载、不自动覆盖。`preserve-review` 策略下，重复提交保存到 `duplicates/` 目录，不覆盖标准文件。
7. 任何“提高兼容性”的改动都不得突破本文件的下载白名单规则。
