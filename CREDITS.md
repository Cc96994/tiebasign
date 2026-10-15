# 致谢与合规说明（Credits）

## TiebaLite

本项目的「内置登录」产品形态参考了开源项目 **TiebaLite**：

- 项目：https://github.com/HuanCheng65/TiebaLite
- 许可证：GNU General Public License v3.0 (GPL-3.0)

**参考内容（仅思路与产品形态，未复制任何代码）：**

1. 登录页 URL 使用百度官方移动版入口
   `https://wappass.baidu.com/passport?login&u=...`（公开的百度网址，属功能事实）；
2. 「App 内嵌官方登录页 + 从 WebView Cookie 提取 BDUSS/STOKEN」的登录方式；
3. 「登录成功后页面跳转到贴吧首页（tieba.baidu.com/index/tbwise/）」
   作为登录成功判定条件。

**说明：**

- 本项目的全部 Python/Java 代码均为独立实现，未从 TiebaLite
  复制、修改或分发任何源码片段，因此不构成 GPL 意义上的衍生作品。
- 若未来要直接复用 TiebaLite 的代码片段，则必须遵守 GPL-3.0：
  保留版权声明、随软件提供完整源码并以 GPL-3.0 发布。
- 在此向 TiebaLite 作者致谢。

## aiotieba

签到核心能力基于开源库 **aiotieba**（**The Unlicense**，即公有领域），
项目内以构建时补丁方式使用（Android Keystore 等 App 自有代码不属于
aiotieba）。

- 项目：https://github.com/lumina37/aiotieba
- 许可证：The Unlicense（原文首句 `This is free and unencumbered software
  released into the public domain.`）。核实自 `vendor/aiotieba-4.7.1.tar.gz`
  内的 `LICENSE` 文件 —— 该 sdist 的 `PKG-INFO` 不含 `License:` 字段。
- 上游是最宽松的一档，因此本项目在其之上选择 Apache-2.0 **不构成冲突**，
  也不会因此被要求改变自身许可证。

## 本项目

本项目自身代码以 **Apache-2.0** 发布，全文见仓库根 `LICENSE`
（Copyright 2026 Cc96994）。GitHub 与 Gitee 两个仓库携带同一份 `LICENSE`。
