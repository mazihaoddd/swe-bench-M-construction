# Equals sign in code block breaks it

**Marked version:**
0.12.0

**Describe the bug**
Having an equals sign in a code block breaks the markdown, but only if a blank line doesn't precede the code block.

Sorry, I can't really put up good examples because code blocks are being formatted as markdown.

**To Reproduce**
📚 [Marked Demo](/testbed/assets/markedjs__marked-3210/input-1.md)

<img width="400" alt="Screenshot 2024-03-01 at 2 11 58 PM" src="/testbed/assets/markedjs__marked-3210/issue-01.png">

**Expected behavior**
📚 [CommonMark Demo](/testbed/assets/markedjs__marked-3210/input-2.md)

<img width="400" alt="Screenshot 2024-03-01 at 2 11 44 PM" src="/testbed/assets/markedjs__marked-3210/issue-02.png">



离线复现：运行 `node /testbed/assets/markedjs__marked-3210/reproduce.cjs`，查看 `/testbed/.reproduction/` 中的输出。复现脚本使用当前源码，不包含修复或判分断言。

资源：`/testbed/assets/markedjs__marked-3210/input-1.md`

资源：`/testbed/assets/markedjs__marked-3210/input-2.md`

资源：`/testbed/assets/markedjs__marked-3210/reproduce.cjs`

请在 `/testbed` 中修改代码，修复上述问题并保持既有行为。
