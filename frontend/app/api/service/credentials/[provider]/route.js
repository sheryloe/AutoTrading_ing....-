import { deleteProviderSecret, upsertProviderSecret } from "../../../../../lib/service-control";
import { getServiceProviderDef, maskKeyHint } from "../../../../../lib/service-provider";

function unauthorized() {
  return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
}

function resolveProvider(params) {
  const provider = getServiceProviderDef(params?.provider);
  if (!provider) {
    return null;
  }
  return provider;
}

function validateAdminToken(body) {
  const expected = String(process.env.SERVICE_ADMIN_TOKEN || "").trim();
  if (!expected) {
    return { ok: false, response: Response.json({ ok: false, error: "service_admin_token_missing" }, { status: 500 }) };
  }
  const adminToken = String(body?.adminToken || "").trim();
  if (!adminToken || adminToken !== expected) {
    return { ok: false, response: unauthorized() };
  }
  return { ok: true };
}

export async function POST(request, context) {
  const body = await request.json().catch(() => ({}));
  const auth = validateAdminToken(body);
  if (!auth.ok) {
    return auth.response;
  }

  const provider = resolveProvider(await context?.params);
  if (!provider) {
    return Response.json({ ok: false, error: "provider_not_supported" }, { status: 404 });
  }

  const apiKey = String(body.apiKey || "").trim();
  const apiSecret = String(body.apiSecret || "").trim();
  if (!apiKey || (provider.requiresSecret && !apiSecret)) {
    return Response.json(
      { ok: false, error: provider.requiresSecret ? "api_key_and_secret_required" : "api_key_required" },
      { status: 400 },
    );
  }

  const payload = {
    api_key: apiKey,
  };
  if (provider.requiresSecret) {
    payload.api_secret = apiSecret;
  }

  try {
    await upsertProviderSecret(provider.id, payload, {
      api_key_hint: maskKeyHint(apiKey),
      provider_label: provider.label,
      provider_role: provider.role,
    });
    return Response.json({ ok: true });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "provider_secret_save_failed" },
      { status: 500 },
    );
  }
}

export async function DELETE(request, context) {
  const body = await request.json().catch(() => ({}));
  const auth = validateAdminToken(body);
  if (!auth.ok) {
    return auth.response;
  }

  const provider = resolveProvider(await context?.params);
  if (!provider) {
    return Response.json({ ok: false, error: "provider_not_supported" }, { status: 404 });
  }

  try {
    await deleteProviderSecret(provider.id);
    return Response.json({ ok: true });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "provider_secret_delete_failed" },
      { status: 500 },
    );
  }
}
