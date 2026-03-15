import { upsertRuntimeConfig } from "../../../../lib/service-control";

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

  try {
    const saved = await upsertRuntimeConfig(body.config || {});
    return Response.json({ ok: true, config: saved });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "runtime_config_save_failed" },
      { status: 500 },
    );
  }
}
