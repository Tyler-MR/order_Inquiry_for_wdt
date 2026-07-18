# 旺店通订单分析前端

这是一个 Vue 3 + Vite 页面，用于调用后端旺店通订单接口并展示订单、店铺、商品和时间维度分析。

## 本地开发

```powershell
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

默认请求 `http://127.0.0.1:8000`。如需更换后端地址，可设置：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 构建

```powershell
npm run build
```
