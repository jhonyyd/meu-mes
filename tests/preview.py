from test_contas import Database
from servidor import create_app
from starlette.responses import HTMLResponse
from starlette.routing import Route
import uvicorn

database = Database()
app = create_app(lambda: database)

async def mobile(request):
    return HTMLResponse('''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Prévia móvel · dados simulados</title></head>
    <body style="margin:0;min-height:100vh;background:#e9eee5;display:flex;align-items:flex-start;justify-content:center;padding:15px;box-sizing:border-box">
    <iframe title="Aplicativo em tela de celular" src="/" width="390" height="844" style="max-width:100%;border:1px solid #becfb9;border-radius:24px;background:#f6f7f3;box-shadow:0 8px 50px #153e3220"></iframe></body></html>''')

app.router.routes.insert(0, Route('/preview-mobile', mobile))

@app.middleware('http')
async def preview_frame(request, call_next):
    response = await call_next(request)
    policy = response.headers.get('Content-Security-Policy', '')
    if policy:
        response.headers['Content-Security-Policy'] = policy.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response

if __name__ == '__main__':
    print('PRÉVIA SIMULADA: pessoa1@teste.com / uma frase de teste longa')
    uvicorn.run(app, host='127.0.0.1', port=8001, access_log=False)
