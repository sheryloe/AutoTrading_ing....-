import { hardResetServiceDemo } from "../../../../lib/service-control";

function unauthorized() {
  return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
}

export async function POST(request) {
  const expected = String(process.env.SERVICE_ADMIN_TOKEN || "").trim();
  if (!expected) {
    return Response.json({ ok: false, error: "service_admin_token_missing" }, { status: 500 });
  }

  const body = await request.json().catch(() => ({}));
  const adminToken = String(body.adminToken || "").trim();
  if (!adminToken || adminToken !== expected) {
    return unauthorized();
  }

  const confirmText = String(body.confirmText || "").trim().toUpperCase();
  if (confirmText !== "RESET FUTURES DEMO") {
    return Response.json({ ok: false, error: "reset_confirmation_required" }, { status: 400 });
  }

  try {
    const result = await hardResetServiceDemo({
      seedUsdt: Number(body.seedUsdt || 10000),
    });
    return Response.json({ ok: true, ...result });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "service_demo_reset_failed" },
      { status: 500 }
    );
  }
}
