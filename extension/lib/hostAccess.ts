// Runtime host access for the target site. The in-tab session probe, PAT mint, and revoke all run
// `chrome.scripting.executeScript` against the DHIS2 tab, which needs host access to that origin.
// At real runtime that access has two sources:
//   - `activeTab`: granted transiently when the user invokes the extension action ON that tab, and
//     revoked again on navigation. It is invisible to `chrome.permissions.contains`.
//   - an optional host permission grant (the manifest declares `optional_host_permissions` for
//     http/https), which is DURABLE and survives navigations and future panel opens.
// Relying on `activeTab` alone is why binds died with "injection failed" whenever the panel was
// opened any other way (side-panel UI, opened earlier on another tab, or the tab navigated since).
// So every user-gesture entry point requests the durable grant first; gesture-less paths (the
// auto-probe) just try the injection and degrade to a "no access" state instead of a raw error.

/** `https://host[:port]/*` match pattern for a base URL, or null when unparseable. */
export function originPattern(baseUrl: string): string | null {
  try {
    return `${new URL(baseUrl).origin}/*`;
  } catch {
    return null;
  }
}

/**
 * Request durable host access for the base URL's origin. MUST be the first await on a user
 * gesture (`chrome.permissions.request` needs the gesture). Resolves true without prompting when
 * the origin is already granted (static host_permissions or a prior optional grant).
 */
export async function requestHostAccess(baseUrl: string): Promise<boolean> {
  const pattern = originPattern(baseUrl);
  if (!pattern) return false;
  try {
    return await chrome.permissions.request({ origins: [pattern] });
  } catch {
    return false;
  }
}
