import { NextRequest, NextResponse } from 'next/server';

const BACKEND = (process.env.INTERNAL_BACKEND_URL || 'http://127.0.0.1:5000').replace(
  /\/$/,
  '',
);

const PROXY_TIMEOUT_MS = 120_000;

function forwardHeaders(req: NextRequest): HeadersInit {
  const h: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  const local = req.headers.get('x-vision-local-key');
  const remote = req.headers.get('x-vision-remote-key');
  if (local) h['X-Vision-Local-Key'] = local;
  if (remote) h['X-Vision-Remote-Key'] = remote;
  return h;
}

async function proxyProgram(
  req: NextRequest,
  id: string,
  method: 'GET' | 'PUT' | 'DELETE',
) {
  const url = `${BACKEND}/api/programs/${encodeURIComponent(id)}`;
  let body: string | undefined;
  if (method === 'PUT') {
    body = await req.text();
  }

  try {
    const res = await fetch(url, {
      method,
      headers: forwardHeaders(req),
      body,
      signal: AbortSignal.timeout(PROXY_TIMEOUT_MS),
    });
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: {
        'Content-Type': res.headers.get('Content-Type') || 'application/json',
      },
    });
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      {
        error: 'Vision backend unreachable',
        detail: `Could not ${method} /api/programs/${id} via ${BACKEND}: ${detail}`,
      },
      { status: 502 },
    );
  }
}

type RouteCtx = { params: Promise<{ id: string }> };

export async function GET(req: NextRequest, ctx: RouteCtx) {
  const { id } = await ctx.params;
  return proxyProgram(req, id, 'GET');
}

export async function PUT(req: NextRequest, ctx: RouteCtx) {
  const { id } = await ctx.params;
  return proxyProgram(req, id, 'PUT');
}

export async function DELETE(req: NextRequest, ctx: RouteCtx) {
  const { id } = await ctx.params;
  return proxyProgram(req, id, 'DELETE');
}
