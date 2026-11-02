# Magpie Codex 额度详情

在 Magpie 中显示 Codex 当前与历史额度窗口、已用词元、费用估算、服务档位及本机用量样本。以 Magpie 原生去重账本与官方比例快照的同一时间点统计，无法确认账号的记录不计入账户估算。

插件采用 Magpie 原生 `package.json` 页面扩展格式。需要 Python 3.10+ 和支持 `quota codex --json`、`usage --csv` 与页面插件的 Magpie；本次本地验证版本为 `0.1.2667`。自动采集需要 Linux 用户级 systemd（系统服务管理器）。读取压缩会话时可选安装 `zstd`（压缩工具）。

## 安装

```bash
git clone https://github.com/Molun0301/magpie-codex-quota-details.git
cd magpie-codex-quota-details
python3 scripts/install.py install
```

默认安装到 `~/.local/share/magpie/plugins/codex-quota-details`，向 Magpie 注册本地页面插件，并启用每分钟运行的 `magpie-quota-details.timer`。首次打开会显示“尚未采集”，等定时器执行后刷新页面。

若 Magpie 不在命令搜索路径中，用 `MAGPIE_BIN=/absolute/path/to/magpie` 指定。安装器支持 `XDG_DATA_HOME`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME` 和 `--destination`。Linux 用户服务不可用时使用手动模式：

```bash
python3 scripts/install.py install --no-service
python3 ~/.local/share/magpie/plugins/codex-quota-details/collect.py
```

`--no-register --no-service` 用于仅复制文件，不注册宿主和定时器。已有手工安装时核对同名文件后加 `--replace`；不会覆盖已有 `page/snapshot.js`，也不会在重复注册时清空价格选项。

## 数据与价格

- 采集器读取 Magpie 的额度与用量导出、历史额度快照、本机价格目录和必要的会话服务档位字段。
- 页面只接收脱敏汇总，账号经过遮蔽，不包含凭据和请求正文。
- 自定义价格通过 Magpie 的页面选项保存；标准、快速与极速模式可分别设置。
- 内置服务价格文件是 2026-10-25 的已有参考快照，不保证仍是最新价格。实际使用前核对 [官方价格](https://developers.openai.com/api/docs/pricing)，可在页面修改。
- 本机没有覆盖其他设备的用量。词元或金额推算不能解释为订阅官方总额度或最终账单；价格未知或快照过期时会提示。

发布包只含空白快照模板，实际快照由采集器在用户机器上生成，并以 `0600` 权限保存。仓库忽略 `page/snapshot.js`。

## 更新、回退与卸载

```bash
git pull --ff-only
python3 scripts/install.py install
```

更新后重新打开 Magpie 插件页；价格选项、运行快照保留。手动模式仍加 `--no-service`。不要只运行 Magpie 的全局插件更新来更新这个本地安装目录，应使用本仓库的安装器。

回退：`git fetch origin --tags`、`git checkout v1.6.0`，重复安装命令。

```bash
python3 scripts/install.py uninstall
```

卸载会停用并删除本插件的定时器、解除页面注册、删除未被手改的程序文件；采集快照保留。Magpie 解除注册可能删除其保存的插件选项，卸载前可在页面记录需要保留的自定义价格。

## 故障检查

```bash
systemctl --user status magpie-quota-details.timer
systemctl --user status magpie-quota-details.service
python3 ~/.local/share/magpie/plugins/codex-quota-details/collect.py
```

若提示账本字段不兼容，请核对 Magpie 导出包含 `session_official_login`、`session_account`、`cost_usd`。采集错误只报告类型，避免在日志中泄露账号。无模型请求的离线验证：

```bash
python3 scripts/check.py
```

版本 `1.6.0`，MIT 许可。[维护与发布](docs/公开文档/维护与发布.md) · [更新记录](docs/公开文档/更新记录.md)。
