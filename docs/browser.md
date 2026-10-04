# 专用浏览器：启动、验证与交接

当前实现只提供浏览器环境和操作命令。尚未接入独立模型执行循环、定时任务或 BOT。

## 启动

在本仓库目录运行。首次初始化需要 Python 3；后续浏览器工具在容器内部运行，宿主机无需安装 Selenium。

macOS：

```sh
export PATH="$HOME/.docker/bin:$PATH"
python3 scripts/init_local.py
docker compose up -d --build --wait
python3 scripts/browserctl.py start
```

Windows PowerShell：

```powershell
py scripts/init_local.py
docker compose up -d --build --wait
py scripts/browserctl.py start
```

初始化脚本保留已有 `.env`，不会覆盖密码。打开 <http://localhost:7900>，点击 Connect 后输入本机 `.env` 中的 `BROWSER_VIEW_PASSWORD`。无需注册 Docker 账号。

浏览器界面端口只绑定 `127.0.0.1`。WebDriver 服务不映射到宿主机；操作命令在容器内部调用它。不要自行把这些端口开放到公网。

## 浏览器工具

以下命令在 macOS 以 `python3 scripts/browserctl.py` 为前缀，在 Windows 以 `py scripts/browserctl.py` 为前缀，返回 JSON 和成功/失败退出码。新 agent 可直接读取这些结果。桥接程序在容器里执行工具，截图自动复制到本机，并返回完整本机路径。

| 子命令 | 行为 |
| --- | --- |
| `status` | 查询服务状态 |
| `start` | 复用有效会话；容器重启后建立新会话并使用原浏览器数据 |
| `observe` | 读取当前标题、URL、窗口尺寸 |
| `windows` | 读取本专用会话的窗口定位、标题和 URL，最后恢复原窗口；结果可能包含私人地址 |
| `switch-window <handle>` | 切换至已观察、仍存在的会话窗口 |
| `new-window <observed-url>` | 新开独立页并访问已观察的地址，保留旧窗口，返回新旧窗口定位 |
| `click-element <selector> --expect-text <text>` | 唯一匹配、核对完整渲染文本及可用状态后，用 WebDriver 点击；失败不自动重放 |
| `focus input.title` | 要求唯一元素，WebDriver 点击并自动滚动，确认当前活跃元素确为目标；失败时不得继续输入 |
| `screenshot current.png` | 保存当前页面截图到本机 `artifacts/` |
| `open https://xiumi.us/` | 打开页面 |
| `reload` | 明确刷新当前页面，重新加载已保存稿件；未保存时不要使用 |
| `click 300 250` | 在页面截图对应的坐标点击 |
| `type '测试文字'` | 在当前焦点输入文字；避免通过命令行传入真实密码 |
| `key Enter` | 按 Enter、Tab、Escape、Backspace；`SelectAll` 在当前焦点执行 Ctrl+A（专用浏览器是 Linux） |
| `scroll 500 --x 1000 --y 450` | 在指定坐标所在区域滚动；负数向上；默认原点 200,200，可能落在模板库 |
| `text body` | 读取指定 CSS 元素的可见文字 |
| `inspect body current.json` | 将选定区域的渲染文本、节点和图片信息取回本机 JSON，只在命令结果中输出路径与统计 |
| `inspect .tn-article-body article.json --frame iframe.preview-frame` | 读取当前秀米预览 iframe 中的正文；选择器须按实际页面重新核对 |
| `close` | 正常结束浏览器会话，保留数据 |

坐标以 WebDriver 的页面截图为准。人工查看入口的画面包含浏览器工具栏，可能缩放，不能直接混用两种坐标。页面或焦点发生变化后重新观察。

`observe` 另返回当前窗口 `handle`，其有效性限于当前会话；跨会话或换设备后必须重新关联。`new-window`、文本核对点击已在 Mac 自动组合历史四篇时实际使用。空文本点击只用于已观察到的唯一搜索图标，不认证稿件身份；新字段和命令尚未在 Windows 验收。

`inspect` 要求读取区域和 iframe 选择器各匹配唯一元素；每次调用在成功或失败后都回到顶层页面。它只读取渲染 DOM，不读取 Cookie、平台内部应用数据或隐藏接口。链接节点记录页面实际提供的 `href` 和 `target`，可用于重新打开官方编辑入口；不能据此推测未出现的地址。结果可能包含私人稿件信息和带令牌的链接，保存在 Git 忽略的 `artifacts/`；不要把整个 JSON 打印到公开日志或提交到仓库。

节点还记录 DOM 直接提供的 `data-*`、`aria-*`、`title` 和 `draggable` 属性，不读取框架内存。同步页的同题稿卡片本轮未提供可区分的身份属性；不能把当前列表位置、同一封面或相同更新时间当成永久稿件身份。

复选框和单选框记录当次 `checked`，支持禁用状态的元素记录 `disabled`；CSS 背景记录当次节点路径，用于关联同步组合里的对应封面。这些字段已在 Mac 当前同步页面读取，尚未在 Windows 验收。路径仍随页面变化，背景样式不证明资源加载或封面内容正确。

秀米编辑器的顶层 `body` 包含模板库，预览页面的 `body` 还包含阅读数等动态信息。它们不能直接当作纯稿件正文。先定位实际文章区域，再读取；CSS 背景样式与图片元素分别记录。

导航或点击命令返回时，页面的异步内容可能尚未加载完毕。先重新截图或读取预期区域确认实际状态；找不到预览 iframe 时不能立即判断稿件损坏。另存等产生副本的动作结果不确定时，先找回副本，不重复提交。

真实稿件匹配中发现：切换编辑器路由后旧预览可能保留；先核对当前稿件定位及标题，必要时在确认无未保存输入后刷新。文章区域暂时为空或图片数量仍增长时不能比较，等待非空且连续观察稳定。平台可能给稿件路由增加组件后缀，应核对实际稿件身份。预览生成的临时链接可以用于继续查看，必须来自页面实际提供的地址；链接会失效，不把它当作永久稿件入口。

打开秀米预览时曾出现“保存成功”提示，即使没有主动编辑或点击保存，也不能保证平台不自动保存或不更新时间。保留进入编辑器前的查询记录；需要严格保证无平台写入时，不能把编辑器预览认定为已验证的纯只读方法。详见[稿件匹配验证](draft-matching-validation.md)。

## 从读取结果生成报告

宿主机需要 Python 3.9 或以上。下面的规则只用于本项目的合成测试稿，不适用于业务稿件：

```sh
python3 scripts/check_snapshot.py artifacts/article.json --rules fixtures/article-check-rules.json --output artifacts/test-article-check.md
```

Windows 将 `python3` 改为 `py`。真实稿件未确认规则时可省略 `--rules`，只检查读取结果完整性和图片元素加载状态。报告默认写入 `artifacts/content-check.md`，输出目录限制在 Git 忽略的 `artifacts/` 内。退出码 `0` 表示已配置自动检查通过，`2` 表示检查发现失败项，`1` 表示输入或执行错误。

图片加载检查使用浏览器的 `complete` 和原始尺寸；不能代替视觉质量、图片语义、封面规范或背景资源加载检查。报告保留这些待核对项。

同步组合另有只读检查器 `scripts/check_sync_composition.py`，检查人工计划的头条、行序、封面样式、账号及选项，详见[公众号同步验证](wechat-sync-validation.md#只读脚本检查)。它不执行同步；计划记录已有提交时拒绝进入再次提交审核，平台实际结果仍须核对。

账号登录、验证码、协议和平台授权由使用者在人工入口完成。打开秀米首页不会自动复用这台电脑其他浏览器里的登录状态。

在较小窗口中可展开 noVNC 的“设置”，将“缩放模式”选为“本地缩放”。远程桌面是 Linux：粘贴或快捷键可能需要左侧“剪贴板”和“额外按键”控件。本轮桌面 Computer Use 的远程快捷键/文本转发有不稳定行为，agent 操作统一使用命令接口；真实人工登录已验收。

## 测试页面

```sh
python3 scripts/browserctl.py self-test
```

测试使用仓库内的独立页面：输入中文、执行坐标点击、核对结果、保存截图，并写入测试用 localStorage 标记。不操作秀米账号。

验证重启恢复（会中断当前浏览器，只在测试阶段执行）：

```sh
docker compose restart browser
python3 scripts/browserctl.py start
python3 scripts/browserctl.py self-test --resume
```

重启后服务需要时间就绪；如果命令提示连接失败，先确认容器 healthy，再执行 `start`。工具不会因连接错误自动重试点击或输入。

## 数据与换电脑

- `weiyang-publicity_browser-data` 命名卷保存 Chromium 数据、会话指针和命令锁。
- `weiyang-publicity_browser-artifacts` 命名卷保存容器中的截图，桥接程序将所需文件取到宿主机。这样无需改变宿主机目录所有者或开放写权限。
- `artifacts/` 保存截图等结果，`.env` 保存本机查看密码；两者不进入 Git。
- 换 agent 时在同一台运行中的电脑执行 `start`，然后重新观察，不重复执行上一条未知结果的操作。
- 换电脑需要先停止旧节点，再备份命名卷和稿件文件，在新节点恢复；只克隆代码不会复制登录状态。不要执行 `docker compose down -v`，它会删除命名卷。
- 登录状态跨设备/系统迁移仍需实际验证，平台也可能要求重新登录。现有任务账本支持检查点与交接记录，见 `docs/tasks.md`；浏览器恢复与自动业务任务恢复仍是不同能力。

当前使用固定镜像 `selenium/standalone-chromium:4.48.0-20260905`。来源与人工查看说明：[Selenium 官方仓库](https://github.com/SeleniumHQ/docker-selenium)。Windows x86_64 已在 `a44ab23` 基线通过构建、输入、点击、截图及容器重启存储保留，见 [Windows 迁移验证](windows-migration-validation.md)；真实业务及跨设备登录恢复另行验证。

2026-10-03 真实副本验证补充：`inspect` 记录节点内联样式和完整段落渲染文本（段落上限 10000 字并标记截断）。判断空段落使用段落全文，不能只检查 `own_text`；编辑器快速输入占位与文章预览分开核对。`SelectAll` 已在本机副本标题输入验收；指定滚动原点已用于文章区和预览 iframe。新增字段和操作尚未在 Windows 验收。

同日合成修正验证新增节点当次 CSS 路径及 `contenteditable` 属性，用于重新定位输入目标。路径随 DOM 改动而变，不是永久标识；`focus` 不认证稿件身份或内容。窗口观察已在本专用会话执行，focus 正向输入及缺失/歧义两项负向保护已本机验收。Backspace 可能合并组件或删除相邻文字，顶层空组件改用明确工具栏删除入口。详见[合成修正验证](format-correction-validation.md)。
