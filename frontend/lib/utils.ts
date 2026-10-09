export { cn } from "cn"

/** Convertit le `detail` d'une erreur API FastAPI en message
 *  affichable : chaîne pour les erreurs métier, tableau d'objets
 *  Pydantic pour les 422 — jamais un objet brut passé à React. */
export function apiErrorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) => {
        if (!entry || typeof entry !== "object") return null;
        const { msg, loc } = entry as { msg?: unknown; loc?: unknown };
        if (typeof msg !== "string") return null;
        const path = Array.isArray(loc)
          ? loc
              .filter(
                (l): l is string | number =>
                  typeof l === "string" || typeof l === "number",
              )
              .join(".")
              .replace(/^body\.?/, "")
          : "";
        return path ? `${path} : ${msg}` : msg;
      })
      .filter((m): m is string => m !== null);
    if (messages.length > 0) return messages.join(" — ");
  }
  return fallback;
}
