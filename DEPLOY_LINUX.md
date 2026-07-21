# Linux Docker 部署

## Windows 本地准备

```powershell
cd C:\Users\Financial\wwwroot\order_Inquiry_for_wdt
Copy-Item compose.env.example .env
Copy-Item backend\.env.example backend\.env
```

编辑 `.env` 填写 MySQL 密码，编辑 `backend\.env` 填写旺店通配置。真实密码和旺店通密钥不要提交 GitHub。确认 Excel 位于 `private\shop_owner_map.xlsx`。

## Linux 获取项目

```bash
mkdir -p /home/lingchi/order_Inquiry_for_wdt
cd /home/lingchi/order_Inquiry_for_wdt
git clone <你的 GitHub 仓库地址> .
```

从 Windows 上传本地密钥配置：

```powershell
$KEY = "C:\Users\Financial\.ssh\codex_linux_deploy_ed25519"
$DEST = "lingchi@192.168.16.54:/home/lingchi/order_Inquiry_for_wdt"
scp -i $KEY .env "$DEST/.env"
scp -i $KEY backend\.env "$DEST/backend/.env"
```

## 启动 Docker

如果 Linux 能访问 Docker Hub：

```bash
cd /home/lingchi/order_Inquiry_for_wdt
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 backend
```

容器启动时会自动执行 `init_db.py`，包括创建 `shop_owner_map` 表。

当前 Linux 如果仍无法访问 Docker Hub，在 Windows 构建后传镜像：

```powershell
cd C:\Users\Financial\wwwroot\order_Inquiry_for_wdt
docker compose build backend web
docker pull mysql:8.4
docker save -o wdt-images.tar wdt-dashboard-backend:local wdt-dashboard-web:local mysql:8.4
$KEY = "C:\Users\Financial\.ssh\codex_linux_deploy_ed25519"
scp -i $KEY wdt-images.tar lingchi@192.168.16.54:/home/lingchi/order_Inquiry_for_wdt/
```

Linux 导入并启动：

```bash
cd /home/lingchi/order_Inquiry_for_wdt
docker load -i wdt-images.tar
docker compose up -d
docker compose ps
```

## 首次导入店铺负责人

Windows 生成 JSON：

```powershell
cd C:\Users\Financial\wwwroot\order_Inquiry_for_wdt
py -3 scripts\sync_shop_owner_map.py --json-only
```

直接推送并导入 Linux：

```powershell
py -3 scripts\sync_shop_owner_map.py `
  --excel private\shop_owner_map.xlsx `
  --ssh-key C:\Users\Financial\.ssh\codex_linux_deploy_ed25519
```

脚本会通过 SSH 上传 JSON，并在 backend 容器内执行数据库导入；导入失败会保留旧数据。

## Windows 每日定时同步

在“任务计划程序”中新建任务：

- 每天执行，例如 02:00
- 程序：`C:\Windows\py.exe`（先用 `where py` 确认）
- 起始位置：`C:\Users\Financial\wwwroot\order_Inquiry_for_wdt`
- 勾选“错过计划时间后尽快运行”

参数：

```text
-3 C:\Users\Financial\wwwroot\order_Inquiry_for_wdt\scripts\sync_shop_owner_map.py --excel C:\Users\Financial\wwwroot\order_Inquiry_for_wdt\private\shop_owner_map.xlsx --ssh-key C:\Users\Financial\.ssh\codex_linux_deploy_ed25519
```

Windows 电脑必须在同步时间开机。以后代码更新后重新构建镜像、传到 Linux，再执行 `docker compose up -d`。MySQL 数据保存在 `mysql_data` volume 中。域名、HTTPS 和公网端口转发待 Docker 部署稳定后，再用 Nginx 或 Caddy 配置。
