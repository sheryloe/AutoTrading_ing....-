import { deleteProviderSecret, upsertProviderSecret } from "../../../../../lib/service-control";

function unauthorized() {
  return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
}

function normalizeHint(apiKey) {
  const compact = String(apiKey || "").trim();
  if (!compact) return "";
  if (compact.length <= 8) return compact;
  return `${compact.slice(0, 4)}...${compact.slice(-4)}`;
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

  const apiKey = String(body.apiKey || "").trim();
  const apiSecret = String(body.apiSecret || "").trim();
  if (!apiKey || !apiSecret) {
    return Response.json({ ok: false, error: "api_key_and_secret_required" }, { status: 400 });
  }

  try {
    await upsertProviderSecret(
      "bybit",
      {
        api_key: apiKey,
        api_secret: apiSecret,
      },
      {
        api_key_hint: normalizeHint(apiKey),
      },
    );
    return Response.json({ ok: true });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "bybit_secret_save_failed" },
      { status: 500 },
    );
  }
}

export async function DELETE(request) {
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
    await deleteProviderSecret("bybit");
    return Response.json({ ok: true });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "bybit_secret_delete_failed" },
      { status: 500 },
    );
  }
}
