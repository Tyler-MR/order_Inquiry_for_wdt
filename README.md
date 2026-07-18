# 旺店通订单查询与经营分析

基于 FastAPI + Vue 3 的旺店通订单查询页面，支持按时间、平台和时间字段查询订单，并从店铺、商品、日期三个维度分析经营数据。

## 功能

- 调用旺店通 OpenAPI 查询订单及商品明细
- 根据接口返回的 `total_count` 自动拉取完整分页
- 查询开始时间、结束时间、平台和时间字段
- 店铺排行、商品排行、每日订单与成交金额趋势
- 订单明细搜索、分页预览和完整 CSV 下载
- 查询条件保存到浏览器 `localStorage`
- 桌面端和移动端响应式布局

## 项目结构

```text
.
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 应用和接口路由
│   │   ├── schemas.py          # 请求、响应模型
│   │   ├── wdt_client.py       # 旺店通签名、分页、聚合和 CSV 数据
│   │   └── profit_service.py   # 原有利润数据服务
│   ├── .env.example
│   └── requirements.txt
└── frontend/
    ├── src/App.vue             # 查询页和分析面板
    ├── src/style.css           # 页面样式和响应式规则
    └── package.json
```

## 配置旺店通

复制环境变量模板：

```powershell
cd backend
Copy-Item .env.example .env
```

然后填写 `backend/.env`：

```env
WDT_SID=your_sid
WDT_APPKEY=your_appkey
WDT_APPSECRET=your_appsecret
WDT_API_URL=https://openapi.huice.com/openapi/trade_query.php
WDT_PAGE_DELAY=0.4
WDT_RATE_LIMIT_RETRIES=4
WDT_MAX_PAGES=0
```

`WDT_MAX_PAGES=0` 表示不设置默认分页上限，由程序根据接口返回的 `total_count` 自动获取完整数据。若手动填写页面上的最大页数且页数不足，接口会报错而不会返回半份结果。

## 启动后端

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

后端地址：http://127.0.0.1:8000

接口文档：http://127.0.0.1:8000/docs

## 启动前端

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

页面地址：http://127.0.0.1:5173

## 安全说明

- `backend/.env` 被 `.gitignore` 忽略，不应提交真实密钥。
- 只将配置模板提交到仓库，真实旺店通密钥请保存在本地环境变量或部署平台的 Secret 中。
- 订单数据只在查询时从旺店通接口读取，项目不把订单数据写入 Git 仓库。

## 验证

```powershell
python -m compileall -q backend/app
cd frontend
npm run build
```
