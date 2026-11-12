# 小红书 Chrome 发布实测经验与故障手册

本手册来自实际批量发布 5 条汽车视频的过程。目标不是“脚本能点击”，而是每一步都有证据，任何异常都不会造成重复发布。

## 一、已验证的成功路径

1. 用户明确授权 Chrome 后，复制 `Cookies`、`Preferences` 和 `Local State` 到临时隔离配置，不修改原 Chrome。
2. 启动独立 Chrome 和 CDP，等待 Cookie 存储加载，只统计域名，不打印 Cookie 值。
3. 用 `Browser.getVersion` 取得 UA，把 `HeadlessChrome` 替换为 `Chrome`，避免平台进入降级页面。
4. 打开发布页，确认账号和“上传视频”页面，而不是短信登录页。
5. 通过 `DOM.setFileInputFiles` 上传视频，等到标题编辑页出现。
6. 用 `Input.insertText` 填写标题、正文和话题，再读取页面值复核，不能只看截图。
7. 用 `Accessibility.getFullAXTree` 找到名称为“发布”且 `disabled=false` 的按钮。按钮存在不等于可以发布。
8. 对按钮执行 `DOM.scrollIntoViewIfNeeded`，用 `DOM.getBoxModel` 的 `model.content` 计算中心点，再用 `Input.dispatchMouseEvent` 发送真实鼠标按下和释放。
9. 只有页面出现“发布成功”或进入 `/publish/success` 才登记 `published`，并保存成功截图。
10. 批量发布逐条检查历史；错误、待确认或风控出现时停止后续项。

## 二、实际踩过的坑与防错措施

| 现象 | 根因 | 已固化的防错措施 |
|---|---|---|
| 内置浏览器已登录，但自动化拿不到会话 | 内置浏览器与个人 Chrome 是不同会话 | 只有用户明确授权后才复制 Chrome 会话；默认不读取个人浏览器 |
| Cookie 检查一直显示 0 | CDP 返回值已解包，又重复读取 `result.cookies` | `call()` 只解包一次；Cookie 读取使用 `call(...).get("cookies", [])` |
| Chrome 启动初期 Cookie 仍为空 | Cookie 存储尚未加载 | 最多等待 10 秒轮询，不在首次空结果时立即失败 |
| 页面出现“不支持视频展示”或笔记首页只有菜单 | `HeadlessChrome` 标识触发平台降级 | 使用真实版本 UA，将 `HeadlessChrome` 替换为 `Chrome` |
| 标题编辑页已出现，但填写时报找不到输入框 | CSS 选择器中的引号转义不一致 | 使用 `input[placeholder*=标题]` 等简单选择器，并读取实际值复核 |
| 发布按钮在截图中可见，普通 DOM 查询却找不到 | 按钮位于组件内部结构 | 用无障碍树查找按钮及 `backendDOMNodeId`，不要依赖固定 XPath |
| 点击按钮没有反应 | 用 `element.click()` 没有产生可信鼠标事件，或按钮仍禁用 | 等待 `disabled=false`，再按节点坐标发送真实鼠标事件 |
| 红色按钮出现，但上传/转码尚未完成 | 按钮“存在”和“启用”是两个状态 | 同时检查编辑页存在和按钮启用；未启用时继续等待 |
| `DOM.getBoxModel` 报 `content` 缺失 | CDP 外层是 `model`，坐标数组在 `model.content` | 先读取 `.get("model", {})`，再取 `content` |
| 点击后一直未成功 | 过早点击禁用按钮、页面降级或控件不匹配 | 保存发布前、异常和结果截图及页面状态，先定位原因，不盲目连点 |
| 初始化失败后残留 Chrome 和 Cookie 临时目录 | `__enter__` 抛错时不会自动进入 `__exit__` | `__enter__` 包装 `_start()`，异常时也执行清理；不得遗留会话副本 |
| `--browser-profile` 看似生效，实际仍读 Default | 复制函数把 Profile 写死 | Profile 从参数传入复制函数，目标隔离配置仍使用 Default |
| 批量发布顺序与入选榜单不一致 | 文件按推文 ID 字典序读取 | 有 manifest 时按 `downloads` 顺序执行，再处理额外计划 |
| 成功页地址被当成公开笔记链接 | `/publish/success` 只是结果页 | `result_url` 保存结果页；只有 `/explore/` 或 `/discovery/item/` 才写入 `note_url` |
| 待确认记录更正后仍被旧记录阻止 | 新 `failed` 被跳过，又匹配到更早的 `unconfirmed` | 历史以同一 ID/标题的最新状态为准，不能继续向旧记录回退 |
| 调试截图本身报错，覆盖原始异常 | 异常处理里直接读取页面和截图 | 调试采集允许失败，不能影响后续历史登记和停止逻辑 |

## 三、异常后的正确处理

### 上传或填写阶段失败

如果没有点击发布，记录 `error` 并停止，不写已发布历史。先查看 `<out>/browser/<原帖ID>_error.png` 和 `_error.json`，修复后重试当前条。

### 已点击但结果不明确

登记 `unconfirmed` 并停止后续发布。不能因为超时就直接重试。先到创作者后台确认：

- 如果已有该笔记，将记录改为 `published`，补充实际公开笔记 URL（如有）。
- 如果明确没有该笔记，才用 `publish_history.py resolve` 追加 `failed`，并写清后台核验依据。
- 未核实前继续保持 `unconfirmed`，它会阻止重复发布。

### 发布成功

保存成功截图和 `result_url`。`note_url` 只在获得真实公开笔记地址时填写；发布成功页不是公开分享链接。

## 四、发布前检查清单

- 当前计划为 `ready`，且用户明确要求实际发布，`confirm_publish=true`。
- 视频文件存在，标题、正文、话题与计划一致。
- 历史中没有该 `source_id` 或标题的有效记录。
- Chrome 登录态和上传页验证通过，Cookie 值未输出。
- 页面不是降级页，标题和正文已复读核验。
- 发布按钮在无障碍树中启用，不使用固定屏幕坐标。
- 每条发布成功后再进入下一条；异常和待确认立即停止批量任务。
- 初始化、发布或异常退出的临时 Chrome 和配置均已清理。

## 五、回归测试要求

修改 Chrome 发布逻辑时，至少覆盖：CDP 返回结构、Profile 复制、计划顺序、公开 URL 判定、正文复核、禁用按钮、真实点击、初始化清理、历史最新状态。真实页面验证分两步：先 `--preflight-only`，再 `--only <原帖ID>` 单条发布，第一条成功后才继续批量。
