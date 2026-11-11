import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type Context = { params: Promise<{ path: string[] }> };

const HOP_BY_HOP = new Set(["host", "connection", "content-length", "transfer-encoding"]);

async function proxy(request: NextRequest, path: string[]) {
  const base = (process.env.API_INTERNAL_URL || "http://127.0.0.1:8791").replace(/\/$/, "");
  const target = `${base}/api/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }
  const response = await fetch(target, init);
  const out = new Headers(response.headers);
  out.delete("content-encoding");
  out.delete("transfer-encoding");
  return new Response(response.body, { status: response.status, headers: out });
}

export async function GET(request: NextRequest, context: Context) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(request: NextRequest, context: Context) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PUT(request: NextRequest, context: Context) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function DELETE(request: NextRequest, context: Context) {
  const { path } = await context.params;
  return proxy(request, path);
}
