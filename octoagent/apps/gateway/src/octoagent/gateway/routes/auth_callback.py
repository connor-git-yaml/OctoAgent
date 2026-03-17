"""OAuth 回调路由 -- 接收 OAuth Provider 的授权回调

Gateway 路由模式：OAuth Provider（如 OpenAI）完成授权后，
浏览器重定向到此路由（而非独立端口的临时服务器）。
此路由通过 shared future 将授权码传递给正在等待的 OAuth 流程。

优点：
- 无需额外端口，彻底避免端口冲突
- 复用 Gateway 的 8000 端口
- 用户多次点击"连接"不会因端口占用而失败
"""

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from octoagent.provider.auth.callback_server import resolve_pending_flow

router = APIRouter()

# 成功回调后返回的 HTML 页面
_SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>OctoAgent OAuth</title></head>
<body style="font-family:system-ui;text-align:center;padding:60px">
<h2>授权成功</h2>
<p>可以关闭此窗口并返回 OctoAgent。</p>
</body>
</html>"""

# 错误页面模板
_ERROR_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>OctoAgent OAuth</title></head>
<body style="font-family:system-ui;text-align:center;padding:60px">
<h2>授权失败</h2>
<p>{message}</p>
</body>
</html>"""


@router.get("/auth/callback")
async def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
) -> HTMLResponse:
    """处理 OAuth 授权回调

    OAuth Provider 授权成功后，浏览器会被重定向到:
      http://localhost:8000/auth/callback?code=xxx&state=yyy

    此路由将 code + state 传递给 resolve_pending_flow()，
    由它唤醒正在 await 的 OAuth 流程（wait_for_gateway_callback）。
    """
    if not code or not state:
        return HTMLResponse(
            content=_ERROR_HTML.format(message="缺少必需的 code 或 state 参数"),
            status_code=400,
        )

    success, message = resolve_pending_flow(code=code, state=state)

    if success:
        return HTMLResponse(content=_SUCCESS_HTML, status_code=200)
    else:
        return HTMLResponse(
            content=_ERROR_HTML.format(message=message),
            status_code=400,
        )
