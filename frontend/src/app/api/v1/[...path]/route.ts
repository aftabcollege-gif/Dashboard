import { NextRequest, NextResponse } from "next/server";
import { handleVercelDemoApi } from "@/lib/vercelDemoApi";

export const dynamic = "force-dynamic";

function isPreviewDemoFallbackEnabled(): boolean {
  return (
    (process.env.VERCEL === "1" && !process.env.BACKEND_INTERNAL_URL) ||
    process.env.NEXT_DEMO_PREVIEW_FALLBACK === "1"
  );
}

async function handle(
  req: NextRequest,
  context: { params: { path?: string[] } | Promise<{ path?: string[] }> }
) {
  if (!isPreviewDemoFallbackEnabled()) {
    return NextResponse.json(
      {
        detail:
          "Backend API proxy is not configured. Start the FastAPI backend on port 8000 or set BACKEND_INTERNAL_URL.",
      },
      { status: 502 }
    );
  }

  const resolvedParams = await Promise.resolve(context.params);
  const segments = resolvedParams?.path || [];
  let body: any = null;

  if (!["GET", "HEAD"].includes(req.method)) {
    const contentType = req.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        body = await req.json();
      } catch {
        body = null;
      }
    } else if (contentType.includes("multipart/form-data")) {
      try {
        const fd = await req.formData();
        const fileEntry = fd.get("file");
        const parsedObj: Record<string, any> = {};
        fd.forEach((v, k) => {
          if (k !== "file" && typeof v === "string") parsedObj[k] = v;
        });
        if (fileEntry && typeof (fileEntry as any).arrayBuffer === "function") {
          const f = fileEntry as File;
          parsedObj.__file = {
            name: f.name || "upload.xlsx",
            buffer: await f.arrayBuffer(),
          };
        }
        body = parsedObj;
      } catch {
        body = null;
      }
    }
  }

  const res = await handleVercelDemoApi(
    req.method,
    segments,
    req.nextUrl.searchParams,
    body,
    req.headers.get("authorization")
  );

  if (res.textBody !== undefined) {
    return new NextResponse(res.textBody, {
      status: res.status,
      headers: {
        "Content-Type": res.contentType || "text/plain; charset=utf-8",
      },
    });
  }

  return NextResponse.json(res.body ?? {}, { status: res.status });
}

export async function GET(req: NextRequest, ctx: any) {
  return handle(req, ctx);
}
export async function POST(req: NextRequest, ctx: any) {
  return handle(req, ctx);
}
export async function PUT(req: NextRequest, ctx: any) {
  return handle(req, ctx);
}
export async function PATCH(req: NextRequest, ctx: any) {
  return handle(req, ctx);
}
export async function DELETE(req: NextRequest, ctx: any) {
  return handle(req, ctx);
}
