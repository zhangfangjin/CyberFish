# CyberFish MySQL 部署

MySQL 是可选的管理员侧持久化组件。未设置 `CYBERFISH_DB_ENABLED=1` 时，
程序保持原来的本地 `config.json` + UDP 运行方式。

## 1. 建库和迁移

要求 MySQL 8.0，数据库字符集使用 `utf8mb4`：

```sql
CREATE DATABASE cyberfish
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
```

用迁移账号执行：

```bash
mysql -h 127.0.0.1 -u cyberfish_migrator -p cyberfish \
  < db/migrations/001_init.sql
```

运行账号只需要应用表的 `SELECT`、`INSERT`、`UPDATE`、`DELETE` 权限。不要把
迁移账号或密码写入 `config.json`。

## 2. 管理员节点环境变量

```bash
export CYBERFISH_DB_ENABLED=1
export CYBERFISH_DB_HOST=127.0.0.1
export CYBERFISH_DB_PORT=3306
export CYBERFISH_DB_NAME=cyberfish
export CYBERFISH_DB_USER=cyberfish_app
export CYBERFISH_DB_PASSWORD='replace-me'
venv/bin/python -m cyberfish --admin
```

管理员和 MySQL 在同一台机器时，也可以设置 `CYBERFISH_DB_UNIX_SOCKET` 使用
Unix socket；设置后优先使用该 socket，`HOST/PORT` 仅作为默认值保留。

只有选举后的有效管理员会连接数据库。展示节点不需要数据库驱动配置和凭据。

## 3. 故障行为

- 数据库断开时继续使用 `config.json` 中最后成功的配置运行。
- 数据库不可用期间拒绝管理配置变更，避免本地缓存和数据库产生冲突。
- 鱼坐标、动画和跨屏移交不写数据库，MySQL 延迟不会进入实时链路。
- 节点每 10 秒上报累计指标，管理员按分钟聚合后异步写库。
- `network_enabled` 关闭的是鱼同步/移交数据面，管理心跳和配置通道保持在线。

## 4. 默认保留时间

- 分钟指标：30 天。
- 自动拓扑快照：90 天。
- 管理命令和 ACK：180 天。
- 节点、配置版本、手动拓扑和运行会话：长期保留。

清理由管理员数据库线程在连接建立后分批执行，每类每次最多删除 1,000 行。
