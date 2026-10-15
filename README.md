# 电子税务局企业登录助手

Windows 企业电子税务局登录辅助工具。

## 安装

从 [Releases](https://github.com/xArsenn/e-tax-login-assistant/releases/latest) 下载最新版安装程序并运行。

## 数据安全

- 公开安装包不包含任何预置公司、账号或密码。
- 用户录入的账号与密码使用 Windows DPAPI 加密，并只保存在当前电脑的当前 Windows 用户目录。
- 程序更新只替换应用文件，不删除本地公司资料。

## 自动更新

应用启动后会检查 GitHub 上的 `latest.json`。有新版本时，经用户确认后下载、校验 SHA-256 并完成更新。

## 浏览器

程序优先使用系统安装的 Google Chrome，其次是 Microsoft Edge 和 Mozilla Firefox。
